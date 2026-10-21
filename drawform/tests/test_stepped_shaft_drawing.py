"""Independent OCP8 solids and source-bound shoulder dimensions; no CAD mocks."""
from __future__ import annotations
from dataclasses import replace
import io
import json
import math
from pathlib import Path

import ezdxf
import pytest
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse
from OCP.BRepClass import BRepClass_FaceClassifier
from OCP.BRepClass3d import BRepClass3d_SolidClassifier
from OCP.BRepGProp import BRepGProp
from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
from OCP.GProp import GProp_GProps
from OCP.GeomAbs import GeomAbs_Cylinder, GeomAbs_Plane
from OCP.IFSelect import IFSelect_ReturnStatus
from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer
from OCP.TopAbs import TopAbs_FACE, TopAbs_IN, TopAbs_ON, TopAbs_OUT, TopAbs_SHELL
from OCP.ShapeAnalysis import ShapeAnalysis_Shell
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

from drawform.delivery import gate_for
from drawform.dimensioning.constraints import Chain, Sized
from drawform.dimensioning.place import place_scheme
from drawform.dimensioning.scheme import derive_scheme
from drawform.domain.curves import to_view, view_basis
from drawform.geometry.loader import import_step
from drawform.geometry.features import Bore, Shaft, extract_features
from drawform.geometry.projection import FRONT, LEFT, TOP, project_view
from drawform.pipeline import render_part

CASES = {'main': (40.,30.,20.,40.), 'reverse': (20.,40.,40.,30.),
         'equal': (40.,30.,20.,30.), 'collision': (30.,30.,20.,40.)}


def write_solid_stepped_shaft(path, d1=40., l1=30., d2=20., l2=40.):
    first = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(0,0,0),gp_Dir(0,0,1)),d1/2,l1).Shape()
    second = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(0,0,l1),gp_Dir(0,0,1)),d2/2,l2).Shape()
    fuse = BRepAlgoAPI_Fuse(first,second)
    assert fuse.IsDone(), 'independent fixture boolean must succeed'
    writer = STEPControl_Writer()
    assert writer.Transfer(fuse.Shape(),STEPControl_AsIs)==IFSelect_ReturnStatus.IFSelect_RetDone
    assert writer.Write(str(path))==IFSelect_ReturnStatus.IFSelect_RetDone
    return Path(path)


def source_facts(model):
    """Independent raw surfaces, not the production stepped-shaft recognizer."""
    cylinders=[];planes=[]
    explorer=TopExp_Explorer(model.shape,TopAbs_FACE)
    while explorer.More():
        face=TopoDS.Face(explorer.Current());explorer.Next()
        adaptor=BRepAdaptor_Surface(face)
        props=GProp_GProps();BRepGProp.SurfaceProperties_s(face,props)
        if adaptor.GetType()==GeomAbs_Cylinder:
            cylinder=adaptor.Cylinder();axis=cylinder.Axis();direction=axis.Direction();location=axis.Location()
            assert (direction.X(),direction.Y(),abs(direction.Z()))==pytest.approx((0,0,1),abs=1e-6)
            assert (location.X(),location.Y())==pytest.approx((0,0),abs=1e-6)
            u=(adaptor.FirstUParameter()+adaptor.LastUParameter())/2
            span=sorted(adaptor.Value(u,v).Z() for v in (adaptor.FirstVParameter(),adaptor.LastVParameter()))
            cylinders.append((float(span[0]),float(span[1]),float(2*cylinder.Radius())))
        elif adaptor.GetType()==GeomAbs_Plane:
            normal=adaptor.Plane().Axis().Direction()
            assert (normal.X(),normal.Y(),abs(normal.Z()))==pytest.approx((0,0,1),abs=1e-6)
            planes.append((float(adaptor.Plane().Location().Z()),float(props.Mass()),face))
        else:
            raise AssertionError('simple solid shaft contains an unexpected surface type')
    return sorted(cylinders),planes


def assert_source_geometry(model, parameters):
    d1,l1,d2,l2=parameters;total=l1+l2;diameter=max(d1,d2)
    assert model.is_valid and model.solid_count==1 and not model.excluded_geometry
    assert model.bbox==pytest.approx((diameter,diameter,total),abs=1e-5)
    assert model.origin==pytest.approx((-diameter/2,-diameter/2,0),abs=1e-5)
    shells=TopExp_Explorer(model.shape,TopAbs_SHELL);shell_count=0
    while shells.More():
        shell=shells.Current();shells.Next();shell_count+=1
        closure=ShapeAnalysis_Shell();closure.LoadShells(shell);closure.CheckOrientedShells(shell,True)
        assert not closure.HasFreeEdges(), 'independent saved solid has an open shell'
    assert shell_count
    state=lambda p:BRepClass3d_SolidClassifier(model.shape,gp_Pnt(*p),1e-7).State()
    for diameter,low,high in ((d1,0,l1),(d2,l1,total)):
        z=(low+high)/2
        assert state((0,0,z))==TopAbs_IN
        assert state((diameter*.6,0,z))==TopAbs_OUT
    props=GProp_GProps();BRepGProp.VolumeProperties_s(model.shape,props)
    assert props.Mass()==pytest.approx(math.pi*(d1*d1*l1+d2*d2*l2)/4,rel=1e-9)
    features=extract_features(model.shape,model.bbox)
    assert not [f for f in features if isinstance(f,Bore)]
    assert len([f for f in features if isinstance(f,Shaft)])==2
    cylinders,planes=source_facts(model)
    assert len(cylinders)==2 and len(planes)==3
    for actual,expected in zip(cylinders,((0,l1,d1),(l1,total,d2))):
        assert actual==pytest.approx(expected,abs=1e-6)
    expected_planes=((0,math.pi*d1*d1/4),(l1,math.pi*abs(d1*d1-d2*d2)/4),(total,math.pi*d2*d2/4))
    for z,area in expected_planes:
        matching=[p for p in planes if abs(p[0]-z)<1e-6]
        assert len(matching)==1 and matching[0][1]==pytest.approx(area,rel=1e-9)
    return planes


def assert_real_shoulder_reference(model,dimension,parameters):
    _,l1,_,l2=parameters
    _,planes=source_facts(model)
    points=dimension.reference_points_3d
    assert len(points)==2
    assert any(abs(p[2]-l1)<1e-6 for p in points)
    assert abs(points[1][2]-points[0][2])==pytest.approx(dimension.value,abs=1e-6)
    for point in points:
        assert math.hypot(point[0],point[1])>1e-6, 'axis center cannot witness the annular shoulder'
        matching=[face for z,_,face in planes if abs(z-point[2])<1e-6]
        assert len(matching)==1
        assert BRepClass_FaceClassifier(matching[0],gp_Pnt(*point),1e-7).State() in (TopAbs_IN,TopAbs_ON)
    assert set(round(p[2],6) for p in points) in ({0.,l1},{l1,l1+l2})


@pytest.fixture(scope='module')
def shaft_contexts(tmp_path_factory):
    root=tmp_path_factory.mktemp('independent-solid-shafts');contexts={}
    for name,parameters in CASES.items():
        path=write_solid_stepped_shaft(root/(name+'.step'),*parameters)
        model=import_step(path);assert_source_geometry(model,parameters)
        features=extract_features(model.shape,model.bbox)
        scheme=derive_scheme(model,features)
        projections={view.name:project_view(model.shape,view) for view in (FRONT,TOP,LEFT)}
        dimensions=place_scheme(scheme,projections)
        contexts[name]={'step':path,'model':model,'scheme':scheme,'projections':projections,
                        'dimensions':dimensions,'parameters':parameters}
    return contexts


def test_true_solid_shaft_step_reaches_a_real_shoulder_dimension(tmp_path,shaft_contexts):
    context=shaft_contexts['main'];output=tmp_path/'drawing';output.mkdir()
    part=render_part(context['step'],output,stem='shaft')
    assert part.verdict.allowed
    shoulders=[d for d in part.dimensions if d.source and d.source[1]=='gap' and d.axis=='z']
    assert len(shoulders)==1, 'real two-stage solid shaft is missing its axial shoulder dimension'
    assert shoulders[0].value==pytest.approx(30,abs=1e-6)
    assert_real_shoulder_reference(context['model'],shoulders[0],context['parameters'])
    assert any(d.source==('outline','height') and d.value==pytest.approx(70) for d in part.dimensions)
    document=json.loads((output/'shaft.json').read_text())
    dims=list(ezdxf.readfile(output/'shaft.dxf').modelspace().query('DIMENSION'))
    assert len(dims)==len(document['dimensions'])
    for native,record in zip(dims,document['dimensions']):
        assert native.get_measurement()==pytest.approx(record['value'],abs=1e-5)
    assert (output/'shaft.pdf').read_bytes().startswith(b'%PDF')
    assert (output/'shaft.svg').stat().st_size>0


@pytest.mark.parametrize('name',('main','reverse','equal','collision'))
def test_bound_shoulder_scheme_has_one_independent_real_span(shaft_contexts,name):
    c=shaft_contexts[name];scheme=c['scheme'];dims=c['dimensions']
    supports={row[0]:(row[1],row[2]) for row in scheme.shoulder_supports}
    assert len(supports)==2
    shoulders=[d for d in dims if d.source and d.source[1]=='gap' and d.axis=='z']
    assert len(shoulders)==1
    dimension=shoulders[0]
    assert dimension.value==pytest.approx(min(c['parameters'][1],c['parameters'][3]))
    assert tuple(dimension.reference_points_3d)==supports[dimension.source[0]]
    assert_real_shoulder_reference(c['model'],dimension,c['parameters'])
    basis=view_basis(c['projections'][dimension.view].direction)
    assert to_view(dimension.reference_points_3d[0],*basis)==pytest.approx(dimension.p1,abs=1e-6)
    assert to_view(dimension.reference_points_3d[1],*basis)==pytest.approx(dimension.p2,abs=1e-6)
    chains=[x for x in scheme.plan.constraints if isinstance(x,Chain) and x.name=='chain:z']
    assert len(chains)==1 and len(chains[0].members)==2 and len(chains[0].reference)==1
    assert (dimension.source[0],'gap') not in chains[0].reference
    assert gate_for(c['model'],scheme,dims,projections=c['projections']).allowed


def test_same_bbox_volume_cannot_hide_swapped_segment_order(shaft_contexts):
    main=shaft_contexts['main']['model'];swapped=shaft_contexts['reverse']['model']
    assert main.bbox==pytest.approx(swapped.bbox,abs=1e-6)
    first=GProp_GProps();second=GProp_GProps()
    BRepGProp.VolumeProperties_s(main.shape,first);BRepGProp.VolumeProperties_s(swapped.shape,second)
    assert first.Mass()==pytest.approx(second.Mass(),rel=1e-9)
    with pytest.raises(AssertionError):assert_source_geometry(swapped,CASES['main'])


def test_gate_rejects_removed_gap_even_when_scheme_omits_unknowns(shaft_contexts):
    c=shaft_contexts['main'];scheme=c['scheme']
    constraints=tuple(x for x in scheme.plan.constraints if not isinstance(x,Chain)
                      and not (isinstance(x,Sized) and x.dof=='gap'))
    reduced=replace(scheme,plan=replace(scheme.plan,constraints=constraints),
                    unknowns=tuple(x for x in scheme.unknowns if x[1]!='gap'),shoulder_supports=())
    dims=tuple(d for d in c['dimensions'] if not (d.source and d.source[1]=='gap'))
    verdict=gate_for(c['model'],reduced,dims,projections=c['projections'])
    assert not verdict.allowed and 'stepped_shaft_dimensions' in verdict.failed


def test_gate_rejects_axis_center_even_when_scheme_and_projection_agree(shaft_contexts):
    c=shaft_contexts['main'];dimension=next(d for d in c['dimensions'] if d.source and d.source[1]=='gap')
    fake=tuple((0.,0.,p[2]) for p in dimension.reference_points_3d)
    supports=tuple((identity,*fake) if identity==dimension.source[0] else row
                   for row in c['scheme'].shoulder_supports for identity in (row[0],))
    scheme=replace(c['scheme'],shoulder_supports=supports)
    basis=view_basis(c['projections'][dimension.view].direction)
    replacement=replace(dimension,reference_points_3d=fake,p1=to_view(fake[0],*basis),p2=to_view(fake[1],*basis))
    dims=tuple(replacement if d is dimension else d for d in c['dimensions'])
    verdict=gate_for(c['model'],scheme,dims,projections=c['projections'])
    assert not verdict.allowed and 'stepped_shaft_dimensions' in verdict.failed


def test_diameter_30_cannot_satisfy_missing_axial_30(shaft_contexts):
    c=shaft_contexts['collision']
    retained=tuple(d for d in c['dimensions'] if not (d.source and d.source[1]=='gap'))
    assert any(d.kind=='diameter' and d.value==pytest.approx(30) for d in retained)
    verdict=gate_for(c['model'],c['scheme'],retained,projections=c['projections'])
    assert not verdict.allowed and 'stepped_shaft_dimensions' in verdict.failed


def test_solid_shaft_has_real_centerlines_in_all_three_views(shaft_contexts):
    from drawform.geometry.centerlines import centerlines_for
    c=shaft_contexts['main']
    features=extract_features(c['model'].shape,c['model'].bbox)
    for name,projection in c['projections'].items():
        lines=centerlines_for(projection,features)
        assert lines, f'solid stepped shaft has no {name} centerline'
        if name=='top':
            assert any(abs(line.start[0])<1e-6 and abs(line.end[0])<1e-6 for line in lines)
            assert any(abs(line.start[1])<1e-6 and abs(line.end[1])<1e-6 for line in lines)
        else:
            basis=view_basis(projection.direction)
            start=to_view((0,0,0),*basis);end=to_view((0,0,70),*basis)
            axis=0 if abs(end[0]-start[0])>1 else 1
            assert any(min(line.start[axis],line.end[axis])<=min(start[axis],end[axis])
                       and max(line.start[axis],line.end[axis])>=max(start[axis],end[axis]) for line in lines)


@pytest.mark.parametrize('remove',('diameters','total'))
def test_source_gate_rejects_removed_required_size_without_unknowns(shaft_contexts,remove):
    c=shaft_contexts['main'];scheme=c['scheme']
    omitted={(x.feature,x.dof) for x in scheme.plan.constraints if isinstance(x,Sized)
             and (x.dof=='diameter' if remove=='diameters' else (x.feature,x.dof)==('outline','height'))}
    assert omitted
    constraints=tuple(x for x in scheme.plan.constraints if not isinstance(x,Sized) or (x.feature,x.dof) not in omitted)
    reduced=replace(scheme,plan=replace(scheme.plan,constraints=constraints),
                    unknowns=tuple(x for x in scheme.unknowns if x not in omitted))
    dims=tuple(d for d in c['dimensions'] if d.source not in omitted)
    verdict=gate_for(c['model'],reduced,dims,projections=c['projections'])
    assert not verdict.allowed and 'stepped_shaft_dimensions' in verdict.failed


def test_source_gate_rejects_duplicate_members_in_axial_chain(shaft_contexts):
    c=shaft_contexts['main'];scheme=c['scheme']
    chain=next(x for x in scheme.plan.constraints if isinstance(x,Chain) and x.name=='chain:z')
    assert len(chain.members)==2
    duplicate=replace(chain,members=(chain.members[0],chain.members[1],chain.members[0]))
    constraints=tuple(duplicate if x is chain else x for x in scheme.plan.constraints)
    changed=replace(scheme,plan=replace(scheme.plan,constraints=constraints))
    verdict=gate_for(c['model'],changed,c['dimensions'],projections=c['projections'])
    assert not verdict.allowed and 'stepped_shaft_dimensions' in verdict.failed, \
        'a duplicated axial member changes the physical dimension equation even when its set is unchanged'


def test_submillimeter_stage_keeps_two_distinct_scheme_sources(tmp_path):
    step=write_solid_stepped_shaft(tmp_path/'tiny-first-stage.step',40.,.0004,20.,40.)
    model=import_step(step)
    assert_source_geometry(model,(40.,.0004,20.,40.))
    features=extract_features(model.shape,model.bbox)
    scheme=derive_scheme(model,features)
    gap_unknowns=[key for key in scheme.unknowns if key[1]=='gap']
    assert len(gap_unknowns)==2 and len(set(gap_unknowns))==2
    identities=[row[0] for row in scheme.shoulder_supports]
    assert len(identities)==2 and len(set(identities))==2
    assert {key[0] for key in gap_unknowns}==set(identities)
    constraints=[x for x in scheme.plan.constraints if isinstance(x,Sized) and x.dof=='gap']
    assert len(constraints)==1 and constraints[0].value==pytest.approx(.0004,abs=1e-7)
    chains=[x for x in scheme.plan.constraints if isinstance(x,Chain) and x.name=='chain:z']
    assert len(chains)==1 and len(chains[0].members)==2 and len(set(chains[0].members))==2

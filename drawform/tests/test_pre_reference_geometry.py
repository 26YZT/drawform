"""斜孔参考必须绑定真实几何，等价表示不能改变定位结果。"""
from dataclasses import replace
import hashlib
import json
import math

import ezdxf
import pytest
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt, gp_Vec

from drawform import pipeline
from drawform.delivery import DeliveryBlocked
from drawform.dimensioning.scheme import derive_scheme
from drawform.geometry.features import Bore, extract_features
from drawform.geometry.loader import import_step
from drawform.geometry import references as ref
from tests.conftest import _write_step


@pytest.fixture(scope='module')
def reference_model(tmp_path_factory):
    folder=tmp_path_factory.mktemp('reference-source')
    shape=BRepPrimAPI_MakeBox(80,50,20).Shape()
    tool=BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(-20,5,10),gp_Dir(.9,.3,0)),3,130).Shape()
    step=_write_step(BRepAlgoAPI_Cut(shape,tool).Shape(),folder/'oblique.step')
    model=import_step(step);features=extract_features(model.shape,model.bbox)
    snapshot=ref.snapshot_for(model,features,hashlib.sha256(step.read_bytes()).hexdigest())
    assert snapshot and snapshot['questions']
    return step,model,features,snapshot


def selection(snapshot, unknown=False):
    return {'basis_sha':snapshot['basis_sha'],'answers':[
        {'question_id':q['id'],'option_id':None if unknown else q['options'][0]['id']}
        for q in snapshot['questions']]}


@pytest.mark.parametrize('reverse',[False,True])
def test_axis_point_translation_and_direction_sign_preserve_reference_and_values(reference_model,reverse):
    _step,model,features,snapshot=reference_model
    shifted=tuple(replace(f,axis_point=tuple(p+17*d for p,d in zip(f.axis_point,f.axis_direction)),
                          axis_direction=tuple(-d for d in f.axis_direction) if reverse else f.axis_direction)
                  if isinstance(f,Bore) else f for f in features)
    other=ref.snapshot_for(model,shifted,snapshot['source_sha256'])
    assert other['basis_sha']==snapshot['basis_sha']
    a,b=ref.resolve(snapshot,selection(snapshot)),ref.resolve(other,selection(snapshot))
    assert [f.bore_id for f in a]==[f.bore_id for f in b]
    assert [f.point for f in a]==pytest.approx([f.point for f in b],abs=1e-10)
    x=derive_scheme(model,features,reference_facts=a)
    y=derive_scheme(model,shifted,reference_facts=b)
    assert [(c.feature,c.dof) for c in x.plan.constraints]==[(c.feature,c.dof) for c in y.plan.constraints]
    assert [c.value for c in x.plan.constraints]==pytest.approx([c.value for c in y.plan.constraints],abs=1e-10)


def test_topology_iteration_order_does_not_change_closed_candidate_ids(reference_model,monkeypatch):
    _step,model,features,snapshot=reference_model
    original=ref.plane_candidates
    monkeypatch.setattr(ref,'plane_candidates',lambda shape:dict(reversed(list(original(shape).items()))))
    other=ref.snapshot_for(model,tuple(reversed(features)),snapshot['source_sha256'])
    assert other['basis_sha']==snapshot['basis_sha']


def test_plane_parameter_origin_moves_within_same_plane_without_changing_identity(reference_model,monkeypatch):
    _step,model,features,snapshot=reference_model
    original=ref.BRepAdaptor_Surface
    class Shifted:
        def __init__(self,face):self.adaptor=original(face)
        def GetType(self):return self.adaptor.GetType()
        def Plane(self):
            plane=self.adaptor.Plane();direction=plane.Position().XDirection()
            plane.Translate(gp_Vec(direction.X()*123,direction.Y()*123,direction.Z()*123))
            return plane
    monkeypatch.setattr(ref,'BRepAdaptor_Surface',Shifted)
    other=ref.snapshot_for(model,features,snapshot['source_sha256'])
    assert other['basis_sha']==snapshot['basis_sha']


def test_candidates_report_extension_without_calling_it_a_physical_mouth(reference_model):
    _step,_model,_features,snapshot=reference_model
    options=[o for q in snapshot['questions'] for o in q['options']]
    assert options and any(not o['on_patch'] for o in options)
    for o in options:
        plane=snapshot['planes'][o['plane_id']]
        assert math.fsum(a*b for a,b in zip(plane['normal'],o['point']))==pytest.approx(plane['constant'],abs=1e-8)
        if not o['on_patch']:assert '延拓' in o['label']


@pytest.mark.parametrize('kind',['missing','number','vector','foreign','duplicate','stale','note_number'])
def test_only_complete_closed_ids_are_accepted(reference_model,kind):
    snapshot=reference_model[3];body=selection(snapshot)
    if kind=='missing':body['answers']=[]
    if kind=='number':body['answers'][0]['value']=12.3
    if kind=='vector':body['answers'][0]['axis']=[1,2,3]
    if kind=='foreign':body['answers'][0]['option_id']='some-other-file'
    if kind=='duplicate':body['answers'].append(dict(body['answers'][0]))
    if kind=='stale':body['basis_sha']='0'*64
    if kind=='note_number':body['answers'][0]['free_note']=123
    with pytest.raises(ref.ReferenceInputError):ref.validate_submission(snapshot,body)


def test_same_geometry_in_a_different_source_cannot_reuse_old_selection(reference_model):
    _step,model,features,snapshot=reference_model
    other=ref.snapshot_for(model,features,'a'*64)
    with pytest.raises(ref.ReferenceInputError) as error:ref.resolve(other,selection(snapshot))
    assert error.value.reason=='stale_reference'


def test_nearly_parallel_or_coordinate_degenerate_plane_is_not_offered(reference_model):
    bore=next(f for f in reference_model[2] if isinstance(f,Bore));_id,geometry=ref.bore_geometry(bore)
    dx,dy,_=geometry['direction']
    assert ref._intersection(bore,geometry,{'normal':(-dy,dx,0),'constant':0},(1,2)) is None
    assert ref._intersection(bore,geometry,{'normal':(1e-10,1,0),'constant':20},(1,2)) is None
    assert ref._intersection(bore,geometry,{'normal':(1,0,0),'constant':10000},(1,2)) is None


def test_reference_is_required_before_any_deliverable(reference_model,tmp_path):
    with pytest.raises(ref.ReferenceRequired) as error:pipeline.render_part(reference_model[0],tmp_path)
    assert error.value.failed==('reference_required',)
    assert error.value.snapshot['questions']
    assert not list(tmp_path.iterdir())


def test_unknown_reference_keeps_every_deliverable_absent(reference_model,tmp_path):
    with pytest.raises(DeliveryBlocked) as error:
        pipeline.render_part(reference_model[0],tmp_path,reference_in=selection(reference_model[3],True))
    assert error.value.failed==('reference_unconfirmed',)
    assert not list(tmp_path.iterdir())


def test_confirmed_intersection_drives_plan_placement_and_native_measurements(reference_model,tmp_path):
    step,model,_features,snapshot=reference_model
    part=pipeline.render_part(step,tmp_path,reference_in=selection(snapshot),with_questions=True)
    files=dict(part.files);data=json.loads(files['json'].read_text())
    assert data['reference_confirmations']
    for fact in data['reference_confirmations']:
        for axis in fact['axes']:
            item=next(c for c in data['constraints'] if c['feature']==fact['feature_id'] and c['dof']==axis)
            index='xyz'.index(axis)
            assert item['value']==pytest.approx(fact['point'][index]-model.origin[index],abs=1e-6)
    doc=ezdxf.readfile(files['dxf']);assert not doc.audit().errors
    native=list(doc.modelspace().query('DIMENSION'));assert native
    assert len(native)==len(data['dimensions'])
    for entity,dim in zip(native,data['dimensions']):
        assert entity.get_measurement()==pytest.approx(dim['value'],abs=1e-6)


def test_incomplete_two_axis_position_is_caught_even_if_removed_from_plan_and_unknowns(reference_model,tmp_path,monkeypatch):
    original=pipeline.derive_scheme
    def drop(*args,**kwargs):
        scheme=original(*args,**kwargs);source=(scheme.reference_facts[0].feature_id,scheme.reference_facts[0].axes[0])
        return replace(scheme,plan=replace(scheme.plan,constraints=tuple(c for c in scheme.plan.constraints
                       if (getattr(c,'feature',None),getattr(c,'dof',None))!=source)),
                       unknowns=tuple(u for u in scheme.unknowns if u!=source))
    monkeypatch.setattr(pipeline,'derive_scheme',drop)
    with pytest.raises(DeliveryBlocked) as error:
        pipeline.render_part(reference_model[0],tmp_path,reference_in=selection(reference_model[3]))
    assert error.value.failed==('incomplete_reference_position',)
    assert not list(tmp_path.iterdir())


def test_source_changed_during_geometry_work_never_binds_old_geometry_to_new_bytes(reference_model,tmp_path,monkeypatch):
    step=tmp_path/'input.step';step.write_bytes(reference_model[0].read_bytes());out=tmp_path/'out';out.mkdir()
    original=pipeline.extract_features
    def change(*args):
        result=original(*args);step.write_bytes(step.read_bytes()+b'\n');return result
    monkeypatch.setattr(pipeline,'extract_features',change)
    with pytest.raises(DeliveryBlocked) as error:pipeline.render_part(step,out)
    assert error.value.failed==('stale_reference',)
    assert not list(out.iterdir())


def test_same_radius_and_offset_crossing_axes_are_not_silently_merged(tmp_path):
    shape=BRepPrimAPI_MakeBox(gp_Pnt(-10,-10,-10),20,20,20).Shape()
    for start,direction in [((-20,0,0),(1,0,0)),((0,-20,0),(0,1,0))]:
        tool=BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(*start),gp_Dir(*direction)),2,40).Shape()
        shape=BRepAlgoAPI_Cut(shape,tool).Shape()
    step=_write_step(shape,tmp_path/'cross.step');model=import_step(step)
    bores=[f for f in extract_features(model.shape,model.bbox) if isinstance(f,Bore)]
    assert {(1,0,0),(0,1,0)}<={tuple(round(v) for v in b.canonical_axis()) for b in bores}


def test_oblique_pcd_is_explicitly_refused_even_when_nearly_normal_to_a_standard_view(tmp_path):
    shape=BRepPrimAPI_MakeBox(80,60,60).Shape()
    for i in range(3):
        y,z=30+12*math.cos(i*2*math.pi/3),30+12*math.sin(i*2*math.pi/3)
        tool=BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(-10,y-.01,z),gp_Dir(1,.001,0)),2,100).Shape()
        shape=BRepAlgoAPI_Cut(shape,tool).Shape()
    step=_write_step(shape,tmp_path/'oblique-pcd.step');out=tmp_path/'out';out.mkdir()
    with pytest.raises(ref.ReferenceRequired) as waiting:pipeline.render_part(step,out)
    with pytest.raises(DeliveryBlocked) as error:
        pipeline.render_part(step,out,reference_in=selection(waiting.value.snapshot))
    assert error.value.failed==('unsupported_oblique_pcd',)
    assert not list(out.iterdir())


def test_diagonal_axis_with_no_complete_two_direction_basis_is_not_silently_underconstrained(tmp_path):
    shape=BRepPrimAPI_MakeBox(80,80,80).Shape()
    tool=BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(-10,-10,-10),gp_Dir(1,1,1)),3,180).Shape()
    step=_write_step(BRepAlgoAPI_Cut(shape,tool).Shape(),tmp_path/'diagonal.step');out=tmp_path/'out';out.mkdir()
    with pytest.raises(DeliveryBlocked) as error:pipeline.render_part(step,out)
    assert error.value.failed==('unsupported_reference_position',)
    assert not list(out.iterdir())


def test_over_limit_real_plane_candidates_are_refused_without_truncation(reference_model,tmp_path,monkeypatch):
    assert len(reference_model[3]['questions'][0]['options'])>1
    monkeypatch.setattr(ref,'MAX_OPTIONS',1)
    with pytest.raises(DeliveryBlocked) as error:pipeline.render_part(reference_model[0],tmp_path)
    assert error.value.failed==('reference_candidates_overflow',)
    assert not list(tmp_path.iterdir())


def test_colliding_plane_fingerprints_refuse_instead_of_selecting_another_face(reference_model,tmp_path,monkeypatch):
    monkeypatch.setattr(ref,'_hash',lambda value:'c'*64)
    with pytest.raises(DeliveryBlocked) as error:pipeline.render_part(reference_model[0],tmp_path)
    assert error.value.failed==('ambiguous_reference',)
    assert not list(tmp_path.iterdir())

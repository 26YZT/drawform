"""Re-open simple model STEP in a fresh DrawForm OCP8 process.

This checker deliberately uses DrawForm/native OCP; it never imports cadgen,
build123d or the isolated worker implementation.
"""
from __future__ import annotations
import hashlib
import json
import math
from pathlib import Path
import sys
import tempfile

import OCP
from OCP.BRepClass3d import BRepClass3d_SolidClassifier
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.ShapeAnalysis import ShapeAnalysis_Shell
from OCP.TopAbs import TopAbs_IN, TopAbs_OUT, TopAbs_SHELL
from OCP.TopExp import TopExp_Explorer
from OCP.gp import gp_Pnt

from drawform.geometry.loader import import_step
from drawform.geometry.features import Bore, Shaft, extract_features
from drawform.workbench.simple_spec import validate_spec


def inspect_stepped_shaft(path, spec, model, source_sha):
    """Independent raw OCP8 face checks; no worker/shaft-recognizer imports."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepClass import BRepClass_FaceClassifier
    from OCP.GeomAbs import GeomAbs_Cylinder, GeomAbs_Plane
    from OCP.TopAbs import TopAbs_FACE, TopAbs_ON, TopAbs_REVERSED
    from OCP.TopoDS import TopoDS
    from OCP.gp import gp_Vec

    p=spec['parameters']
    diameters=(p['diameter_1_mm'],p['diameter_2_mm'])
    lengths=(p['length_1_mm'],p['length_2_mm'])
    shoulder_z=lengths[0];total=sum(lengths);diameter=max(diameters)
    near=lambda a,b:math.isfinite(a) and math.isfinite(b) and math.isclose(a,b,rel_tol=1e-9,abs_tol=1e-5)
    cylinders=[];planes=[];unexpected=[]
    explorer=TopExp_Explorer(model.shape,TopAbs_FACE)
    while explorer.More():
        face=TopoDS.Face(explorer.Current());explorer.Next()
        adaptor=BRepAdaptor_Surface(face);kind=adaptor.GetType()
        if kind==GeomAbs_Cylinder:
            cylinder=adaptor.Cylinder();axis=cylinder.Axis();location=axis.Location();direction=axis.Direction()
            ax=(float(direction.X()),float(direction.Y()),float(direction.Z()))
            loc=(float(location.X()),float(location.Y()),float(location.Z()))
            along=sum(a*b for a,b in zip(ax,loc));offset=[a-along*b for a,b in zip(loc,ax)]
            u1,u2=float(adaptor.FirstUParameter()),float(adaptor.LastUParameter())
            v1,v2=float(adaptor.FirstVParameter()),float(adaptor.LastVParameter())
            mid_u=(u1+u2)/2
            lower,upper=sorted(float(adaptor.Value(mid_u,v).Z()) for v in (v1,v2))
            point=gp_Pnt();du=gp_Vec();dv=gp_Vec()
            adaptor.D1(mid_u,(v1+v2)/2,point,du,dv)
            normal=du.Crossed(dv)
            if face.Orientation()==TopAbs_REVERSED:normal.Reverse()
            point_tuple=(float(point.X()),float(point.Y()),float(point.Z()))
            projection=sum((a-b)*c for a,b,c in zip(point_tuple,loc,ax))
            radial=gp_Vec(*(a-b-projection*c for a,b,c in zip(point_tuple,loc,ax)))
            outward=normal.Magnitude()>0 and radial.Magnitude()>0 and normal.Dot(radial)>0
            cylinders.append({'diameter':float(cylinder.Radius()*2),'low_z':lower,'high_z':upper,
                              'axis':ax,'offset':offset,'turn':abs(u2-u1),'outward':bool(outward)})
        elif kind==GeomAbs_Plane:
            properties=GProp_GProps();BRepGProp.SurfaceProperties_s(face,properties)
            plane=adaptor.Plane();normal=plane.Axis().Direction()
            planes.append({'face':face,'z':float(plane.Location().Z()),'area':float(properties.Mass()),
                           'normal':(float(normal.X()),float(normal.Y()),float(normal.Z()))})
        else:unexpected.append(str(kind))
    checks=[]
    def record(identity,good,observed=None):
        row={'id':identity,'passed':bool(good)}
        if observed is not None:row['actual']=observed
        checks.append(row)
    record('df.single_valid_solid',model.is_valid and model.solid_count==1 and not model.excluded_geometry,
           {'solid_count':model.solid_count,'is_valid':model.is_valid,'excluded_geometry':model.excluded_geometry})
    shell_flags=[];shells=TopExp_Explorer(model.shape,TopAbs_SHELL)
    while shells.More():
        shell=shells.Current();shells.Next();analysis=ShapeAnalysis_Shell()
        analysis.LoadShells(shell);analysis.CheckOrientedShells(shell,True);shell_flags.append(bool(analysis.HasFreeEdges()))
    record('df.closed_shells',bool(shell_flags) and not any(shell_flags),shell_flags)
    record('df.extents',all(near(a,b) for a,b in zip(model.bbox,(diameter,diameter,total))),model.bbox)
    record('df.origin',all(near(a,b) for a,b in zip(model.origin,(-diameter/2,-diameter/2,0))),model.origin)
    volume=GProp_GProps();BRepGProp.VolumeProperties_s(model.shape,volume)
    expected_volume=math.pi/4*sum(d*d*l for d,l in zip(diameters,lengths))
    record('df.volume',volume.Mass()>0 and near(float(volume.Mass()),expected_volume),
           {'actual_mm3':float(volume.Mass()),'expected_mm3':expected_volume})
    record('df.shaft_surface_types',not unexpected and len(cylinders)==2 and len(planes)==3,
           {'cylinders':len(cylinders),'planes':len(planes),'unexpected':unexpected})
    record('df.shaft_no_inner_cylinder',len(cylinders)==2 and all(c['outward'] for c in cylinders),cylinders)
    record('df.shaft_axes',len(cylinders)==2 and all(near(c['axis'][0],0) and near(c['axis'][1],0)
           and near(abs(c['axis'][2]),1) and all(near(value,0) for value in c['offset']) for c in cylinders),cylinders)
    record('df.shaft_complete_finite_cylinders',len(cylinders)==2 and all(near(c['turn'],2*math.pi)
           and math.isfinite(c['low_z']) and math.isfinite(c['high_z']) and c['high_z']>c['low_z'] for c in cylinders),cylinders)
    expected_intervals=[(diameters[0],0.,shoulder_z),(diameters[1],shoulder_z,total)]
    interval_matches=[]
    for d,start,end in expected_intervals:
        interval_matches.append([i for i,c in enumerate(cylinders) if near(c['diameter'],d)
                                 and near(c['low_z'],start) and near(c['high_z'],end)])
    correct_segments=len(cylinders)==2 and all(len(row)==1 for row in interval_matches) \
        and len({row[0] for row in interval_matches if len(row)==1})==2
    record('df.shaft_segment_z_intervals',correct_segments,{'expected':expected_intervals,'actual':cylinders})
    radii=[d/2 for d in diameters]
    expected_faces=[(0.,math.pi*radii[0]**2),(shoulder_z,math.pi*abs(radii[0]**2-radii[1]**2)),(total,math.pi*radii[1]**2)]
    face_matches=[[i for i,plane in enumerate(planes) if near(plane['z'],z) and near(plane['area'],area)] for z,area in expected_faces]
    planar_ok=len(planes)==3 and all(len(row)==1 for row in face_matches) \
        and len({row[0] for row in face_matches if len(row)==1})==3
    record('df.shaft_three_axial_planes',planar_ok and all(near(plane['normal'][0],0) and near(plane['normal'][1],0)
           and near(abs(plane['normal'][2]),1) for plane in planes),
           [{k:v for k,v in plane.items() if k!='face'} for plane in planes])
    shoulders=[plane for plane in planes if near(plane['z'],shoulder_z) and near(plane['area'],expected_faces[1][1])]
    witness=(0,sum(radii)/2,shoulder_z);annular=False
    if len(shoulders)==1:
        face=shoulders[0]['face']
        annular=BRepClass_FaceClassifier(face,gp_Pnt(*witness),1e-7).State() in (TopAbs_IN,TopAbs_ON) \
            and BRepClass_FaceClassifier(face,gp_Pnt(0,0,shoulder_z),1e-7).State()==TopAbs_OUT
    record('df.shaft_shoulder_plane',annular,{'z':shoulder_z,'area_mm2':expected_faces[1][1],'witness':witness})
    classify=lambda point:BRepClass3d_SolidClassifier(model.shape,gp_Pnt(*point),1e-7).State()
    material=[];outside=[]
    for radius,start,length in ((radii[0],0.,lengths[0]),(radii[1],shoulder_z,lengths[1])):
        for fraction in (1/3,2/3):
            z=start+length*fraction
            material.extend([(0,0,z),(radius*.6,0,z),(0,-radius*.6,z)])
            outside.extend([(radius*1.3,0,z),(0,-radius*1.3,z)])
    dz=min(lengths)/5;ring_radius=sum(radii)/2
    before=(ring_radius,0,shoulder_z-dz);after=(ring_radius,0,shoulder_z+dz)
    wanted=(TopAbs_IN,TopAbs_OUT) if diameters[0]>diameters[1] else (TopAbs_OUT,TopAbs_IN)
    actual=(classify(before),classify(after))
    record('df.shaft_shoulder_material_sides',actual==wanted,[str(value) for value in actual])
    outside.extend([(0,0,-dz),(0,0,total+dz)])
    record('df.material_points',all(classify(point)==TopAbs_IN for point in material),material)
    record('df.outside_air_points',all(classify(point)==TopAbs_OUT for point in outside),outside)
    record('df.source_unchanged',hashlib.sha256(path.read_bytes()).hexdigest()==source_sha)
    return {'schema_version':2,'spec':spec,'step_sha256':source_sha,'passed':all(row['passed'] for row in checks),
            'checks':checks,'engine':'DrawForm OCP8 fresh process','ocp_version':OCP.__version__}


def inspect(path, spec):
    spec = validate_spec(spec)
    if spec['kind'] not in ('plate', 'washer', 'stepped_shaft'):
        raise ValueError('unsupported simple-part kind')
    path = Path(path)
    source_bytes = path.read_bytes()
    source_sha = hashlib.sha256(source_bytes).hexdigest()
    with tempfile.TemporaryDirectory(prefix='drawform-inspect-simple-') as scratch:
        snapshot = Path(scratch) / 'model.step'
        snapshot.write_bytes(source_bytes)
        model = import_step(snapshot)
    if spec["kind"] == "stepped_shaft":
        return inspect_stepped_shaft(path, spec, model, source_sha)
    features = extract_features(model.shape, model.bbox)
    bores = [f for f in features if isinstance(f, Bore)]
    shafts = [f for f in features if isinstance(f, Shaft)]
    p = spec['parameters']; thick = p['thickness_mm']
    close = lambda a,b: math.isclose(a,b,rel_tol=1e-9,abs_tol=1e-5)
    props = GProp_GProps(); BRepGProp.VolumeProperties_s(model.shape,props)
    checks=[]
    def check(name, passed, actual=None):
        row={'id':name,'passed':bool(passed)}
        if actual is not None:row['actual']=actual
        checks.append(row)
    check('df.single_valid_solid',model.is_valid and model.solid_count==1,
          {'solid_count':model.solid_count,'is_valid':model.is_valid})
    shells=TopExp_Explorer(model.shape,TopAbs_SHELL); free_edges=[]
    while shells.More():
        shell=shells.Current()
        analyzer=ShapeAnalysis_Shell(); analyzer.LoadShells(shell); analyzer.CheckOrientedShells(shell,True)
        free_edges.append(bool(analyzer.HasFreeEdges())); shells.Next()
    check('df.closed_shells',bool(free_edges) and all(not flag for flag in free_edges),free_edges)

    if spec['kind']=='plate':
        length,width=p['length_mm'],p['width_mm']
        expected_box=(length,width,thick)
        expected_holes=spec['holes']
        expected_volume=length*width*thick-sum(math.pi*h['diameter_mm']**2*thick/4 for h in expected_holes)
        check('df.no_outer_cylinder',not shafts,[s.diameter for s in shafts])
        edge_x=length/2-max((abs(h['x_mm'])+h['diameter_mm']/2 for h in expected_holes),default=0)
        edge_y=width/2-max((abs(h['y_mm'])+h['diameter_mm']/2 for h in expected_holes),default=0)
        material_points=[(length/2-edge_x/2,width/2-edge_y/2,thick/2),
                         (-length/2+edge_x/2,-width/2+edge_y/2,thick/2)]
    else:
        outer,inner=p['outer_diameter_mm'],p['inner_diameter_mm']
        expected_box=(outer,outer,thick)
        expected_volume=math.pi*(outer**2-inner**2)*thick/4
        expected_holes=[{'x_mm':0,'y_mm':0,'diameter_mm':inner}] if inner else []
        outer_good=len(shafts)==1 and close(shafts[0].diameter,outer) and close(shafts[0].length,thick) \
            and close(shafts[0].axis_direction[0],0) and close(shafts[0].axis_direction[1],0) \
            and close(abs(shafts[0].axis_direction[2]),1)
        if outer_good:
            location=shafts[0].axis_point; direction=shafts[0].axis_direction
            dot=sum(a*b for a,b in zip(location,direction))
            outer_good=all(close(a-dot*b,0) for a,b in zip(location,direction))
        check('df.outer_cylinder',outer_good,[{'diameter':s.diameter,'length':s.length,'axis_point':s.axis_point} for s in shafts])
        r=(outer+inner)/4
        material_points=[(r,0,thick/2),(-r,0,thick/2),(0,r,thick/2),(0,-r,thick/2)]
        if inner==0:material_points.append((0,0,thick/2))

    check('df.extents',all(close(a,b) for a,b in zip(model.bbox,expected_box)),list(model.bbox))
    check('df.origin',all(close(a,b) for a,b in zip(model.origin,(-expected_box[0]/2,-expected_box[1]/2,0))),list(model.origin))
    check('df.volume',props.Mass()>0 and close(props.Mass(),expected_volume),
          {'actual_mm3':props.Mass(),'expected_mm3':expected_volume})
    matches=[]
    for h in expected_holes:
        indices=[i for i,b in enumerate(bores) if close(b.diameter,h['diameter_mm'])
                 and close(b.axis_offset()[0],h['x_mm']) and close(b.axis_offset()[1],h['y_mm'])]
        matches.append(indices)
    matched=len(bores)==len(expected_holes) and all(len(m)==1 for m in matches) \
        and len({m[0] for m in matches if len(m)==1})==len(expected_holes)
    check('df.hole_count_positions_diameters',matched,[{'diameter':b.diameter,'offset':b.axis_offset()} for b in bores])
    check('df.hole_axes_and_through_span',all(b.through and close(b.depth,thick)
          and close(b.axis_direction[0],0) and close(b.axis_direction[1],0)
          and close(abs(b.axis_direction[2]),1) and b.axis_span is not None
          and close(b.axis_span[0],0) and close(b.axis_span[1],thick) for b in bores),
          [{'through':b.through,'depth':b.depth,'span':b.axis_span} for b in bores])
    classify=lambda point:BRepClass3d_SolidClassifier(model.shape,gp_Pnt(*point),1e-7).State()
    air=[(h['x_mm'],h['y_mm'],fraction*thick) for h in expected_holes for fraction in (.2,.8)]
    check('df.hole_air_points',all(classify(point)==TopAbs_OUT for point in air),air)
    check('df.material_points',bool(material_points) and all(classify(point)==TopAbs_IN for point in material_points),material_points)
    if spec['kind']=='washer':
        outside=[(p['outer_diameter_mm']*.75,0,thick/2)]
        check('df.outside_air',all(classify(point)==TopAbs_OUT for point in outside),outside)
    check('df.source_unchanged',hashlib.sha256(path.read_bytes()).hexdigest()==source_sha)
    return {'schema_version':2,'spec':spec,'step_sha256':source_sha,
            'passed':all(c['passed'] for c in checks),'checks':checks,
            'engine':'DrawForm OCP8 fresh process','ocp_version':OCP.__version__}


def main():
    request=json.loads(Path(sys.argv[1]).read_text())
    result=inspect(request['step'],request['spec'])
    Path(request['output']).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return 0 if result['passed'] else 2

if __name__=='__main__':
    raise SystemExit(main())

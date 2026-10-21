"""Fixed-source plate/washer/stepped-shaft CAD worker in the isolated cadgen OCCT 7.9 runtime."""
from __future__ import annotations
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from plate import WorkerError, cold_environment, digest, make_preview, runtime_versions, write_json

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'drawform/src'))

MODEL_SOURCE = '''from cadgen import build123d as bd
from cadgen import step
import json
from pathlib import Path

@step(out="out/model.step")
def model():
    spec = json.loads(Path(__file__).with_name("parameters.json").read_text())
    p = spec["parameters"]
    align = (bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN)
    if spec["kind"] == "plate":
        thickness = p["thickness_mm"]
        part = bd.Box(p["length_mm"], p["width_mm"], thickness, align=align)
        for hole in spec["holes"]:
            part -= bd.Pos(hole["x_mm"], hole["y_mm"], -1) * bd.Cylinder(
                hole["diameter_mm"] / 2, thickness + 2, align=align)
    elif spec["kind"] == "washer":
        thickness = p["thickness_mm"]
        part = bd.Cylinder(p["outer_diameter_mm"] / 2, thickness, align=align)
        if p["inner_diameter_mm"] > 0:
            part -= bd.Pos(0, 0, -1) * bd.Cylinder(
                p["inner_diameter_mm"] / 2, thickness + 2, align=align)
    elif spec["kind"] == "stepped_shaft":
        part = bd.Cylinder(p["diameter_1_mm"] / 2, p["length_1_mm"], align=align)
        part += bd.Pos(0, 0, p["length_1_mm"]) * bd.Cylinder(
            p["diameter_2_mm"] / 2, p["length_2_mm"], align=align)
    else:
        raise ValueError("unsupported fixed model kind")
    part.label = spec["kind"]
    return part

if __name__ == "__main__":
    model()
'''


def read_request(path):
    try:
        if path.stat().st_size > 65536:
            raise ValueError('request exceeds 65536 bytes')
        request = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(request, dict) or set(request) != {'schema_version', 'spec', 'output_dir'}:
            raise ValueError('request must contain only schema_version, spec, output_dir')
        if type(request['schema_version']) is not int or request['schema_version'] != 2:
            raise ValueError('schema_version must be integer 2')
        # Controlled pure-data import; never imports modeling, Flask or a CAD kernel.
        from drawform.workbench.simple_spec import validate_spec
        spec = validate_spec(request['spec'])
        if spec['kind'] not in ('plate', 'washer', 'stepped_shaft'):
            raise ValueError('this worker supports plate, washer and two-segment solid shaft only')
        output_value = request['output_dir']
        if not isinstance(output_value, str) or not Path(output_value).is_absolute():
            raise ValueError('output_dir must be an absolute private path')
        output = Path(output_value)
        if output != output.resolve():
            raise ValueError('output_dir must be canonical without symlink aliases')
        return spec, output
    except (ValueError, TypeError, KeyError, OSError, OverflowError) as exc:
        raise WorkerError('INVALID_REQUEST', str(exc)) from exc


def validate_saved_shaft(path, spec):
    """Cold OCP7.9 file inspection; no production shaft-recognition helper."""
    from build123d import import_step
    from cadgen.geometry import boundary_edges, topology_errors
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepClass import BRepClass_FaceClassifier
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    from OCP.GeomAbs import GeomAbs_Cylinder, GeomAbs_Plane
    from OCP.TopAbs import TopAbs_IN, TopAbs_ON, TopAbs_OUT
    from OCP.gp import gp_Pnt

    path = Path(path)
    original = path.read_bytes()
    import hashlib
    sha = hashlib.sha256(original).hexdigest()
    with tempfile.TemporaryDirectory(prefix='cadgen-shaft-readback-') as folder:
        snapshot = Path(folder) / 'saved.step'
        snapshot.write_bytes(original)
        shape = import_step(str(snapshot))
    p = spec['parameters']
    d1, l1, d2, l2 = (p[k] for k in ('diameter_1_mm', 'length_1_mm', 'diameter_2_mm', 'length_2_mm'))
    r1, r2, total = d1/2, d2/2, l1+l2
    close = lambda a,b: math.isfinite(a) and math.isfinite(b) and math.isclose(a,b,rel_tol=1e-9,abs_tol=1e-6)
    solids = list(shape.solids())
    classify = (lambda point: BRepClass3d_SolidClassifier(solids[0].wrapped,gp_Pnt(*point),1e-7).State()) if len(solids)==1 else (lambda point: None)
    box = shape.bounding_box()
    lo = [float(box.min.X),float(box.min.Y),float(box.min.Z)]
    hi = [float(box.max.X),float(box.max.Y),float(box.max.Z)]
    size = [b-a for a,b in zip(lo,hi)]
    volume = float(sum(s.volume for s in solids))
    cylinders, planes, other = [], [], []
    for face in shape.faces():
        surface = BRepAdaptor_Surface(face.wrapped)
        if surface.GetType()==GeomAbs_Cylinder:
            cylinder=surface.Cylinder();axis=cylinder.Axis();direction=axis.Direction();location=axis.Location()
            vector=[float(direction.X()),float(direction.Y()),float(direction.Z())]
            origin=[float(location.X()),float(location.Y()),float(location.Z())]
            along=sum(a*b for a,b in zip(vector,origin))
            offset=[a-along*b for a,b in zip(origin,vector)]
            u0,u1=float(surface.FirstUParameter()),float(surface.LastUParameter())
            v0,v1=float(surface.FirstVParameter()),float(surface.LastVParameter())
            u=(u0+u1)/2;v=(v0+v1)/2
            span=sorted(float(surface.Value(u,t).Z()) for t in (v0,v1))
            point=surface.Value(u,v);xyz=[float(point.X()),float(point.Y()),float(point.Z())]
            axial=sum((a-b)*c for a,b,c in zip(xyz,origin,vector))
            radial=[a-b-axial*c for a,b,c in zip(xyz,origin,vector)]
            radius=float(cylinder.Radius());radial_length=math.sqrt(sum(a*a for a in radial))
            delta=min(radius/4,(span[1]-span[0])/4,max(1e-5,radius*1e-4))
            normal=[a/radial_length for a in radial] if radial_length>0 else [0,0,0]
            inside=[a-delta*b for a,b in zip(xyz,normal)];outside=[a+delta*b for a,b in zip(xyz,normal)]
            outer=delta>0 and classify(inside)==TopAbs_IN and classify(outside)==TopAbs_OUT
            cylinders.append({'diameter_mm':2*radius,'axis_offset_mm':offset,'axis_direction':vector,
                              'z_span_mm':span,'u_span_rad':u1-u0,'outer_surface':bool(outer)})
        elif surface.GetType()==GeomAbs_Plane:
            props=GProp_GProps();BRepGProp.SurfaceProperties_s(face.wrapped,props)
            plane=surface.Plane();normal=plane.Axis().Direction()
            planes.append((face.wrapped,{'z_mm':float(plane.Location().Z()),'area_mm2':float(props.Mass()),
                                         'normal':[float(normal.X()),float(normal.Y()),float(normal.Z())]}))
        else:
            other.append(str(surface.GetType()))
    expected_segments=[(d1,0.,l1),(d2,l1,total)]
    matches=[[i for i,c in enumerate(cylinders) if close(c['diameter_mm'],diameter)
              and close(c['z_span_mm'][0],low) and close(c['z_span_mm'][1],high)]
             for diameter,low,high in expected_segments]
    segments_ok=len(cylinders)==2 and all(len(m)==1 for m in matches) and len({m[0] for m in matches if len(m)==1})==2
    expected_planes=[(0.,math.pi*r1*r1),(l1,math.pi*abs(r1*r1-r2*r2)),(total,math.pi*r2*r2)]
    plane_matches=[[i for i,(_,row) in enumerate(planes) if close(row['z_mm'],z) and close(row['area_mm2'],area)]
                   for z,area in expected_planes]
    planes_ok=len(planes)==3 and all(len(m)==1 for m in plane_matches) and len({m[0] for m in plane_matches if len(m)==1})==3
    shoulder_radius=(r1+r2)/2;shoulder_point=(shoulder_radius,0,l1)
    shoulder=[face for face,row in planes if close(row['z_mm'],l1) and close(row['area_mm2'],expected_planes[1][1])]
    shoulder_ok=False
    if len(shoulder)==1:
        shoulder_ok=BRepClass_FaceClassifier(shoulder[0],gp_Pnt(*shoulder_point),1e-7).State() in (TopAbs_IN,TopAbs_ON) \
            and BRepClass_FaceClassifier(shoulder[0],gp_Pnt(0,0,l1),1e-7).State()==TopAbs_OUT
    material=[];air=[]
    for radius,low,high in ((r1,0.,l1),(r2,l1,total)):
        mid=(low+high)/2
        material.extend([(0,0,mid),(radius/2,0,mid),(-radius/2,0,mid),(0,radius/2,mid)])
        air.extend([(radius*1.25,0,mid),(-radius*1.25,0,mid)])
    dz=min(l1,l2)/4
    shoulder_states=[classify((shoulder_radius,0,l1-dz)),classify((shoulder_radius,0,l1+dz))]
    expected_states=[TopAbs_IN,TopAbs_OUT] if r1>r2 else [TopAbs_OUT,TopAbs_IN]
    air.extend([(0,0,-dz),(0,0,total+dz)])
    free=[len(boundary_edges(shell)) for solid in solids for shell in solid.shells()]
    issues=topology_errors(shape)
    checks=[]
    def check(name,passed,actual):checks.append({'name':name,'status':'passed' if passed else 'failed','actual':actual})
    diameter=max(d1,d2)
    check('single_solid',len(solids)==1,len(solids))
    check('valid_topology',bool(shape.is_valid) and not issues,{'valid':bool(shape.is_valid),'issues':[i.code for i in issues]})
    check('closed_shells',bool(free) and all(n==0 for n in free),free)
    check('bbox_size',all(close(a,b) for a,b in zip(size,(diameter,diameter,total))),size)
    check('bbox_origin',all(close(a,b) for a,b in zip(lo,(-diameter/2,-diameter/2,0))),lo)
    check('positive_volume',volume>0,volume)
    check('analytical_volume',close(volume,math.pi*(d1*d1*l1+d2*d2*l2)/4),volume)
    check('shaft_only_expected_surface_types',not other and len(cylinders)==2 and len(planes)==3,{'other':other,'cylinders':len(cylinders),'planes':len(planes)})
    check('shaft_cylinder_axes',len(cylinders)==2 and all(all(close(a,b) for a,b in zip(c['axis_offset_mm'],(0,0,0))) and close(c['axis_direction'][0],0) and close(c['axis_direction'][1],0) and close(abs(c['axis_direction'][2]),1) for c in cylinders),cylinders)
    check('shaft_full_finite_cylinders',len(cylinders)==2 and all(close(abs(c['u_span_rad']),2*math.pi) and all(math.isfinite(v) for v in c['z_span_mm']) and c['z_span_mm'][1]>c['z_span_mm'][0] for c in cylinders),cylinders)
    check('shaft_no_inner_cylinder',len(cylinders)==2 and all(c['outer_surface'] for c in cylinders),cylinders)
    check('shaft_segment_z_intervals',segments_ok,{'expected':expected_segments,'actual':cylinders})
    check('shaft_three_axial_planes',planes_ok and all(close(row['normal'][0],0) and close(row['normal'][1],0) and close(abs(row['normal'][2]),1) for _,row in planes),[row for _,row in planes])
    check('shaft_shoulder_annulus',shoulder_ok,{'point':shoulder_point,'z_mm':l1,'expected_area_mm2':expected_planes[1][1]})
    check('shaft_shoulder_material_sides',shoulder_states==expected_states,[str(state) for state in shoulder_states])
    check('material_points',all(classify(point)==TopAbs_IN for point in material),material)
    check('outside_air_points',all(classify(point)==TopAbs_OUT for point in air),air)
    check('source_unchanged',digest(path)==sha,sha)
    return shape,{'schema_version':2,'step_sha256':sha,'spec':spec,'passed':all(c['status']=='passed' for c in checks),'checks':checks,
                  'numerical_tolerance':{'absolute_mm':1e-6,'relative':1e-9},
                  'facts':{'solid_count':len(solids),'bbox':{'min':lo,'max':hi,'size':size},'bbox_mm':size,'volume_mm3':volume,'cylinders':cylinders,'planes':[row for _,row in planes]}}


def validate_saved_step(path, spec):
    if spec["kind"] == "stepped_shaft":
        return validate_saved_shaft(path, spec)
    from build123d import import_step
    from cadgen.geometry import boundary_edges, topology_errors
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.GeomAbs import GeomAbs_Cylinder
    from OCP.TopAbs import TopAbs_IN, TopAbs_OUT
    from OCP.gp import gp_Pnt

    shape = import_step(str(path))
    p = spec['parameters']; thick = p['thickness_mm']
    solids = list(shape.solids())
    box = shape.bounding_box()
    lo = [float(box.min.X), float(box.min.Y), float(box.min.Z)]
    hi = [float(box.max.X), float(box.max.Y), float(box.max.Z)]
    size = [b-a for a, b in zip(lo, hi)]
    volume = float(sum(s.volume for s in solids))
    if spec['kind'] == 'plate':
        length, width = p['length_mm'], p['width_mm']
        holes = spec['holes']
        expected_cylinders = [(h['x_mm'], h['y_mm'], h['diameter_mm']) for h in holes]
        expected_size = [length, width, thick]
        expected_volume = (length*width - sum(math.pi*(h['diameter_mm']/2)**2 for h in holes))*thick
        margin_x = length/2-max((abs(h['x_mm'])+h['diameter_mm']/2 for h in holes), default=0)
        margin_y = width/2-max((abs(h['y_mm'])+h['diameter_mm']/2 for h in holes), default=0)
        material_x, material_y = length/2-margin_x/2, width/2-margin_y/2
        material_points = [[sx*material_x, sy*material_y, thick/2] for sx in (-1,1) for sy in (-1,1)]
    else:
        outer, inner = p['outer_diameter_mm'], p['inner_diameter_mm']
        holes = [{'x_mm':0., 'y_mm':0., 'diameter_mm':inner}] if inner else []
        expected_cylinders = [(0., 0., outer)] + ([(0.,0.,inner)] if inner else [])
        expected_size = [outer, outer, thick]
        expected_volume = math.pi*(outer*outer-inner*inner)*thick/4
        radius = (outer+inner)/4
        material_points = [[radius,0,thick/2], [-radius,0,thick/2], [0,radius,thick/2], [0,-radius,thick/2]]
    cylinders = []
    for face in shape.faces():
        surface = BRepAdaptor_Surface(face.wrapped)
        if surface.GetType() != GeomAbs_Cylinder:
            continue
        c = surface.Cylinder(); axis = c.Axis(); loc = axis.Location(); d = axis.Direction()
        direction = [float(d.X()), float(d.Y()), float(d.Z())]
        location = [float(loc.X()), float(loc.Y()), float(loc.Z())]
        projection = sum(a*b for a,b in zip(direction,location))
        offset = [a-projection*b for a,b in zip(location,direction)]
        u = (surface.FirstUParameter()+surface.LastUParameter())/2
        span = sorted(float(surface.Value(u,v).Z()) for v in (surface.FirstVParameter(),surface.LastVParameter()))
        row = {'diameter_mm':float(2*c.Radius()),'axis_offset_mm':offset,'axis_direction':direction,'z_span_mm':span}
        if not any(all(math.isclose(a,b,abs_tol=1e-6) for a,b in zip(
                [row['diameter_mm'],*offset,*span], [old['diameter_mm'],*old['axis_offset_mm'],*old['z_span_mm']])) for old in cylinders):
            cylinders.append(row)
    close = lambda a,b: math.isclose(a,b,rel_tol=1e-9,abs_tol=1e-6)
    matched = []
    for x,y,diameter in expected_cylinders:
        matches = [i for i,c in enumerate(cylinders) if close(c['diameter_mm'],diameter)
                   and close(c['axis_offset_mm'][0],x) and close(c['axis_offset_mm'][1],y)]
        matched.append(matches)
    cylinder_match = len(cylinders)==len(expected_cylinders) and all(len(m)==1 for m in matched) \
        and len({m[0] for m in matched if len(m)==1}) == len(expected_cylinders)
    axis_ok = all(close(c['axis_direction'][0],0) and close(c['axis_direction'][1],0)
                  and close(abs(c['axis_direction'][2]),1) for c in cylinders)
    span_ok = all(close(c['z_span_mm'][0],0) and close(c['z_span_mm'][1],thick) for c in cylinders)
    air_points = [[h['x_mm'],h['y_mm'],thick*f] for h in holes for f in (.25,.5,.75)]
    air_ok = not air_points
    material_ok = False
    if len(solids)==1:
        classify = lambda point: BRepClass3d_SolidClassifier(solids[0].wrapped,gp_Pnt(*point),1e-7).State()
        air_ok = all(classify(point)==TopAbs_OUT for point in air_points)
        material_ok = all(classify(point)==TopAbs_IN for point in material_points)
    free = [len(boundary_edges(shell)) for solid in solids for shell in solid.shells()]
    issues = topology_errors(shape)
    checks=[]
    def check(name, passed, actual):
        checks.append({'name':name,'status':'passed' if passed else 'failed','actual':actual})
    check('single_solid',len(solids)==1,len(solids))
    check('valid_topology',bool(shape.is_valid) and not issues,{'valid':bool(shape.is_valid),'issues':[i.code for i in issues]})
    check('closed_shells',bool(free) and all(n==0 for n in free),free)
    check('bbox_size',all(close(a,b) for a,b in zip(size,expected_size)),size)
    check('bbox_origin',all(close(a,b) for a,b in zip(lo,[-expected_size[0]/2,-expected_size[1]/2,0])),lo)
    check('positive_volume',bool(solids) and all(s.volume>0 for s in solids),[float(s.volume) for s in solids])
    check('analytical_volume',close(volume,expected_volume),{'actual':volume,'expected':expected_volume})
    check('cylinder_positions_diameters_count',cylinder_match,cylinders)
    check('cylinder_axes',axis_ok,[c['axis_direction'] for c in cylinders])
    check('cylinder_through_spans',span_ok,[c['z_span_mm'] for c in cylinders])
    check('hole_air_points',air_ok,air_points)
    check('material_points',material_ok,material_points)
    bbox={'min':lo,'max':hi,'size':size}
    return shape, {'schema_version':2,'step_sha256':digest(path),'spec':spec,
                   'passed':all(c['status']=='passed' for c in checks), 'checks':checks,
                   'numerical_tolerance':{'absolute_mm':1e-6,'relative':1e-9},
                   'facts':{'solid_count':len(solids),'bbox':bbox,'bbox_mm':size,'volume_mm3':volume,'cylinders':cylinders}}


def run(request_path):
    spec, output = read_request(request_path)
    versions = runtime_versions()
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise WorkerError('OUTPUT_NOT_EMPTY','output_dir must be new or empty')
    output.mkdir(parents=True,exist_ok=True)
    work=output/'work'; work.mkdir(exist_ok=False)
    (work/'model.py').write_text(MODEL_SOURCE,encoding='utf-8')
    write_json(work/'parameters.json',spec)
    env=cold_environment(work)
    with (work/'model-stdout.log').open('wb') as stdout, (work/'model-stderr.log').open('wb') as stderr:
        proc=subprocess.run([sys.executable,str(work/'model.py'),'--json'],cwd=work,env=env,stdout=stdout,stderr=stderr)
    if proc.returncode:
        raise WorkerError('MODEL_BUILD_FAILED',f'cadgen exited {proc.returncode}; see private work logs')
    staged=work/'out/model.step'
    if not staged.is_file() or not staged.stat().st_size:
        raise WorkerError('STEP_MISSING','declared STEP output is missing')
    for key,value in env.items():
        if key.startswith('CADGEN_') or key=='DO_NOT_TRACK':os.environ[key]=value
    step=output/'model.step'; os.replace(staged,step)
    shape,validation=validate_saved_step(step,spec)
    write_json(output/'validation.json',validation)
    if not validation['passed']:raise WorkerError('STEP_VALIDATION_FAILED','saved STEP does not match the specification')
    preview=make_preview(shape,validation['step_sha256'],validation['facts']['bbox'])
    preview['schema_version']=2
    write_json(output/'preview.json',preview)
    paths={'step':step,'validation':output/'validation.json','preview':output/'preview.json',
           'source':work/'model.py','parameters':work/'parameters.json'}
    files={key:{'path':path.relative_to(output).as_posix(),'sha256':digest(path),'size_bytes':path.stat().st_size}
           for key,path in paths.items()}
    result={'schema_version':2,'status':'succeeded','spec':spec,'runtime':versions,
            'step_sha256':validation['step_sha256'],'files':files,
            'validation':{'passed':True,'checks':validation['checks']}}
    write_json(output/'result.json',result)
    return result


def main():
    try:
        if len(sys.argv)!=2:raise WorkerError('INVALID_REQUEST','usage: simple.py request.json')
        print(json.dumps(run(Path(sys.argv[1])),ensure_ascii=False,allow_nan=False))
        return 0
    except WorkerError as exc:
        print(json.dumps({'schema_version':2,'status':'failed','error':{'code':exc.code,'message':str(exc)}},ensure_ascii=False),file=sys.stderr)
        return 2 if exc.code=='INVALID_REQUEST' else 3
    except Exception as exc:
        print(json.dumps({'schema_version':2,'status':'failed','error':{'code':'WORKER_FAILED','message':f'{type(exc).__name__}: {exc}'}},ensure_ascii=False),file=sys.stderr)
        return 3

if __name__=='__main__':
    raise SystemExit(main())

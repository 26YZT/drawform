"""在DrawForm OCP8独立进程中重开cadgen产物；不占用HTTP/既有几何线程。"""
from __future__ import annotations
import hashlib
import json
import math
import sys
from pathlib import Path
import OCP
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from drawform.geometry.loader import import_step
from drawform.geometry.features import extract_features, Bore


def inspect(path, parameters):
    model = import_step(Path(path))
    p = parameters; length, width, thick, diameter = (p[k] for k in ('length_mm','width_mm','thickness_mm','hole_diameter_mm'))
    close = lambda a,b: math.isclose(a,b,rel_tol=1e-9,abs_tol=1e-5)
    features = extract_features(model.shape, model.bbox)
    bores = [b for b in features if isinstance(b, Bore)]
    props = GProp_GProps(); BRepGProp.VolumeProperties_s(model.shape, props)
    expected_volume = (length*width-math.pi*(diameter/2)**2)*thick
    checks = [dict(id='df.solid',passed=model.is_valid and model.solid_count==1),
              dict(id='df.extents',passed=all(close(a,b) for a,b in zip(model.bbox,(length,width,thick))),actual=list(model.bbox)),
              dict(id='df.origin',passed=all(close(a,b) for a,b in zip(model.origin,(-length/2,-width/2,0))),actual=list(model.origin)),
              dict(id='df.volume',passed=close(props.Mass(),expected_volume),actual=props.Mass()),
              dict(id='df.hole',passed=len(bores)==1 and close(bores[0].diameter,diameter) and bores[0].through and close(bores[0].depth,thick) and close(bores[0].axis_direction[0],0) and close(bores[0].axis_direction[1],0) and all(close(v,0) for v in bores[0].axis_offset()[:2]))]
    return {'schema_version':1,'step_sha256':hashlib.sha256(Path(path).read_bytes()).hexdigest(),'passed':all(c['passed'] for c in checks),'checks':checks,'engine':'DrawForm OCP8 fresh process','ocp_version':OCP.__version__}


def main():
    request = json.loads(Path(sys.argv[1]).read_text())
    result = inspect(request['step'],request['parameters'])
    Path(request['output']).write_text(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if result['passed'] else 2

if __name__=='__main__':
    raise SystemExit(main())

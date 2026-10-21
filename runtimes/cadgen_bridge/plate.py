"""Bounded, real cadgen plate worker; invoked only in the isolated runtime.

Usage: <model-python> plate.py <request.json>
The caller owns deadlines, process-group cancellation and private output paths.
This worker never imports DrawForm's OCCT runtime or executes supplied Python.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import subprocess
import sys


class WorkerError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


DIMENSION_KEYS = ("length_mm", "width_mm", "thickness_mm", "hole_diameter_mm")
REQUEST_KEYS = {"schema_version", "output_dir", *DIMENSION_KEYS}
MODEL_SOURCE = '''from cadgen import build123d as bd
from cadgen import step
import json
from pathlib import Path


@step(out="out/plate.step")
def model():
    parameters = json.loads(Path(__file__).with_name("parameters.json").read_text())
    length = parameters["length_mm"]
    width = parameters["width_mm"]
    thickness = parameters["thickness_mm"]
    radius = parameters["hole_diameter_mm"] / 2.0
    align = (bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN)
    body = bd.Box(length, width, thickness, align=align)
    # Extend the cutter beyond both cap faces; no coincident cutter caps.
    tool = bd.Pos(0, 0, -1.0) * bd.Cylinder(
        radius=radius, height=thickness + 2.0, align=align
    )
    result = body - tool
    result.label = "plate"
    return result


if __name__ == "__main__":
    model()
'''


def read_request(path: Path):
    try:
        if path.stat().st_size > 65_536:
            raise ValueError("request exceeds 65536 bytes")
        request = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(request, dict) or set(request) != REQUEST_KEYS:
            raise ValueError("request must contain exactly the documented fields")
        if type(request["schema_version"]) is not int or request["schema_version"] != 1:
            raise ValueError("schema_version must be integer 1")
        parameters = {}
        for key in DIMENSION_KEYS:
            value = request[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{key} must be a finite positive number")
            value = float(value)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{key} must be a finite positive number")
            parameters[key] = value
        if parameters["hole_diameter_mm"] >= min(parameters["length_mm"], parameters["width_mm"]):
            raise ValueError("hole_diameter_mm must be smaller than length_mm and width_mm")
        value = request["output_dir"]
        if not isinstance(value, str) or not value or not Path(value).is_absolute():
            raise ValueError("output_dir must be an absolute path")
        output = Path(value)
        # The parent chooses a private path; reject symlink aliases and ambiguous '..'.
        if output != output.resolve():
            raise ValueError("output_dir must be a canonical absolute path without symlink aliases")
        return parameters, output
    except (OSError, ValueError, TypeError, OverflowError) as exc:
        raise WorkerError("INVALID_REQUEST", str(exc)) from exc


def write_json(path: Path, value):
    temporary = path.with_name(path.name + ".tmp")
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
    with temporary.open("x", encoding="utf-8") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def digest(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def runtime_versions():
    try:
        versions = {
            "python_version": platform.python_version(),
            "cadgen_version": importlib.metadata.version("cadgen"),
            "build123d_version": importlib.metadata.version("build123d"),
            "ocp_version": importlib.metadata.version("cadquery-ocp-novtk"),
        }
    except importlib.metadata.PackageNotFoundError as exc:
        raise WorkerError("RUNTIME_UNAVAILABLE", "isolated cadgen/build123d/OCP runtime is required") from exc
    if versions["cadgen_version"] != "0.7.21" or not versions["build123d_version"].startswith("0.11.") \
            or not versions["ocp_version"].startswith("7.9."):
        raise WorkerError("RUNTIME_MISMATCH", "expected cadgen 0.7.21, build123d 0.11 and OCP 7.9")
    return versions


def cold_environment(work: Path):
    allowed = ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "LC_CTYPE", "SYSTEMROOT", "SystemRoot")
    env = {key: os.environ[key] for key in allowed if key in os.environ}
    env.update({
        "CADGEN_DAEMON": "0", "CADGEN_UPDATE_CHECK": "0", "CADGEN_TELEMETRY": "0",
        "DO_NOT_TRACK": "1", "CADGEN_CACHE_DIR": str(work / "cache"),
        "CADGEN_NODE": str(work / "node-disabled"), "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
    })
    return env


def validate_saved_step(step_path: Path, parameters):
    # A fresh file parse, rather than cadgen.read_step's warm content-addressed tree.
    from build123d import import_step
    from cadgen.geometry import boundary_edges, topology_errors
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.GeomAbs import GeomAbs_Cylinder
    from OCP.TopAbs import TopAbs_IN, TopAbs_OUT
    from OCP.gp import gp_Pnt

    shape = import_step(str(step_path))
    length, width, thickness, diameter = (parameters[key] for key in DIMENSION_KEYS)
    tolerance = 1e-6  # numerical geometry tolerance in mm, not a manufacturing tolerance
    volume_expected = length * width * thickness - math.pi * (diameter / 2.0) ** 2 * thickness
    solids = list(shape.solids())
    box = shape.bounding_box()
    lo = [float(box.min.X), float(box.min.Y), float(box.min.Z)]
    hi = [float(box.max.X), float(box.max.Y), float(box.max.Z)]
    size = [b - a for a, b in zip(lo, hi)]
    volume = float(sum(solid.volume for solid in solids))
    issues = topology_errors(shape)
    free_edges = [len(boundary_edges(shell)) for solid in solids for shell in solid.shells()]
    cylinders = []
    for face in shape.faces():
        adaptor = BRepAdaptor_Surface(face.wrapped)
        if adaptor.GetType() != GeomAbs_Cylinder:
            continue
        cylinder = adaptor.Cylinder()
        axis = cylinder.Axis()
        point, direction = axis.Location(), axis.Direction()
        direction_values = [float(direction.X()), float(direction.Y()), float(direction.Z())]
        point_values = [float(point.X()), float(point.Y()), float(point.Z())]
        dot = sum(a * b for a, b in zip(point_values, direction_values))
        offset = [a - dot * b for a, b in zip(point_values, direction_values)]
        u = (adaptor.FirstUParameter() + adaptor.LastUParameter()) / 2.0
        ends = [adaptor.Value(u, v).Z() for v in (adaptor.FirstVParameter(), adaptor.LastVParameter())]
        cylinders.append({
            "diameter_mm": float(2.0 * cylinder.Radius()), "axis_direction": direction_values,
            "axis_offset_mm": offset, "z_span_mm": sorted(float(value) for value in ends),
        })
    air_points = [[0.0, 0.0, thickness * f] for f in (0.25, 0.5, 0.75)]
    material_x = ((diameter / 2.0) + (length / 2.0)) / 2.0
    material_points = [[material_x, 0.0, thickness / 2.0], [-material_x, 0.0, thickness / 2.0]]
    air_ok = material_ok = False
    if len(solids) == 1:
        solid = solids[0].wrapped
        air_ok = all(BRepClass3d_SolidClassifier(solid, gp_Pnt(*p), tolerance).State() == TopAbs_OUT
                     for p in air_points)
        material_ok = all(BRepClass3d_SolidClassifier(solid, gp_Pnt(*p), tolerance).State() == TopAbs_IN
                          for p in material_points)

    close = lambda a, b: math.isclose(a, b, rel_tol=1e-9, abs_tol=tolerance)
    checks = []
    def check(name, passed, expected, actual):
        checks.append({"name": name, "status": "passed" if passed else "failed", "expected": expected, "actual": actual})

    check("single_solid", len(solids) == 1, 1, len(solids))
    check("valid_topology", bool(shape.is_valid) and not issues, True,
          {"is_valid": bool(shape.is_valid), "issues": [item.code for item in issues]})
    check("closed_shells", bool(free_edges) and all(count == 0 for count in free_edges), "all shells have zero free edges", free_edges)
    check("bbox_size", all(close(a, b) for a, b in zip(size, (length, width, thickness))), [length, width, thickness], size)
    check("bbox_origin", all(close(a, b) for a, b in zip(lo, (-length / 2, -width / 2, 0))), [-length / 2, -width / 2, 0], lo)
    check("positive_volume", bool(solids) and all(solid.volume > 0 for solid in solids), "each solid has positive volume", [float(s.volume) for s in solids])
    check("analytical_volume", close(volume, volume_expected), volume_expected, volume)
    check("hole_diameter", bool(cylinders) and all(close(c["diameter_mm"], diameter) for c in cylinders), diameter,
          [c["diameter_mm"] for c in cylinders])
    check("hole_axis", bool(cylinders) and all(
        close(abs(c["axis_direction"][2]), 1.0) and close(c["axis_direction"][0], 0) and close(c["axis_direction"][1], 0)
        and all(close(v, 0) for v in c["axis_offset_mm"]) for c in cylinders),
        "centered on Z axis", cylinders)
    check("through_hole_span", bool(cylinders) and all(close(c["z_span_mm"][0], 0) and close(c["z_span_mm"][1], thickness)
                                                    for c in cylinders), [0, thickness], [c["z_span_mm"] for c in cylinders])
    check("hole_air_points", air_ok, "all sampled hole-center points outside material", air_points)
    check("plate_material_points", material_ok, "both sampled plate points inside material", material_points)
    report = {
        "schema_version": 1, "step_sha256": digest(step_path),
        "passed": all(item["status"] == "passed" for item in checks),
        "numerical_tolerance": {"absolute_mm": tolerance, "relative": 1e-9},
        "facts": {"solid_count": len(solids), "bbox_mm": size, "bbox": {"min": lo, "max": hi, "size": size},
                  "volume_mm3": volume, "cylindrical_faces": cylinders}, "checks": checks,
    }
    return shape, report


def make_preview(shape, step_sha, bbox):
    # Tessellation is derived from the saved STEP geometry, not the constructor.
    vertices, faces = shape.tessellate(0.1, 0.1)
    points = [[float(v.X), float(v.Y), float(v.Z)] for v in vertices]
    triangles = [[int(i) for i in face] for face in faces]
    if not points or not triangles or any(not all(math.isfinite(v) for v in p) for p in points) \
            or any(len(f) != 3 or any(i < 0 or i >= len(points) for i in f) for f in triangles):
        raise WorkerError("PREVIEW_INVALID", "saved STEP tessellation is empty or invalid")
    return {"schema_version": 1, "step_sha256": step_sha, "units": "mm", "bbox": bbox,
            "vertices": points, "faces": triangles, "purpose": "interactive_preview"}


def run(request_path: Path):
    parameters, output = read_request(request_path)
    versions = runtime_versions()
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise WorkerError("OUTPUT_NOT_EMPTY", "output_dir must be a new or empty private directory")
    output.mkdir(parents=True, exist_ok=True)
    work = output / "work"
    work.mkdir(exist_ok=False)
    (work / "model.py").write_text(MODEL_SOURCE, encoding="utf-8")
    write_json(work / "parameters.json", parameters)
    env = cold_environment(work)
    # No fresh session: the parent supervisor's process group owns all children.
    with (work / "model-stdout.log").open("wb") as stdout, (work / "model-stderr.log").open("wb") as stderr:
        proc = subprocess.run([sys.executable, str(work / "model.py"), "--json"], cwd=work, env=env,
                              stdout=stdout, stderr=stderr, check=False)
    if proc.returncode:
        raise WorkerError("MODEL_BUILD_FAILED", f"cadgen exited with code {proc.returncode}; see private work logs")
    staged_step = work / "out" / "plate.step"
    if not staged_step.is_file() or staged_step.stat().st_size == 0:
        raise WorkerError("STEP_MISSING", "cadgen completed without the declared STEP output")
    # Environment is also cold and private during file read-back in this process.
    for key, value in env.items():
        if key.startswith("CADGEN_") or key == "DO_NOT_TRACK":
            os.environ[key] = value
    step_path = output / "plate.step"
    os.replace(staged_step, step_path)
    shape, validation = validate_saved_step(step_path, parameters)
    write_json(output / "validation.json", validation)
    if not validation["passed"]:
        raise WorkerError("STEP_VALIDATION_FAILED", "saved STEP failed requested-geometry checks")
    preview = make_preview(shape, validation["step_sha256"], validation["facts"]["bbox"])
    write_json(output / "preview.json", preview)
    paths = {"step": step_path, "validation": output / "validation.json", "preview": output / "preview.json",
             "source": work / "model.py", "parameters": work / "parameters.json"}
    files = {key: {"path": path.relative_to(output).as_posix(), "sha256": digest(path), "size_bytes": path.stat().st_size}
             for key, path in paths.items()}
    result = {
        "schema_version": 1, "status": "succeeded", "parameters": parameters, "runtime": versions,
        "step_sha256": validation["step_sha256"], "files": files,
        "validation": {"passed": True, "checks": validation["checks"]},
    }
    write_json(output / "result.json", result)
    return result


def main():
    try:
        if len(sys.argv) != 2:
            raise WorkerError("INVALID_REQUEST", "usage: plate.py request.json")
        result = run(Path(sys.argv[1]))
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0
    except WorkerError as exc:
        print(json.dumps({"schema_version": 1, "status": "failed", "error": {"code": exc.code, "message": str(exc)}},
                         ensure_ascii=False), file=sys.stderr)
        return 2 if exc.code == "INVALID_REQUEST" else 3
    except Exception as exc:
        print(json.dumps({"schema_version": 1, "status": "failed", "error": {"code": "WORKER_FAILED", "message": f"{type(exc).__name__}: {exc}"}},
                         ensure_ascii=False), file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())

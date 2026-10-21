"""Two real builds, then independent saved-STEP checks and reversed-end traps.

No skip: this repository provisions both native runtimes for the first bundle.
Wrong-spec checks reuse saved bytes rather than constructing further models.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "runtimes/cadgen_bridge/simple.py"
MODEL_PYTHON = Path(os.environ.get(
    "DRAWFORM_MODEL_PYTHON", str(ROOT / "runtimes/cadgen_bridge/.venv/bin/python")))
SPECS = [
    {"schema_version": 2, "kind": "stepped_shaft", "parameters": {
        "diameter_1_mm": 40, "length_1_mm": 30,
        "diameter_2_mm": 20, "length_2_mm": 40}, "holes": []},
    {"schema_version": 2, "kind": "stepped_shaft", "parameters": {
        "diameter_1_mm": 20, "length_1_mm": 25,
        "diameter_2_mm": 40, "length_2_mm": 25}, "holes": []},
]


def environment():
    allowed = ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "LC_CTYPE", "SYSTEMROOT")
    env = {key: os.environ[key] for key in allowed if key in os.environ}
    env.update(PYTHONPATH=str(ROOT / "drawform/src"), CADGEN_DAEMON="0",
               CADGEN_UPDATE_CHECK="0", CADGEN_TELEMETRY="0", DO_NOT_TRACK="1",
               PYTHONDONTWRITEBYTECODE="1")
    return env


@pytest.fixture(scope="module", params=SPECS, ids=["forward", "reverse_equal_lengths"])
def saved_shaft(request, tmp_path_factory):
    assert MODEL_PYTHON.is_file(), "the required isolated cadgen runtime is missing"
    folder = tmp_path_factory.mktemp("native-shaft-" + request.node.name.replace("/", "_"))
    output = folder / "model"
    body = {"schema_version": 2, "spec": request.param, "output_dir": str(output)}
    entry = folder / "request.json"
    entry.write_text(json.dumps(body), encoding="utf-8")
    result = subprocess.run([str(MODEL_PYTHON), str(RUNNER), str(entry)], cwd=ROOT,
                            env=environment(), capture_output=True, text=True, timeout=180)
    (folder / "runner.stdout.log").write_text(result.stdout, encoding="utf-8")
    (folder / "runner.stderr.log").write_text(result.stderr, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr
    return {"spec": request.param, "folder": folder, "output": output,
            "step": output / "model.step", "stdout": result.stdout}


def inspect_in_process(model, spec, folder, *, worker):
    output = folder / ("cadgen-check.json" if worker else "ocp8-check.json")
    entry = folder / ("cadgen-request.json" if worker else "ocp8-request.json")
    entry.write_text(json.dumps({"step": str(model["step"]), "spec": spec,
                                 "output": str(output)}), encoding="utf-8")
    if worker:
        program = (
            "import json,sys\nfrom pathlib import Path\n"
            "sys.path.insert(0,sys.argv[1])\nfrom simple import validate_saved_step\n"
            "request=json.loads(Path(sys.argv[2]).read_text())\n"
            "_,report=validate_saved_step(Path(request['step']),request['spec'])\n"
            "Path(request['output']).write_text(json.dumps(report))\n"
        )
        args = [str(MODEL_PYTHON), "-c", program, str(RUNNER.parent), str(entry)]
    else:
        args = [sys.executable, "-m", "drawform.workbench.inspect_simple", str(entry)]
    result = subprocess.run(args, cwd=ROOT, env=environment(), capture_output=True,
                            text=True, timeout=120)
    assert output.is_file(), result.stdout + result.stderr
    report = json.loads(output.read_text())
    assert report["step_sha256"] == hashlib.sha256(model["step"].read_bytes()).hexdigest()
    return report, result


def reversed_spec(spec):
    p = spec["parameters"]
    return {"schema_version": 2, "kind": "stepped_shaft", "parameters": {
        "diameter_1_mm": p["diameter_2_mm"], "length_1_mm": p["length_2_mm"],
        "diameter_2_mm": p["diameter_1_mm"], "length_2_mm": p["length_1_mm"]}, "holes": []}


def test_real_shaft_saved_artifacts_bind_the_exact_step(saved_shaft):
    model = saved_shaft
    result = json.loads((model["output"] / "result.json").read_text())
    assert json.loads(model["stdout"].strip().splitlines()[-1]) == result
    assert result["status"] == "succeeded" and result["spec"] == model["spec"]
    assert result["runtime"]["ocp_version"].startswith("7.9.")
    sha = hashlib.sha256(model["step"].read_bytes()).hexdigest()
    assert result["step_sha256"] == sha
    for artifact in result["files"].values():
        path = model["output"] / artifact["path"]
        assert path.stat().st_size == artifact["size_bytes"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == artifact["sha256"]
    validation = json.loads((model["output"] / "validation.json").read_text())
    checks = {item["name"]: item for item in validation["checks"]}
    assert validation["passed"] and validation["step_sha256"] == sha
    assert checks["shaft_segment_z_intervals"]["status"] == "passed"
    assert checks["shaft_shoulder_annulus"]["status"] == "passed"
    assert checks["shaft_no_inner_cylinder"]["status"] == "passed"
    preview = json.loads((model["output"] / "preview.json").read_text())
    assert preview["step_sha256"] == sha and preview["vertices"] and preview["faces"]
    assert all(len(v) == 3 and all(math.isfinite(x) for x in v) for v in preview["vertices"])
    assert all(len(face) == 3 and all(0 <= i < len(preview["vertices"]) for i in face)
               for face in preview["faces"])


def test_independent_ocp8_accepts_exact_segment_order(saved_shaft, tmp_path):
    report, process = inspect_in_process(saved_shaft, saved_shaft["spec"], tmp_path, worker=False)
    assert process.returncode == 0 and report["passed"], report
    assert report["ocp_version"].startswith("8.")
    checks = {item["id"]: item for item in report["checks"]}
    assert checks["df.shaft_segment_z_intervals"]["passed"]
    assert checks["df.shaft_shoulder_plane"]["passed"]
    assert checks["df.shaft_no_inner_cylinder"]["passed"]


@pytest.mark.parametrize("worker", [True, False], ids=["ocp79", "ocp8"])
def test_reversed_ends_fail_despite_equal_bbox_and_volume(saved_shaft, tmp_path, worker):
    wrong = reversed_spec(saved_shaft["spec"])
    original = saved_shaft["spec"]["parameters"]
    reversed_parameters = wrong["parameters"]
    volume = lambda p: math.pi / 4 * (p["diameter_1_mm"] ** 2 * p["length_1_mm"]
                                    + p["diameter_2_mm"] ** 2 * p["length_2_mm"])
    assert volume(original) == volume(reversed_parameters)
    assert max(original["diameter_1_mm"], original["diameter_2_mm"]) == max(
        reversed_parameters["diameter_1_mm"], reversed_parameters["diameter_2_mm"])
    assert original["length_1_mm"] + original["length_2_mm"] == (
        reversed_parameters["length_1_mm"] + reversed_parameters["length_2_mm"])
    before = saved_shaft["step"].read_bytes()
    report, _ = inspect_in_process(saved_shaft, wrong, tmp_path, worker=worker)
    assert report["passed"] is False
    if worker:
        checks = {item["name"]: item["status"] == "passed" for item in report["checks"]}
        assert checks["bbox_size"] and checks["analytical_volume"]
        assert checks["shaft_segment_z_intervals"] is False
    else:
        checks = {item["id"]: item["passed"] for item in report["checks"]}
        assert checks["df.extents"] and checks["df.volume"]
        assert checks["df.shaft_segment_z_intervals"] is False
    assert saved_shaft["step"].read_bytes() == before

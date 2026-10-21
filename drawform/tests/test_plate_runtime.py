"""Real cross-runtime acceptance for the bounded plate modelling entrypoint.

Positive tests require the isolated cadgen interpreter.  No native geometry is
mocked, and invalid requests are rejected before importing a CAD kernel.
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
RUNNER = ROOT / "runtimes" / "cadgen_bridge" / "plate.py"


def _request(directory: Path, **changes):
    value = {
        "schema_version": 1,
        "length_mm": 100,
        "width_mm": 60,
        "thickness_mm": 8,
        "hole_diameter_mm": 20,
        "output_dir": str(directory),
    }
    value.update(changes)
    return value


def _run(tmp_path, request, python=sys.executable):
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    return subprocess.run(
        [str(python), str(RUNNER), str(request_path)],
        cwd=str(ROOT), capture_output=True, text=True, timeout=180,
    )


@pytest.fixture(scope="module")
def model_python():
    selected = Path(os.environ.get(
        "DRAWFORM_MODEL_PYTHON",
        str(ROOT / "runtimes" / "cadgen_bridge" / ".venv" / "bin" / "python"),
    ))
    if not selected.is_file():
        pytest.skip("isolated cadgen interpreter is not provisioned")
    probe = subprocess.run(
        [str(selected), "-c", "import cadgen, build123d, OCP"],
        capture_output=True, text=True, timeout=180,
    )
    if probe.returncode:
        pytest.fail("isolated cadgen interpreter is present but imports fail: " + probe.stderr)
    return selected


def test_plate_runtime_entrypoint_exists():
    assert RUNNER.is_file(), "bounded native plate runner has not been implemented"


@pytest.mark.parametrize("changes", [
    {"schema_version": 2},
    {"schema_version": True},
    {"length_mm": 0},
    {"width_mm": -1},
    {"thickness_mm": True},
    {"hole_diameter_mm": 60},
    {"hole_diameter_mm": float("nan")},
    {"length_mm": float("inf")},
    {"output_dir": "relative/output"},
    {"command": "untrusted extra input"},
])
def test_invalid_plate_requests_write_no_artifacts(tmp_path, changes):
    output_dir = tmp_path / "output"
    proc = _run(tmp_path, _request(output_dir, **changes))
    assert proc.returncode == 2, proc.stdout + proc.stderr
    error = json.loads(proc.stderr.strip().splitlines()[-1])
    assert error["schema_version"] == 1
    assert error["status"] == "failed"
    assert error["error"]["code"] == "INVALID_REQUEST"
    assert not output_dir.exists()


@pytest.mark.parametrize("length,width,thickness,diameter", [
    (100, 60, 8, 20),
    (120, 80, 10, 12),
])
def test_real_plate_step_preview_and_drawform_readback(
    tmp_path, model_python, length, width, thickness, diameter,
):
    output_dir = tmp_path / "output"
    proc = _run(tmp_path, _request(
        output_dir, length_mm=length, width_mm=width,
        thickness_mm=thickness, hole_diameter_mm=diameter,
    ), python=model_python)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    result = json.loads((output_dir / "result.json").read_text())
    assert json.loads(proc.stdout.strip().splitlines()[-1]) == result
    assert result["schema_version"] == 1
    assert result["status"] == "succeeded"
    assert result["validation"]["passed"] is True
    for item in result["files"].values():
        artifact = output_dir / item["path"]
        assert artifact.is_file()
        assert artifact.stat().st_size == item["size_bytes"]
        assert hashlib.sha256(artifact.read_bytes()).hexdigest() == item["sha256"]

    step_path = output_dir / "plate.step"
    step_sha = hashlib.sha256(step_path.read_bytes()).hexdigest()
    assert result["step_sha256"] == step_sha
    saved = step_path.read_text(errors="replace")
    assert "ISO-10303-21;" in saved and "END-ISO-10303-21;" in saved
    validation = json.loads((output_dir / "validation.json").read_text())
    assert validation["step_sha256"] == step_sha
    assert validation["passed"] is True
    assert all(item["status"] == "passed" for item in validation["checks"])
    assert validation["facts"]["solid_count"] == 1
    assert validation["facts"]["bbox_mm"] == pytest.approx([length, width, thickness], abs=1e-5)
    expected_volume = length * width * thickness - math.pi * (diameter / 2) ** 2 * thickness
    assert validation["facts"]["volume_mm3"] == pytest.approx(expected_volume, rel=1e-9)

    preview = json.loads((output_dir / "preview.json").read_text())
    assert preview["schema_version"] == 1
    assert preview["step_sha256"] == step_sha
    assert len(preview["vertices"]) > 8 and len(preview["faces"]) > 12
    assert all(len(v) == 3 and all(math.isfinite(n) for n in v) for v in preview["vertices"])
    assert all(len(f) == 3 and all(isinstance(i, int) and 0 <= i < len(preview["vertices"]) for i in f)
               for f in preview["faces"])

    # Saved STEP crosses the OCCT 7.9 -> existing DrawForm OCCT 8 boundary.
    from drawform.geometry.loader import import_step
    from drawform.geometry.features import Bore, extract_features
    model = import_step(step_path)
    assert model.is_valid and model.solid_count == 1
    assert model.bbox == pytest.approx([length, width, thickness], abs=1e-5)
    assert model.origin == pytest.approx([-length / 2, -width / 2, 0], abs=1e-5)
    bores = [f for f in extract_features(model.shape, model.bbox) if isinstance(f, Bore)]
    assert len(bores) == 1
    assert bores[0].diameter == pytest.approx(diameter, abs=1e-6)
    assert bores[0].depth == pytest.approx(thickness, abs=1e-6)
    assert bores[0].through is True
    assert bores[0].axis_offset() == pytest.approx([0, 0, 0], abs=1e-6)


def test_existing_output_is_not_overwritten(tmp_path, model_python):
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    sentinel = output_dir / "plate.step"
    sentinel.write_bytes(b"keep original")
    proc = _run(tmp_path, _request(output_dir), python=model_python)
    assert proc.returncode != 0
    assert sentinel.read_bytes() == b"keep original"
    assert not (output_dir / "result.json").exists()

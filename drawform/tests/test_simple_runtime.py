"""Real plate/washer native runtime and original drawing-chain acceptance."""
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
RUNNER = ROOT / 'runtimes/cadgen_bridge/simple.py'
PLATE = {'schema_version': 2, 'kind': 'plate',
         'parameters': {'length_mm': 120, 'width_mm': 80, 'thickness_mm': 8},
         'holes': [{'x_mm': -25, 'y_mm': 5, 'diameter_mm': 8},
                   {'x_mm': 25, 'y_mm': -5, 'diameter_mm': 12}]}
WASHER = {'schema_version': 2, 'kind': 'washer',
          'parameters': {'outer_diameter_mm': 60, 'inner_diameter_mm': 20, 'thickness_mm': 8}, 'holes': []}
DISC = {'schema_version': 2, 'kind': 'washer',
        'parameters': {'outer_diameter_mm': 40, 'inner_diameter_mm': 0, 'thickness_mm': 6}, 'holes': []}


def test_simple_runtime_entrypoints_exist():
    assert RUNNER.is_file(), 'simple native entrypoint is missing'
    assert (ROOT / 'drawform/src/drawform/workbench/inspect_simple.py').is_file()


@pytest.fixture(scope='module')
def model_python():
    selected = Path(os.environ.get('DRAWFORM_MODEL_PYTHON', str(ROOT / 'runtimes/cadgen_bridge/.venv/bin/python')))
    if not selected.is_file():
        pytest.skip('isolated native runtime is not provisioned')
    probe = subprocess.run([str(selected), '-c', 'import cadgen, build123d, OCP'],
                           capture_output=True, text=True, timeout=180)
    assert probe.returncode == 0, probe.stderr
    return selected


def _run(tmp_path, spec, python=sys.executable, output=None):
    output = output or tmp_path / 'model'
    request = {'schema_version': 2, 'spec': spec, 'output_dir': str(output)}
    path = tmp_path / 'request.json'
    path.write_text(json.dumps(request))
    return subprocess.run([str(python), str(RUNNER), str(path)], capture_output=True,
                          text=True, cwd=ROOT, timeout=180), output


@pytest.mark.parametrize('spec', [PLATE, WASHER, DISC], ids=['two_hole_plate', 'washer', 'disc'])
def test_real_simple_model_inspection_and_2d_coverage(tmp_path, model_python, spec):
    proc, output = _run(tmp_path, spec, model_python)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    result = json.loads((output / 'result.json').read_text())
    assert json.loads(proc.stdout.strip().splitlines()[-1]) == result
    assert result['schema_version'] == 2 and result['status'] == 'succeeded'
    assert result['spec']['kind'] == spec['kind'] and result['validation']['passed']
    step = output / 'model.step'
    step_sha = hashlib.sha256(step.read_bytes()).hexdigest()
    assert result['step_sha256'] == step_sha
    for item in result['files'].values():
        path = output / item['path']
        assert path.stat().st_size == item['size_bytes']
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item['sha256']
    for name in ('preview', 'validation'):
        value = json.loads((output / (name + '.json')).read_text())
        assert value['schema_version'] == 2 and value['step_sha256'] == step_sha
    preview = json.loads((output / 'preview.json').read_text())
    assert preview['vertices'] and preview['faces']
    assert all(len(v) == 3 and all(math.isfinite(x) for x in v) for v in preview['vertices'])
    assert all(len(f) == 3 and all(0 <= i < len(preview['vertices']) for i in f) for f in preview['faces'])

    # Independent OCP8 checker re-opens exactly these saved STEP bytes.
    check_output = tmp_path / 'ocp8.json'
    inspect_request = tmp_path / 'inspect-request.json'
    inspect_request.write_text(json.dumps({'step': str(step), 'spec': spec, 'output': str(check_output)}))
    env = {**os.environ, 'PYTHONPATH': str(ROOT / 'drawform/src')}
    check = subprocess.run([sys.executable, '-m', 'drawform.workbench.inspect_simple', str(inspect_request)],
                           capture_output=True, text=True, env=env, timeout=180)
    assert check.returncode == 0, check.stdout + check.stderr
    inspected = json.loads(check_output.read_text())
    assert inspected['passed'] and inspected['step_sha256'] == step_sha
    assert inspected['ocp_version'].startswith('8.')
    assert all(c['passed'] for c in inspected['checks'])
    # A different design must fail even though the STEP remains a valid solid.
    from drawform.workbench.inspect_simple import inspect
    changed_spec = json.loads(json.dumps(spec))
    if spec['kind'] == 'plate':
        changed_spec['holes'][0]['x_mm'] += 1
    else:
        changed_spec['parameters']['outer_diameter_mm'] += 2
    assert inspect(step, changed_spec)['passed'] is False

    from drawform.geometry.loader import import_step
    from drawform.geometry.features import Bore, Shaft, extract_features, group_bores, group_shafts
    from drawform.pipeline import render_part
    model = import_step(step)
    features = extract_features(model.shape, model.bbox)
    bores = [f for f in features if isinstance(f, Bore)]
    shafts = [f for f in features if isinstance(f, Shaft)]
    if spec['kind'] == 'plate':
        assert len(bores) == len(spec['holes']) and not shafts
        for hole in spec['holes']:
            matches = [b for b in bores if abs(b.diameter-hole['diameter_mm']) < 1e-6
                       and math.dist(b.axis_offset()[:2], (hole['x_mm'], hole['y_mm'])) < 1e-6]
            assert len(matches) == 1 and matches[0].through
    else:
        assert len(bores) == int(spec['parameters']['inner_diameter_mm'] > 0)
        assert {round(s.diameter, 6) for s in shafts} == {spec['parameters']['outer_diameter_mm']}

    drawing_dir = tmp_path / 'drawing'
    drawing_dir.mkdir()
    part = render_part(step, drawing_dir, stem='simple', with_questions=True)
    assert part.verdict.allowed
    assert {kind for kind, _ in part.files} == {'dxf', 'svg', 'pdf', 'json'}
    for _, path in part.files:
        assert path.is_file() and path.stat().st_size > 0
    sources = {d.source: d.value for d in part.dimensions}
    for dim in part.dimensions:
        sources.update(zip(dim.shared_sources, dim.shared_values))
    assert sources[('outline', 'height')] == pytest.approx(spec['parameters']['thickness_mm'])
    for group in group_bores(bores):
        assert sources[(group.group_id, 'diameter')] == pytest.approx(group.diameter)
    for group in group_shafts(shafts):
        assert sources[(group.group_id, 'diameter')] == pytest.approx(group.diameter)
    if spec['kind'] == 'plate':
        for b in bores:
            for index, axis in enumerate(('x', 'y')):
                assert sources[(b.feature_id, axis)] == pytest.approx(b.axis_offset()[index] - model.origin[index])
    import ezdxf
    from drawform.eval.intake import dimension_entities
    entities = list(dimension_entities(ezdxf.readfile(drawing_dir / 'simple.dxf')))
    assert entities
    measurements = [float(e.get_measurement()) for e in entities]
    for value in set(sources.values()):
        assert any(math.isclose(value, m, abs_tol=1e-5) for m in measurements), (value, measurements)


@pytest.mark.parametrize('change', ['overlap', 'outside', 'shaft', 'nan'])
def test_invalid_simple_specs_write_nothing(tmp_path, change):
    spec = json.loads(json.dumps(PLATE))
    if change == 'overlap':
        spec['holes'][1] = dict(spec['holes'][0])
    elif change == 'outside':
        spec['holes'][0]['x_mm'] = 60
    elif change == 'shaft':
        spec = {'schema_version': 2, 'kind': 'stepped_shaft', 'parameters':
                {'diameter1_mm': 40, 'length1_mm': 30, 'diameter2_mm': 20, 'length2_mm': 40}, 'holes': []}
    elif change == 'nan':
        spec['holes'][0]['diameter_mm'] = float('nan')
    proc, output = _run(tmp_path, spec)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert json.loads(proc.stderr.strip().splitlines()[-1])['error']['code'] == 'INVALID_REQUEST'
    assert not output.exists()

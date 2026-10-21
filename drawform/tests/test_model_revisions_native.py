"""One real native model-family revision loop, with immutable parent evidence.

No geometry is mocked. The route-registration red test creates no CAD tasks;
the native test deliberately builds only the original and one edited model.
"""
from __future__ import annotations
import hashlib
import io
import json
import math
from pathlib import Path
import time

import ezdxf
import pytest

from drawform.service.app import create_app

API = '/api/workbench/v1'
TOKENS = '甲席位=revision-native-owner,乙席位=revision-native-other'
AUTH = {'Authorization': 'Bearer revision-native-owner'}
OTHER = {'Authorization': 'Bearer revision-native-other'}


def _stop(app):
    service = app.extensions['drawform_modeling']
    service.stop()
    service.jobs.stop()


def _get(client, sid, headers=AUTH):
    response = client.get(API + '/modeling/sessions/' + sid, headers=headers)
    assert response.status_code == 200, response.data
    return response.get_json()


def _wait_model(client, sid, timeout=180):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        data = _get(client, sid)
        if data['state'] == 'awaiting_adoption':
            return data
        if data['state'] in ('failed', 'interrupted', 'needs_clarification'):
            raise AssertionError(data)
        time.sleep(.1)
    raise AssertionError('real revision model did not complete')


def _source_bytes():
    doc = ezdxf.new('R2010')
    doc.units = 4
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (100, 0), (100, 60), (0, 60)], close=True)
    msp.add_circle((30, 20), 5)
    msp.add_circle((70, 40), 5)
    msp.add_text('RECTANGULAR PLATE', dxfattribs={'insert': (0, -12)})
    msp.add_text('THICKNESS 8 mm', dxfattribs={'insert': (0, -20)})
    msp.add_text('THROUGH HOLES', dxfattribs={'insert': (0, -28)})
    output = io.StringIO()
    doc.write(output)
    return output.getvalue().encode()


def _create_original(client):
    source = _source_bytes()
    response = client.post(API + '/modeling/drawing-inputs', headers=AUTH,
                           data={'file': (io.BytesIO(source), 'two-hole-plate.dxf')})
    assert response.status_code == 201, response.data
    uploaded = response.get_json()
    assert uploaded['source_sha256'] == hashlib.sha256(source).hexdigest()
    response = client.post(API + '/modeling/sessions',
                           headers={**AUTH, 'Idempotency-Key': 'revision-native-original'},
                           json={'bundle_id': uploaded['bundle_id'], 'sheet': 'A3'})
    assert response.status_code == 201, response.data
    data = _wait_model(client, response.get_json()['session_id'])
    assert data['editable_spec'] == {
        'schema_version': 2, 'kind': 'plate',
        'parameters': {'length_mm': 100., 'width_mm': 60., 'thickness_mm': 8.},
        'holes': [{'x_mm': -20., 'y_mm': -10., 'diameter_mm': 10.},
                  {'x_mm': 20., 'y_mm': 10., 'diameter_mm': 10.}],
    }
    version = data['model_version']
    assert version['number'] == 1 and version['latest_number'] == 1
    assert version['root_session_id'] == data['session_id']
    assert version['parent_session_id'] is None
    assert version['mode'] == 'original' and version['changes'] == []
    assert data['artifact_integrity']['status'] == 'verified'
    return data


def _adopt_and_draw(client, data, key):
    sid = data['session_id']
    response = client.post(API + '/modeling/candidates/' + sid + '/adopt',
                           headers=AUTH, json={'expected_sha256': data['step_sha256']})
    assert response.status_code == 200, response.data
    response = client.post(API + '/documents/' + sid + '/drawings',
                           headers={**AUTH, 'Idempotency-Key': key},
                           json={'step_sha256': data['step_sha256']})
    assert response.status_code == 202, response.data
    jid = response.get_json()['job_id']
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        response = client.get('/jobs/' + jid, headers=AUTH)
        assert response.status_code == 200, response.data
        job = response.get_json()
        if job['state'] not in ('queued', 'running'):
            break
        time.sleep(.1)
    assert job['state'] == 'drawn', job
    assert set(job['files']) == {'dxf', 'svg', 'pdf', 'json'}
    files = {}
    for kind, url in job['files'].items():
        response = client.get(url, headers=AUTH)
        assert response.status_code == 200, response.data
        assert response.data
        files[kind] = response.data
    assert files['pdf'].startswith(b'%PDF')
    return jid, job, files


def _revision_body(parent, changes=None):
    return {'expected_step_sha256': parent['step_sha256'],
            'expected_spec_sha256': parent['model_version']['spec_sha256'],
            'expected_revision': parent['model_version']['latest_number'],
            'changes': changes if changes is not None else
                [{'op': 'set_hole', 'hole_id': 'hole_2', 'diameter_mm': 12}]}


def test_revision_api_route_is_registered(tmp_path):
    app = create_app(tmp_path / 'work', token=TOKENS)
    try:
        response = app.test_client().post(
            API + '/modeling/sessions/' + '0' * 32 + '/revisions',
            headers={**AUTH, 'Idempotency-Key': 'route-probe'},
            json={'expected_step_sha256': '0' * 64, 'expected_spec_sha256': '0' * 64,
                  'expected_revision': 1, 'changes': [{'op': 'set_hole', 'hole_id': 'hole_2', 'diameter_mm': 12}]},
        )
        routes = [r for r in app.url_map.iter_rules()
                  if r.rule == API + '/modeling/sessions/<sid>/revisions' and 'POST' in r.methods]
        assert routes, f'revisions POST route missing; actual API response HTTP {response.status_code}'
        assert response.status_code == 404  # Registered route correctly rejects an unknown parent.
    finally:
        _stop(app)


def test_real_native_revision_preserves_parent_history_and_geometry(tmp_path):
    work = tmp_path / 'work'
    app = create_app(work, token=TOKENS)
    try:
        client = app.test_client()
        parent = _create_original(client)
        parent_id = parent['session_id']
        original_step = client.get(parent['files']['step'], headers=AUTH).data
        parent_job, parent_job_data, original_drawing = _adopt_and_draw(client, parent, 'draw-parent')
        parent = _get(client, parent_id)
        original_facts = json.loads(json.dumps(parent['analysis']['facts']))
        original_analysis_holes = json.loads(json.dumps(parent['analysis']['holes']))
        body = _revision_body(parent)
        url = API + '/modeling/sessions/' + parent_id + '/revisions'
        revision_headers = {**AUTH, 'Idempotency-Key': 'only-hole-two-diameter'}
        response = client.post(url, headers=revision_headers, json=body)
        assert response.status_code in (200, 201, 202), response.data
        child_id = response.get_json()['session_id']
        assert child_id != parent_id
        duplicate = client.post(url, headers=revision_headers, json=body)
        assert duplicate.status_code in (200, 201, 202), duplicate.data
        assert duplicate.get_json()['session_id'] == child_id
        different = {**body, 'changes': [{'op': 'set_hole', 'hole_id': 'hole_2', 'diameter_mm': 14}]}
        assert client.post(url, headers=revision_headers, json=different).status_code == 409
        assert client.post(url, headers={**AUTH, 'Idempotency-Key': 'stale-family'}, json=body).status_code == 409
        assert client.post(url, headers={**OTHER, 'Idempotency-Key': 'other-seat'}, json=body).status_code == 404
        assert client.get(API + '/modeling/sessions/' + child_id, headers=OTHER).status_code == 404
        assert client.get(API + '/modeling/sessions/' + child_id + '/history', headers=OTHER).status_code == 404

        child = _wait_model(client, child_id)
        version = child['model_version']
        assert version['number'] == 2 and version['latest_number'] == 2
        assert version['root_session_id'] == parent_id and version['parent_session_id'] == parent_id
        assert version['parent_step_sha256'] == parent['step_sha256']
        assert version['parent_spec_sha256'] == parent['model_version']['spec_sha256']
        assert version['mode'] == 'design_variant'
        assert len(version['changes']) == 1
        change = version['changes'][0]
        assert change['hole_id'] == 'hole_2' and change['before'] == 10 and change['after'] == 12 and change['unit'] == 'mm'
        assert change['path'] and change['label']
        expected = json.loads(json.dumps(parent['editable_spec']))
        expected['holes'][1]['diameter_mm'] = 12.
        assert child['editable_spec'] == expected
        assert child['analysis']['facts'] == original_facts
        assert child['analysis']['holes'] == original_analysis_holes
        preview_response = client.get(child['source_preview_url'], headers=AUTH)
        assert preview_response.status_code == 200, preview_response.data
        assert preview_response.data.startswith(b'\x89PNG\r\n\x1a\n')
        assert child['job_id'] is None and child['artifact_integrity']['status'] == 'verified'
        assert child['validation']['passed']
        assert client.post(API + '/documents/' + child_id + '/drawings',
                           headers={**AUTH, 'Idempotency-Key': 'not-adopted'},
                           json={'step_sha256': child['step_sha256']}).status_code == 409
        child_step_response = client.get(child['files']['step'], headers=AUTH)
        assert child_step_response.status_code == 200
        child_step = child_step_response.data
        assert child_step != original_step and hashlib.sha256(child_step).hexdigest() == child['step_sha256']
        for name in ('validation', 'drawform_validation', 'preview'):
            artifact = client.get(child['files'][name], headers=AUTH)
            assert artifact.status_code == 200, artifact.data
            checked = artifact.get_json()
            assert checked['step_sha256'] == child['step_sha256']
            if name != 'preview':
                assert checked['passed']
        # Independent OCP8 feature readback from API-exported saved STEP bytes.
        readback = tmp_path / 'independent-child.step'
        readback.write_bytes(child_step)
        from drawform.geometry.loader import import_step
        from drawform.geometry.features import Bore, extract_features, group_bores
        model = import_step(readback)
        assert model.is_valid and model.solid_count == 1
        assert model.bbox == pytest.approx((100, 60, 8), abs=1e-5)
        assert model.origin == pytest.approx((-50, -30, 0), abs=1e-5)
        bores = [f for f in extract_features(model.shape, model.bbox) if isinstance(f, Bore)]
        assert len(bores) == 2
        for x, y, diameter in ((-20, -10, 10), (20, 10, 12)):
            matching = [b for b in bores if math.dist(b.axis_offset()[:2], (x, y)) < 1e-6]
            assert len(matching) == 1
            assert matching[0].diameter == pytest.approx(diameter, abs=1e-6)
            assert matching[0].depth == pytest.approx(8, abs=1e-6) and matching[0].through

        child_job, child_job_data, child_drawing = _adopt_and_draw(client, child, 'draw-child')
        assert child_job != parent_job
        document = json.loads(child_drawing['json'])
        sources = {}
        for dim in document['dimensions']:
            if dim['source']:
                sources[tuple(dim['source'])] = dim['value']
            sources.update((tuple(s), v) for s, v in zip(dim['shared_sources'], dim['shared_values']))
        for group in group_bores(bores):
            assert sources[(group.group_id, 'diameter')] == pytest.approx(group.diameter)
        for bore in bores:
            assert sources[(bore.feature_id, 'x')] == pytest.approx(bore.axis_offset()[0] + 50)
            assert sources[(bore.feature_id, 'y')] == pytest.approx(bore.axis_offset()[1] + 30)
        native = list(ezdxf.read(io.StringIO(child_drawing['dxf'].decode())).modelspace().query('DIMENSION'))
        assert len(native) == len(document['dimensions'])
        for entity, dimension in zip(native, document['dimensions']):
            assert float(entity.get_measurement()) == pytest.approx(dimension['value'], abs=1e-5)
            if dimension['kind'] == 'diameter':
                assert entity.dxf.dimtype & 7 in (3, 4)
        assert any(d['kind'] == 'diameter' and d['value'] == 12 for d in document['dimensions'])
        assert _get(client, parent_id)['job_id'] == parent_job
        assert client.get(parent['files']['step'], headers=AUTH).data == original_step
        for kind, parent_endpoint in parent_job_data['files'].items():
            assert client.get(parent_endpoint, headers=AUTH).data == original_drawing[kind]

        latest_parent = _get(client, parent_id)
        assert latest_parent['model_version']['latest_number'] == 2
        invalid_changes = [[], [{'op': 'set_hole', 'hole_id': 'hole_2', 'diameter_mm': float('nan')}],
            [{'op': 'set_hole', 'hole_id': 'hole_2', 'diameter_mm': float('inf')}],
            [{'op': 'set_hole', 'hole_id': 'hole_2', 'diameter_mm': True}],
            [{'op': 'set_hole', 'hole_id': 'hole_2', 'diameter_mm': '12+1'}],
            [{'op': 'set_hole', 'hole_id': 'hole_2', 'diameter_mm': 0}],
            [{'op': 'set_hole', 'hole_id': 'hole_2', 'x_mm': -20, 'y_mm': -10}],
            [{'op': 'set_hole', 'hole_id': 'hole_2', 'x_mm': 1000}],
            [{'op': 'set_hole', 'hole_id': 'hole_99', 'diameter_mm': 12}],
            [{'op': 'add_hole', 'diameter_mm': 12}],
            [{'op': 'set_hole', 'hole_id': 'hole_2', 'diameter_mm': 12}, {'op': 'set_hole', 'hole_id': 'hole_2', 'diameter_mm': 14}],
            [{'op': 'set_parameter', 'parameter': 'thickness_mm', 'value_mm': 8}]]
        for index, changes in enumerate(invalid_changes):
            invalid = client.post(url, headers={**AUTH, 'Idempotency-Key': f'invalid-edit-{index}'},
                                  json=_revision_body(latest_parent, changes))
            assert invalid.status_code == 422, (index, invalid.status_code, invalid.data)
        history_response = client.get(API + '/modeling/sessions/' + child_id + '/history', headers=AUTH)
        assert history_response.status_code == 200, history_response.data
        history = history_response.get_json()
        assert history['root_session_id'] == parent_id and history['selected_session_id'] == child_id
        assert history['latest_number'] == 2 and len(history['versions']) == 2
        assert sorted((v['number'], v['session_id'], v['parent_session_id'], v['job_id']) for v in history['versions']) == [
            (1, parent_id, None, parent_job), (2, child_id, parent_id, child_job)]

        _stop(app)
        app = create_app(work, token=TOKENS)
        client = app.test_client()
        restored = _get(client, child_id)
        assert restored['state'] == 'adopted' and restored['job_id'] == child_job
        assert restored['model_version'] == child['model_version']
        assert restored['model_version']['parent_session_id'] == parent_id
        assert _get(client, parent_id)['job_id'] == parent_job
        restored_history = client.get(API + '/modeling/sessions/' + child_id + '/history', headers=AUTH).get_json()
        assert restored_history == history
        assert client.get(restored['files']['step'], headers=AUTH).data == child_step
        assert client.get(parent['files']['step'], headers=AUTH).data == original_step
        for data, expected_files in ((parent_job_data, original_drawing), (child_job_data, child_drawing)):
            for kind, endpoint in data['files'].items():
                assert client.get(endpoint, headers=AUTH).data == expected_files[kind]
        replay = client.post(url, headers=revision_headers, json=body)
        assert replay.status_code in (200, 201, 202) and replay.get_json()['session_id'] == child_id

        # Parent integrity is part of ancestry, even when the child is already saved.
        parent_artifact = work / 'workbench/sessions' / parent_id / 'artifacts' / 'model.step'
        parent_artifact.write_bytes(b'changed parent STEP')
        assert client.get(parent['files']['step'], headers=AUTH).status_code == 409
        assert client.post(url, headers={**AUTH, 'Idempotency-Key': 'broken-parent'},
                           json=_revision_body(_get(client, parent_id), [{'op': 'set_hole', 'hole_id': 'hole_2', 'diameter_mm': 14}])).status_code == 409
        assert client.get(restored['files']['step'], headers=AUTH).status_code == 409
        assert client.get(restored['files']['preview'], headers=AUTH).status_code == 409
        assert client.post(API + '/modeling/candidates/' + child_id + '/adopt', headers=AUTH,
                           json={'expected_sha256': child['step_sha256']}).status_code == 409
        assert _get(client, child_id)['artifact_integrity']['status'] == 'unavailable'
        assert len(client.get(API + '/modeling/sessions/' + child_id + '/history', headers=AUTH).get_json()['versions']) == 2
    finally:
        _stop(app)

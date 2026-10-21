"""Real HTTP/native acceptance for source-preserving text clarification.

The first test is intentionally a cheap HTTP red/green guard: missing thickness
must create a clarification session and must not start native CAD generation.
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
TOKEN = 'text-clarification-native-owner'
AUTH = {'Authorization': 'Bearer ' + TOKEN}
TOKENS = '甲=' + TOKEN + ',乙=text-clarification-native-other'
OTHER = {'Authorization': 'Bearer text-clarification-native-other'}
MISSING_THICKNESS = '做一块长100毫米、宽60毫米的板，中间开一个直径20毫米的通孔。'


def _stop(app):
    service = app.extensions['drawform_modeling']
    service.stop()
    service.jobs.stop()


def _upload_text(client, text):
    response = client.post(API + '/modeling/inputs', headers=AUTH,
                           json={'text': text, 'units': 'mm'})
    assert response.status_code == 201, response.data
    uploaded = response.get_json()
    assert uploaded['text'] == text
    assert uploaded['text_sha256'] == hashlib.sha256(text.encode()).hexdigest()
    return uploaded


def _assert_no_native_artifacts(work):
    for pattern in ('*.step', 'model.log', 'readback.log', 'model.py'):
        assert not list(Path(work).rglob(pattern)), 'unanswered text must not launch a CAD worker'


def test_missing_thickness_creates_only_a_bound_question_without_cad(tmp_path):
    work = tmp_path / 'work'
    app = create_app(work, token=TOKEN)
    try:
        client = app.test_client()
        uploaded = _upload_text(client, MISSING_THICKNESS)
        response = client.post(API + '/modeling/sessions',
                               headers={**AUTH, 'Idempotency-Key': 'missing-thickness'},
                               json={'bundle_id': uploaded['bundle_id'], 'sheet': 'A3'})
        _assert_no_native_artifacts(work)
        assert response.status_code == 201, response.data
        data = response.get_json()
        assert data['state'] == 'needs_clarification'
        assert data['input_kind'] == 'text'
        assert len(data['questions']) == 1
        question = data['questions'][0]
        assert question['parameter'] == 'thickness_mm' and question['type'] == 'number'
        assert question['id'] and question['label'] and question['reason']
        assert isinstance(data['basis_sha256'], str) and len(data['basis_sha256']) == 64
        assert type(data['clarification_revision']) is int
        assert data['clarification_revision'] >= 1
        assert data['source_sha256'] == uploaded['text_sha256']
        assert not data.get('step_sha256')
        _assert_no_native_artifacts(work)
    finally:
        _stop(app)


def _create_pending(client, text, key):
    uploaded = _upload_text(client, text)
    response = client.post(API + '/modeling/sessions',
                           headers={**AUTH, 'Idempotency-Key': key},
                           json={'bundle_id': uploaded['bundle_id'], 'sheet': 'A3'})
    assert response.status_code == 201, response.data
    data = response.get_json()
    assert data['state'] == 'needs_clarification', data
    return data


def _get(client, sid):
    response = client.get(API + '/modeling/sessions/' + sid, headers=AUTH)
    assert response.status_code == 200, response.data
    return response.get_json()


def _answer_body(data, by_parameter):
    questions = {q['parameter']: q['id'] for q in data['questions']}
    return {'basis_sha256': data['basis_sha256'], 'revision': data['clarification_revision'],
            'answers': {questions[parameter]: value for parameter, value in by_parameter.items()}}


def _answer(client, data, values, key):
    body = _answer_body(data, values)
    response = client.post(API + '/modeling/sessions/' + data['session_id'] + '/answers',
                           headers={**AUTH, 'Idempotency-Key': key}, json=body)
    assert response.status_code == 200, response.data
    return response.get_json(), body


def _source_facts(data):
    return [f for f in data['analysis']['facts'] if f['evidence']['type'] != 'user_answer']


def _user_facts(data):
    return [f for f in data['analysis']['facts'] if f['evidence']['type'] == 'user_answer']


def _assert_text_evidence(data, text):
    assert data['text'] == text
    assert data['source_sha256'] == data['text_sha256'] == hashlib.sha256(text.encode()).hexdigest()
    analysis = data['analysis']
    normalized = analysis['normalized_text']
    assert analysis['normalized_text_sha256'] == hashlib.sha256(normalized.encode()).hexdigest()
    facts = _source_facts(data)
    assert facts
    spans = [f['evidence'] for f in facts if f['evidence']['type'] == 'text_span']
    assert spans
    for evidence in spans:
        start, end = evidence['span']
        assert evidence['span_basis'] == 'normalized_text'
        assert 0 <= start < end <= len(normalized)
        assert normalized[start:end] == evidence['text']
        assert evidence['source_sha256'] == data['source_sha256']


def _pending_snapshot(data):
    return {k: data.get(k) for k in ('state', 'basis_sha256', 'clarification_revision',
            'analysis', 'parameters', 'holes', 'clarifications')}


def _wait_model(client, sid, timeout=180):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        data = _get(client, sid)
        if data['state'] == 'awaiting_adoption':
            return data
        if data['state'] in ('failed', 'interrupted', 'needs_clarification'):
            raise AssertionError(data)
        time.sleep(.1)
    raise AssertionError('clarified native model did not finish')


def _readback(client, data, tmp_path, expected_holes):
    response = client.get(data['files']['step'], headers=AUTH)
    assert response.status_code == 200, response.data
    step_bytes = response.data
    assert hashlib.sha256(step_bytes).hexdigest() == data['step_sha256']
    for kind in ('validation', 'drawform_validation', 'preview'):
        response = client.get(data['files'][kind], headers=AUTH)
        assert response.status_code == 200, response.data
        report = response.get_json()
        assert report['step_sha256'] == data['step_sha256']
        if kind != 'preview':
            assert report['passed']
        else:
            assert report['vertices'] and report['faces']
    path = tmp_path / (data['session_id'] + '.step')
    path.write_bytes(step_bytes)
    from drawform.geometry.loader import import_step
    from drawform.geometry.features import Bore, extract_features
    model = import_step(path)
    assert model.is_valid and model.solid_count == 1
    assert model.bbox == pytest.approx((100, 60, 8), abs=1e-5)
    assert model.origin == pytest.approx((-50, -30, 0), abs=1e-5)
    bores = [b for b in extract_features(model.shape, model.bbox) if isinstance(b, Bore)]
    assert len(bores) == len(expected_holes)
    for x, y, diameter in expected_holes:
        matches = [b for b in bores if math.dist(b.axis_offset()[:2], (x, y)) < 1e-6]
        assert len(matches) == 1
        assert matches[0].diameter == pytest.approx(diameter, abs=1e-6)
        assert matches[0].depth == pytest.approx(8, abs=1e-6) and matches[0].through
    return step_bytes, bores


def _adopt_export_draw(client, data, bores, key):
    sid = data['session_id']
    response = client.post(API + '/modeling/candidates/' + sid + '/adopt', headers=AUTH,
                           json={'expected_sha256': data['step_sha256']})
    assert response.status_code == 200, response.data
    export = client.post(API + '/modeling/candidates/' + sid + '/exports', headers=AUTH,
                         json={'format': 'step'})
    assert export.status_code == 200, export.data
    exported = export.get_json()
    assert exported['representation'] == 'native_brep' and exported['step_sha256'] == data['step_sha256']
    assert client.get(exported['download_url'], headers=AUTH).status_code == 200
    response = client.post(API + '/documents/' + sid + '/drawings',
                           headers={**AUTH, 'Idempotency-Key': key},
                           json={'step_sha256': data['step_sha256']})
    assert response.status_code == 202, response.data
    jid = response.get_json()['job_id']
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        response = client.get('/jobs/' + jid, headers=AUTH)
        assert response.status_code == 200
        job = response.get_json()
        if job['state'] not in ('queued', 'running'):
            break
        time.sleep(.1)
    assert job['state'] == 'drawn', job
    assert set(job['files']) == {'dxf', 'svg', 'pdf', 'json'}
    files = {}
    for kind, endpoint in job['files'].items():
        response = client.get(endpoint, headers=AUTH)
        assert response.status_code == 200 and response.data
        files[kind] = response.data
    assert files['pdf'].startswith(b'%PDF')
    document = json.loads(files['json'])
    sources = {}
    for dimension in document['dimensions']:
        if dimension['source']:
            sources[tuple(dimension['source'])] = dimension['value']
        sources.update((tuple(source), value) for source, value in
                       zip(dimension['shared_sources'], dimension['shared_values']))
    from drawform.geometry.features import group_bores
    for group in group_bores(bores):
        assert sources[(group.group_id, 'diameter')] == pytest.approx(group.diameter)
    for bore in bores:
        assert sources[(bore.feature_id, 'x')] == pytest.approx(bore.axis_offset()[0] + 50)
        assert sources[(bore.feature_id, 'y')] == pytest.approx(bore.axis_offset()[1] + 30)
    native = list(ezdxf.read(io.StringIO(files['dxf'].decode())).modelspace().query('DIMENSION'))
    assert len(native) == len(document['dimensions'])
    for entity, dimension in zip(native, document['dimensions']):
        assert entity.get_measurement() == pytest.approx(dimension['value'], abs=1e-5)
        if dimension['kind'] == 'diameter':
            assert entity.dxf.dimtype & 7 in (3, 4)
    return jid, job, files


def test_real_missing_thickness_answer_revision_and_source_integrity(tmp_path):
    work = tmp_path / 'work'
    app = create_app(work, token=TOKENS)
    try:
        client = app.test_client()
        pending = _create_pending(client, MISSING_THICKNESS, 'main-text')
        sid = pending['session_id']
        _assert_text_evidence(pending, MISSING_THICKNESS)
        assert [q['parameter'] for q in pending['questions']] == ['thickness_mm']
        original_facts = json.loads(json.dumps(_source_facts(pending)))
        initial = _pending_snapshot(pending)
        answers_url = API + '/modeling/sessions/' + sid + '/answers'
        valid_body = _answer_body(pending, {'thickness_mm': 8})
        assert client.get(API + '/modeling/sessions/' + sid, headers=OTHER).status_code == 404
        assert client.post(answers_url, headers={**OTHER, 'Idempotency-Key': 'other-seat'}, json=valid_body).status_code == 404
        for index, invalid in enumerate((True, 0, -1, '8+1', float('nan'), float('inf'), 10**400)):
            response = client.post(answers_url, headers={**AUTH, 'Idempotency-Key': f'bad-number-{index}'},
                                   json=_answer_body(pending, {'thickness_mm': invalid}))
            assert response.status_code == 422, response.data
            assert _pending_snapshot(_get(client, sid)) == initial
        for index, body in enumerate(({**valid_body, 'basis_sha256': '0'*64},
                                      {**valid_body, 'revision': pending['clarification_revision'] + 1})):
            assert client.post(answers_url, headers={**AUTH, 'Idempotency-Key': f'stale-{index}'}, json=body).status_code == 409
        known_override = {**valid_body, 'answers': {'length_mm': 120}}
        assert client.post(answers_url, headers={**AUTH, 'Idempotency-Key': 'cannot-override-known'}, json=known_override).status_code == 422
        qid = next(iter(valid_body['answers']))
        duplicate_json = ('{"basis_sha256":' + json.dumps(pending['basis_sha256']) +
            ',"revision":' + str(pending['clarification_revision']) + ',"answers":{' +
            json.dumps(qid) + ':8,' + json.dumps(qid) + ':9}}')
        assert client.post(answers_url, headers={**AUTH, 'Idempotency-Key': 'duplicate-json'},
                           data=duplicate_json, content_type='application/json').status_code == 422
        assert _pending_snapshot(_get(client, sid)) == initial
        _assert_no_native_artifacts(work)

        _, answer_body = _answer(client, pending, {'thickness_mm': 8}, 'thickness-once')
        replay_headers = {**AUTH, 'Idempotency-Key': 'thickness-once'}
        assert client.post(answers_url, headers=replay_headers, json=answer_body).status_code == 200
        changed_body = {**answer_body, 'answers': {qid: 9}}
        assert client.post(answers_url, headers=replay_headers, json=changed_body).status_code == 409
        assert client.post(answers_url, headers={**AUTH, 'Idempotency-Key': 'late-old-basis'}, json=answer_body).status_code == 409
        parent = _wait_model(client, sid)
        assert parent['spec'] == {'schema_version': 2, 'kind': 'plate',
            'parameters': {'length_mm': 100., 'width_mm': 60., 'thickness_mm': 8.},
            'holes': [{'x_mm': 0., 'y_mm': 0., 'diameter_mm': 20.}]}
        assert _source_facts(parent) == original_facts
        assert len(_user_facts(parent)) == 1 and _user_facts(parent)[0]['value'] == 8
        original_step, bores = _readback(client, parent, tmp_path, [(0, 0, 20)])
        parent_job, parent_job_data, parent_files = _adopt_export_draw(client, parent, bores, 'draw-original-text')
        parent = _get(client, sid)
        version_body = {'expected_step_sha256': parent['step_sha256'],
            'expected_spec_sha256': parent['model_version']['spec_sha256'],
            'expected_revision': parent['model_version']['latest_number'],
            'changes': [{'op': 'set_hole', 'hole_id': 'hole_1', 'diameter_mm': 12}]}
        response = client.post(API + '/modeling/sessions/' + sid + '/revisions',
            headers={**AUTH, 'Idempotency-Key': 'text-design-variant'}, json=version_body)
        assert response.status_code == 201, response.data
        child = _wait_model(client, response.get_json()['session_id'])
        assert child['text'] == MISSING_THICKNESS
        assert _source_facts(child) == original_facts and _user_facts(child) == _user_facts(parent)
        assert child['editable_spec']['holes'] == [{'x_mm': 0., 'y_mm': 0., 'diameter_mm': 12.}]
        assert child['analysis']['holes'][0]['diameter_mm'] == 20
        assert child['model_version']['parent_session_id'] == sid and child['model_version']['mode'] == 'design_variant'
        assert client.post(API + '/documents/' + child['session_id'] + '/drawings',
            headers={**AUTH, 'Idempotency-Key': 'unadopted-text-variant'},
            json={'step_sha256': child['step_sha256']}).status_code == 409
        child_step, bores = _readback(client, child, tmp_path, [(0, 0, 12)])
        child_job, child_job_data, child_files = _adopt_export_draw(client, child, bores, 'draw-text-variant')
        assert child_job != parent_job and child_step != original_step
        assert client.get(parent['files']['step'], headers=AUTH).data == original_step
        for kind, endpoint in parent_job_data['files'].items():
            assert client.get(endpoint, headers=AUTH).data == parent_files[kind]

        _stop(app)
        app = create_app(work, token=TOKENS)
        client = app.test_client()
        restored = _get(client, child['session_id'])
        assert restored['state'] == 'adopted' and restored['job_id'] == child_job
        assert _user_facts(restored) == _user_facts(parent)
        assert _get(client, sid)['job_id'] == parent_job
        assert client.get(restored['files']['step'], headers=AUTH).data == child_step
        assert client.get(parent['files']['step'], headers=AUTH).data == original_step
        for job, expected in ((parent_job_data, parent_files), (child_job_data, child_files)):
            for kind, endpoint in job['files'].items():
                assert client.get(endpoint, headers=AUTH).data == expected[kind]
        history = client.get(API + '/modeling/sessions/' + child['session_id'] + '/history', headers=AUTH).get_json()
        assert history['latest_number'] == 2 and len(history['versions']) == 2
        # The original UTF-8 source remains the authority for both versions.
        source_record = work / 'workbench/inputs' / (parent['bundle_id'] + '.json')
        stored = json.loads(source_record.read_text())
        stored['text'] = stored['text'] + ' changed'
        source_record.write_text(json.dumps(stored))
        assert client.post(answers_url, headers=replay_headers, json=answer_body).status_code == 409
        assert client.get(parent['files']['step'], headers=AUTH).status_code == 409
        assert client.get(restored['files']['step'], headers=AUTH).status_code == 409
    finally:
        _stop(app)


def test_real_multihole_partial_answers_keep_negative_and_zero_coordinates(tmp_path):
    text = '矩形板 长100毫米 宽60毫米，2个通孔；孔1 直径10毫米；孔2 X=20 Y=10。'
    work = tmp_path / 'work'
    app = create_app(work, token=TOKENS)
    try:
        client = app.test_client()
        data = _create_pending(client, text, 'partial-multihole')
        sid = data['session_id']
        _assert_text_evidence(data, text)
        assert {q['parameter'] for q in data['questions']} == {
            'thickness_mm', 'hole_origin', 'holes.0.x_mm', 'holes.0.y_mm', 'holes.1.diameter_mm'}
        source_facts = json.loads(json.dumps(_source_facts(data)))
        first, _ = _answer(client, data, {'thickness_mm': 8, 'hole_origin': 'plate_center',
            'holes.0.x_mm': -20, 'holes.0.y_mm': 0}, 'multihole-partial')
        assert first['state'] == 'needs_clarification'
        assert [q['parameter'] for q in first['questions']] == ['holes.1.diameter_mm']
        assert first['analysis']['holes'][0] == {'x_mm': -20., 'y_mm': 0., 'diameter_mm': 10.}
        assert _source_facts(first) == source_facts
        _assert_no_native_artifacts(work)
        _stop(app)
        app = create_app(work, token=TOKENS)
        client = app.test_client()
        restored = _get(client, sid)
        assert restored['state'] == 'needs_clarification'
        assert _pending_snapshot(restored) == _pending_snapshot(first)
        _assert_no_native_artifacts(work)
        _, last_body = _answer(client, restored, {'holes.1.diameter_mm': 12}, 'multihole-last')
        assert client.post(API + '/modeling/sessions/' + sid + '/answers',
            headers={**AUTH, 'Idempotency-Key': 'multihole-last'}, json=last_body).status_code == 200
        ready = _wait_model(client, sid)
        assert ready['spec']['holes'] == [{'x_mm': -20., 'y_mm': 0., 'diameter_mm': 10.},
                                         {'x_mm': 20., 'y_mm': 10., 'diameter_mm': 12.}]
        assert _source_facts(ready) == source_facts and len(_user_facts(ready)) == 5
        _, bores = _readback(client, ready, tmp_path, [(-20, 0, 10), (20, 10, 12)])
        _adopt_export_draw(client, ready, bores, 'draw-partial-multihole')
    finally:
        _stop(app)


def test_dynamic_center_hole_question_and_blind_text_never_build(tmp_path):
    work = tmp_path / 'work'
    app = create_app(work, token=TOKENS)
    try:
        client = app.test_client()
        data = _create_pending(client, '矩形板 长100毫米 宽60毫米 厚8毫米。', 'choose-hole-mode')
        assert [q['parameter'] for q in data['questions']] == ['hole_mode']
        assert {c['value'] for c in data['questions'][0]['choices']} == {'no_holes', 'center_through'}
        selected, _ = _answer(client, data, {'hole_mode': 'center_through'}, 'choose-center')
        assert selected['state'] == 'needs_clarification'
        assert [q['parameter'] for q in selected['questions']] == ['holes.0.diameter_mm']
        assert selected['analysis']['holes'] == [{'x_mm': 0., 'y_mm': 0., 'diameter_mm': None}]
        assert _user_facts(selected)[0]['value'] == 'center_through'
        _assert_no_native_artifacts(work)
        uploaded = _upload_text(client, MISSING_THICKNESS.replace('通孔', '盲孔'))
        refused = client.post(API + '/modeling/sessions',
            headers={**AUTH, 'Idempotency-Key': 'blind-is-not-through'},
            json={'bundle_id': uploaded['bundle_id'], 'sheet': 'A3'})
        assert refused.status_code == 422, refused.data
        _assert_no_native_artifacts(work)
    finally:
        _stop(app)

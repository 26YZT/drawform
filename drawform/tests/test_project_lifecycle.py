"""Project publication controls with synthetic persisted assets, without workers.

These tests exercise state, transactions and file binding only. Native CAD and
fresh-process restoration are separate acceptance checks, never implied here.
"""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
import threading
import uuid
import zipfile

import pytest

from drawform.service.app import create_app
from drawform.service.jobs import DRAWN, JobQueue
from drawform.workbench.modeling import save_json


TOKEN = 'project-lifecycle-control'
OTHER_TOKEN = 'project-lifecycle-other-owner'
AUTH = {'Authorization': 'Bearer ' + TOKEN}
OTHER_AUTH = {'Authorization': 'Bearer ' + OTHER_TOKEN}
API = '/api/workbench/v1'
TEXT = '矩形板长100宽60厚8毫米，中心直径20毫米通孔'
SPEC = {'schema_version': 2, 'kind': 'plate',
        'parameters': {'length_mm': 100., 'width_mm': 60., 'thickness_mm': 8.},
        'holes': [{'x_mm': 0., 'y_mm': 0., 'diameter_mm': 20.}]}


def sha(data):
    return hashlib.sha256(data).hexdigest()


class _NoWorker:
    def __init__(self, *args, **kwargs):
        pass

    def start(self):
        pass

    def join(self, timeout=None):
        pass


@pytest.fixture
def control(tmp_path, monkeypatch):
    """Create a Flask client, not an HTTP service; neither worker can start."""
    monkeypatch.setattr(JobQueue, 'start', lambda self: None)
    with monkeypatch.context() as patch:
        patch.setattr(threading, 'Thread', _NoWorker)
        app = create_app(tmp_path / 'work', token=TOKEN + ',' + OTHER_TOKEN)
    service = app.extensions['drawform_modeling']
    owner = sha(TOKEN.encode())
    yield app, service, owner
    service.stop()
    service.jobs.stop()


def _complete_model(service, sid, owner, data, payload):
    output = service.directory(sid) / 'artifacts'
    output.mkdir(parents=True, exist_ok=True)
    (output / 'model.step').write_bytes(payload)
    step_sha = sha(payload)
    for name in ('preview.json', 'validation.json', 'drawform-validation.json'):
        save_json(output / name, {'step_sha256': step_sha, 'passed': True,
                                  'spec': data['spec']})
    hashes = {kind: sha((output / name).read_bytes()) for kind, name in {
        'step': 'model.step', 'preview': 'preview.json',
        'validation': 'validation.json', 'drawform_validation': 'drawform-validation.json'}.items()}
    save_json(service.directory(sid) / 'manifest.json', {
        'schema_version': 1, 'parameters': data['parameters'],
        'text_sha256': data['text_sha256'], 'artifacts': hashes,
        'runtime': {'synthetic_transaction_test': True}, 'spec': data['spec'],
        'input_kind': data['input_kind'], 'source_sha256': data['source_sha256'],
        'revision_provenance': data.get('revision_provenance'),
        'human_review': 'not_reviewed'})
    service.update(sid, owner, 'awaiting_adoption', step_filename='model.step',
                   step_sha256=step_sha, artifact_hashes=hashes)
    service.adopt(sid, owner, step_sha)
    return service.get(sid, owner)


def _complete_drawing(service, sid, owner):
    data = service.get(sid, owner)
    bound = service.drawing(sid, owner, data['step_sha256'])
    job = service.jobs.get(bound['job_id'])
    formats = {
        'dxf': b'0\nSECTION\n2\nENTITIES\n0\nENDSEC\n0\nEOF\n',
        'svg': b'<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0 L100 0"/></svg>',
        'pdf': b'%PDF-1.4\n% synthetic publication-control fixture\n%%EOF\n',
        'json': json.dumps({'schema_version': 2, 'sheet': 'A3',
                            'part': {'bbox': [100., 60., 8.], 'origin': [0., 0., 0.], 'is_valid': True},
                            'views': [], 'dimensions': [], 'features': []}).encode()}
    files = []
    for kind, payload in formats.items():
        path = job.directory / (job.id + '.' + kind)
        path.write_bytes(payload)
        files.append((kind, path))
    service.jobs._set(job.id, state=DRAWN, files=tuple(files))
    return service.get(sid, owner)


def seed(control, *, drawing=False, with_job=True):
    app, service, owner = control
    sid = uuid.uuid4().hex
    if drawing:
        raw = b'0\nSECTION\n2\nHEADER\n0\nENDSEC\n0\nEOF\n'
        bundle = service.drawings.upload('source.dxf', raw, owner)
        text = ''
        source_sha = bundle['source_sha256']
    else:
        bundle = service.add_input(TEXT, 'mm', owner)
        text, source_sha = TEXT, bundle['text_sha256']
    data = {'session_id': sid, 'bundle_id': bundle['bundle_id'], 'text': text,
            'text_sha256': source_sha, 'source_sha256': source_sha,
            'input_kind': 'drawing' if drawing else 'text',
            'filename': 'source.dxf' if drawing else '', 'sheet': 'A3',
            'parameters': SPEC['parameters'], 'holes': SPEC['holes'], 'spec': SPEC,
            'model_kind': 'plate', 'candidate_id': sid,
            'created_at': '2026-10-18T00:00:00+00:00', 'job_id': None,
            'backend': 'local_simple_v2', 'error': '', 'human_review': 'not_reviewed'}
    if drawing:
        preview = service.directory(sid) / 'analysis/preview.png'
        preview.parent.mkdir(parents=True)
        preview.write_bytes(b'\x89PNG\r\n\x1a\nsynthetic-control-preview')
        data['source_preview_sha256'] = sha(preview.read_bytes())
        data['analysis'] = {'kind': 'plate', 'source_sha256': source_sha,
                            'parameters': SPEC['parameters'], 'holes': SPEC['holes'],
                            'units': 'mm', 'facts': [], 'questions': []}
        save_json(preview.parent / 'analysis.json', data['analysis'])
    with service.connect() as db:
        db.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?)',
                   (sid, owner, 'seed:' + sid, sha(sid.encode()), 'queued', json.dumps(data)))
        db.commit()
    result = _complete_model(service, sid, owner, data, b'ISO-10303-21; synthetic model ' + sid.encode())
    return _complete_drawing(service, sid, owner) if with_job else result


def child(control, parent, *, with_job=True):
    app, service, owner = control
    data = service.revisions.create(parent['session_id'], owner, {
        'expected_step_sha256': parent['step_sha256'],
        'expected_spec_sha256': parent['model_version']['spec_sha256'],
        'expected_revision': parent['model_version']['latest_number'],
        'changes': [{'op': 'set_parameter', 'parameter': 'thickness_mm', 'value_mm': 10.}]},
        'child-' + uuid.uuid4().hex)
    result = _complete_model(service, data['session_id'], owner, data,
                             b'ISO-10303-21; synthetic changed model ' + data['session_id'].encode())
    return _complete_drawing(service, data['session_id'], owner) if with_job else result


def payload(data, revision=0):
    return {'session_id': data['session_id'], 'expected_head_revision': revision,
            'expected_step_sha256': data['step_sha256']}


def publish(control, root, data, *, revision=0, key='publish-1', auth=AUTH):
    return control[0].test_client().post(API + '/projects/' + root + '/effective',
        headers={**auth, 'Idempotency-Key': key}, json=payload(data, revision))


def project(control, root):
    response = control[0].test_client().get(API + '/projects/' + root, headers=AUTH)
    assert response.status_code == 200, response.data
    return response.get_json()


def assert_success(response):
    assert response.status_code in (200, 201), response.data


def journal(control, root):
    with control[1].connect() as db:
        return [dict(row) for row in db.execute(
            'SELECT * FROM project_publications WHERE root_id=? ORDER BY rowid', (root,))]


def test_project_is_not_effective_merely_because_model_was_adopted(control):
    data = seed(control)
    state = project(control, data['session_id'])
    assert state['head_revision'] == 0 and state['effective'] is None
    response = control[0].test_client().get(API + '/projects/' + data['session_id'] + '/package', headers=AUTH)
    assert response.status_code == 409
    assert journal(control, data['session_id']) == []


def test_get_apis_do_not_create_project_rows_or_change_workspace_bytes(control):
    data = seed(control); root = data['session_id']; service = control[1]
    before = {p.relative_to(service.root.parent).as_posix(): p.read_bytes()
              for p in service.root.parent.rglob('*') if p.is_file()}
    with service.connect() as db:
        assert db.execute('SELECT count(*) FROM projects').fetchone()[0] == 0
    client = control[0].test_client()
    for suffix in ('/modeling/sessions/' + root, '/projects', '/projects/' + root,
                   '/projects/' + root + '/publications'):
        response = client.get(API + suffix, headers=AUTH)
        assert response.status_code == 200, response.data
    assert client.get(API + '/projects/' + root + '/package', headers=AUTH).status_code == 409
    after = {p.relative_to(service.root.parent).as_posix(): p.read_bytes()
             for p in service.root.parent.rglob('*') if p.is_file()}
    assert after == before
    with service.connect() as db:
        assert db.execute('SELECT count(*) FROM projects').fetchone()[0] == 0


def test_explicit_publication_binds_model_job_and_does_not_claim_human_review(control):
    data = seed(control); root = data['session_id']
    assert_success(publish(control, root, data))
    state = project(control, root)
    assert state['head_revision'] == 1
    assert state['effective']['session_id'] == root
    assert state['effective']['job_id'] == data['job_id']
    assert state['effective']['step_sha256'] == data['step_sha256']
    assert state['effective']['manifest_sha256']
    assert control[1].get(root, control[2])['human_review'] == 'not_reviewed'
    assert journal(control, root)[0]['state'] == 'complete'


def test_idempotency_same_request_never_advances_twice_and_changed_body_conflicts(control):
    data = seed(control); root = data['session_id']
    first = publish(control, root, data)
    assert_success(first)
    repeated = publish(control, root, data)
    assert_success(repeated)
    assert repeated.get_json() == first.get_json()
    assert project(control, root)['head_revision'] == 1 and len(journal(control, root)) == 1
    assert publish(control, root, data, revision=1).status_code == 409


def test_latest_candidate_does_not_move_effective_head_and_old_head_cannot_publish(control):
    parent = seed(control); root = parent['session_id']
    assert_success(publish(control, root, parent))
    variant = child(control, control[1].get(root, control[2]))
    assert project(control, root)['effective']['session_id'] == root
    assert publish(control, root, variant, key='stale-head').status_code == 409
    assert_success(publish(control, root, variant, revision=1, key='publish-2'))
    state = project(control, root)
    assert state['head_revision'] == 2 and state['effective']['session_id'] == variant['session_id']
    assert control[1].get(root, control[2])['state'] == 'adopted'


def test_concurrent_cas_has_one_winner_and_one_publication(control):
    data = seed(control); root = data['session_id']
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda key: publish(control, root, data, key=key), ('cas-a', 'cas-b')))
    assert sum(r.status_code in (200, 201) for r in responses) == 1
    assert sum(r.status_code == 409 for r in responses) == 1
    assert project(control, root)['head_revision'] == 1
    assert sum(row['state'] == 'complete' for row in journal(control, root)) == 1


def test_project_roots_are_independent_and_foreign_candidate_is_rejected(control):
    first, second = seed(control), seed(control)
    assert publish(control, first['session_id'], second).status_code == 409
    assert_success(publish(control, first['session_id'], first, key='same-key'))
    assert_success(publish(control, second['session_id'], second, key='same-key'))
    assert project(control, first['session_id'])['head_revision'] == 1
    assert project(control, second['session_id'])['head_revision'] == 1


def test_owner_boundary_and_auth_cover_read_publish_package_and_history(control):
    data = seed(control); root = data['session_id']; client = control[0].test_client()
    for suffix in ('', '/package', '/publications'):
        assert client.get(API + '/projects/' + root + suffix).status_code == 401
        assert client.get(API + '/projects/' + root + suffix, headers=OTHER_AUTH).status_code == 404
    assert publish(control, root, data, auth=OTHER_AUTH).status_code == 404
    listing = client.get(API + '/projects', headers=OTHER_AUTH)
    assert listing.status_code == 200 and root not in listing.get_data(as_text=True)
    assert journal(control, root) == []


@pytest.mark.parametrize('change', [
    {'expected_head_revision': True}, {'expected_head_revision': -1},
    {'expected_head_revision': '0'}, {'session_id': '../escape'},
    {'expected_step_sha256': 'invalid'}, {'unknown': 1},
])
def test_strict_publication_contract_creates_no_effective_head(control, change):
    data = seed(control); root = data['session_id']
    response = control[0].test_client().post(API + '/projects/' + root + '/effective',
        headers={**AUTH, 'Idempotency-Key': 'invalid'}, json={**payload(data), **change})
    # Unknown/malformed identities may share the non-enumerating 404 boundary.
    expected = (404, 409, 422) if 'session_id' in change else (409, 422)
    assert response.status_code in expected, response.data
    assert project(control, root)['head_revision'] == 0


def test_publication_requires_request_key_and_rejects_duplicate_json_fields(control):
    data = seed(control); root = data['session_id']; client = control[0].test_client()
    url = API + '/projects/' + root + '/effective'
    assert client.post(url, headers=AUTH, json=payload(data)).status_code in (422, 428)
    raw = json.dumps(payload(data)).replace('"expected_head_revision": 0',
        '"expected_head_revision": 0, "expected_head_revision": 1')
    response = client.post(url, headers={**AUTH, 'Idempotency-Key': 'duplicate'},
                           data=raw, content_type='application/json')
    assert response.status_code == 422
    assert project(control, root)['head_revision'] == 0


@pytest.mark.parametrize('kind', ['step', 'preview', 'validation', 'drawform_validation'])
def test_missing_model_member_blocks_publication_without_changing_old_head(control, kind):
    data = seed(control); root = data['session_id']
    assert_success(publish(control, root, data))
    variant = child(control, control[1].get(root, control[2]))
    control[1].file(variant['session_id'], control[2], kind).unlink()
    response = publish(control, root, variant, revision=1, key='missing-' + kind)
    assert response.status_code == 409, response.data
    assert project(control, root)['effective']['session_id'] == root


@pytest.mark.parametrize('kind', ['dxf', 'svg', 'pdf', 'json'])
def test_missing_drawing_member_blocks_publication(control, kind):
    data = seed(control); job = control[1].jobs.get(data['job_id'])
    dict(job.files)[kind].unlink()
    response = publish(control, data['session_id'], data)
    assert response.status_code == 409, response.data
    assert project(control, data['session_id'])['head_revision'] == 0


def test_unfinished_drawing_and_unadopted_model_cannot_be_project_effective(control):
    data = seed(control, with_job=False); root = data['session_id']
    assert publish(control, root, data, key='no-drawing').status_code == 409
    data = _complete_drawing(control[1], root, control[2])
    control[1].update(root, control[2], 'awaiting_adoption')
    assert publish(control, root, data, key='not-adopted').status_code == 409
    assert project(control, root)['head_revision'] == 0


@pytest.mark.parametrize('damage', ['job-bytes', 'job-record', 'drawing-source', 'drawing-source-record'])
def test_step_and_original_drawing_source_binding_cannot_be_mixed(control, damage):
    data = seed(control, drawing=True); service = control[1]; root = data['session_id']
    job = service.jobs.get(data['job_id'])
    if damage == 'job-bytes':
        job.source.write_bytes(b'another model STEP')
    elif damage == 'job-record':
        service.jobs._set(job.id, source_sha256='0' * 64)
    elif damage == 'drawing-source':
        (service.root / 'inputs' / data['bundle_id'] / 'source.dxf').write_bytes(b'changed drawing')
    else:
        service.update(root, control[2], 'adopted', source_sha256='0' * 64)
    response = publish(control, root, data)
    assert response.status_code == 409, response.data
    assert project(control, root)['head_revision'] == 0


def test_dxf_only_amendment_cannot_be_bundled_with_stale_companions(control):
    data = seed(control); service = control[1]; root = data['session_id']
    job = service.jobs.get(data['job_id'])
    base = dict(job.files)['dxf'].read_bytes()
    service.jobs.put_version(job.id, base + b'999\nmanual dxf-only change\n', sha(base))
    response = publish(control, root, data)
    assert response.status_code == 409, response.data
    assert project(control, root)['head_revision'] == 0


@pytest.mark.parametrize('filename', ['manifest.json', 'adoption.json', 'drawing-binding.json'])
def test_changed_model_sidecar_cannot_enter_a_project_publication(control, filename):
    data = seed(control); root = data['session_id']; service = control[1]
    path = service.directory(root) / filename
    saved = json.loads(path.read_text())
    if filename == 'manifest.json':
        saved['artifacts']['step'] = '0' * 64
    else:
        saved['step_sha256'] = '0' * 64
    save_json(path, saved)
    response = publish(control, root, data)
    assert response.status_code == 409, response.data
    assert project(control, root)['head_revision'] == 0


@pytest.mark.parametrize('damage', ['missing', 'request'])
def test_revision_receipt_must_match_saved_lineage_before_publication(control, damage):
    data = seed(control); root = data['session_id']; service = control[1]
    variant = child(control, service.get(root, control[2]))
    path = service.directory(variant['session_id']) / 'revision.json'
    if damage == 'missing':
        path.unlink()
    else:
        receipt = json.loads(path.read_text())
        receipt['request']['changes'][0]['value_mm'] = 99.
        save_json(path, receipt)
    response = publish(control, root, variant)
    assert response.status_code == 409, response.data
    assert project(control, root)['head_revision'] == 0


def test_symlinked_artifact_is_rejected_without_reading_or_changing_its_target(control):
    data = seed(control); root = data['session_id']; service = control[1]
    path = service.file(root, control[2], 'preview')
    outside = service.root.parent.parent / 'outside-preview.json'
    original = path.read_bytes()
    outside.write_bytes(original)
    path.unlink(); path.symlink_to(outside)
    response = publish(control, root, data)
    assert response.status_code == 409, response.data
    assert outside.read_bytes() == original
    assert project(control, root)['head_revision'] == 0


@pytest.mark.parametrize('stage', ['prepared', 'captured', 'committed'])
def test_durable_journal_fault_retry_never_loses_or_double_advances_head(control, monkeypatch, stage):
    data = seed(control); service = control[1]; root = data['session_id']
    lifecycle = service.projects
    original = lifecycle._checkpoint
    visited = []

    def checkpoint(current):
        visited.append(current)
        if current == stage:
            rows = journal(control, root)
            assert len(rows) == 1, 'Journal must be durable before checkpoint side effects'
            assert project(control, root)['head_revision'] == (1 if stage == 'committed' else 0)
            raise OSError('injected project ' + current + ' failure')
        return original(current)

    monkeypatch.setattr(lifecycle, '_checkpoint', checkpoint)
    response = publish(control, root, data)
    assert response.status_code == 500, response.data
    assert stage in visited
    assert project(control, root)['head_revision'] == (1 if stage == 'committed' else 0)
    monkeypatch.setattr(lifecycle, '_checkpoint', original)
    assert_success(publish(control, root, data))
    assert project(control, root)['head_revision'] == 1
    rows = journal(control, root)
    assert len(rows) == 1 and rows[0]['state'] == 'complete'


@pytest.mark.parametrize('stage', ['prepared', 'captured'])
def test_startup_recovers_uncommitted_journal_without_promoting_head(control, monkeypatch, stage):
    data = seed(control); root = data['session_id']; service = control[1]

    class SimulatedProcessDeath(BaseException):
        pass

    def terminate(current):
        if current == stage:
            raise SimulatedProcessDeath(current)

    monkeypatch.setattr(service.projects, '_checkpoint', terminate)
    with pytest.raises(SimulatedProcessDeath):
        service.projects.publish(root, control[2], payload(data), 'interrupted-publish')
    assert project(control, root)['head_revision'] == 0
    assert len(journal(control, root)) == 1
    service.projects = type(service.projects)(service)
    assert project(control, root)['head_revision'] == 0
    assert journal(control, root)[0]['state'] in ('interrupted', 'failed')
    assert_success(publish(control, root, data, key='interrupted-publish'))
    assert project(control, root)['head_revision'] == 1
    assert len(journal(control, root)) == 1


def test_packages_are_immutable_versioned_and_verifiable(control):
    from drawform.workbench.project_package import verify_package
    data = seed(control); root = data['session_id']; service = control[1]
    assert_success(publish(control, root, data))
    first = service.projects.package(root, control[2], revision=1).getvalue()
    assert verify_package(io.BytesIO(first))['verified'] is True
    variant = child(control, service.get(root, control[2]))
    assert_success(publish(control, root, variant, revision=1, key='second'))
    assert service.projects.package(root, control[2], revision=1).getvalue() == first
    second = service.projects.package(root, control[2]).getvalue()
    assert second != first and verify_package(io.BytesIO(second))['verified'] is True
    with zipfile.ZipFile(io.BytesIO(second)) as archive:
        names = archive.namelist()
        assert 'manifest.json' in names
        assert any(name.endswith('.step') for name in names)
        assert all(any(name.endswith('.' + kind) for name in names) for kind in ('dxf', 'svg', 'pdf', 'json'))
    client = control[0].test_client()
    downloaded = client.get(API + '/projects/' + root + '/package?revision=1', headers=AUTH)
    assert downloaded.status_code == 200 and downloaded.data == first
    assert client.get(API + '/projects/' + root + '/publications', headers=AUTH).status_code == 200


def test_saved_snapshot_is_independent_of_later_live_artifact_corruption(control):
    data = seed(control); root = data['session_id']; service = control[1]
    assert_success(publish(control, root, data))
    before = service.projects.package(root, control[2]).getvalue()
    service.file(root, control[2], 'step').write_bytes(b'changed after immutable publication')
    assert service.projects.package(root, control[2], revision=1).getvalue() == before


def test_package_bytes_do_not_depend_on_the_download_wall_clock(control, monkeypatch):
    data = seed(control); root = data['session_id']; service = control[1]
    assert_success(publish(control, root, data))
    with monkeypatch.context() as patch:
        patch.setattr(zipfile.time, 'localtime', lambda *args: (2026, 1, 1, 0, 0, 0, 0, 1, -1))
        first = service.projects.package(root, control[2]).getvalue()
        patch.setattr(zipfile.time, 'localtime', lambda *args: (2028, 2, 2, 12, 30, 0, 0, 1, -1))
        second = service.projects.package(root, control[2]).getvalue()
    assert first == second


@pytest.mark.parametrize('damage', ['bytes', 'extra', 'traversal', 'duplicate'])
def test_independent_verifier_rejects_zip_tampering(control, damage):
    from drawform.workbench.project_package import verify_package
    data = seed(control); root = data['session_id']; service = control[1]
    assert_success(publish(control, root, data))
    original = service.projects.package(root, control[2]).getvalue()
    changed = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(original)) as source, zipfile.ZipFile(changed, 'w') as output:
        entries = source.infolist()
        target = next(entry.filename for entry in entries if entry.filename.endswith('.step'))
        for entry in entries:
            content = source.read(entry.filename)
            if damage == 'bytes' and entry.filename == target:
                content += b'tampered'
            output.writestr(entry.filename, content)
        if damage in ('extra', 'traversal'):
            output.writestr('unlisted.txt' if damage == 'extra' else '../escape.txt', b'untrusted')
        elif damage == 'duplicate':
            with pytest.warns(UserWarning, match='Duplicate name'):
                output.writestr(target, source.read(target))
    changed.seek(0)
    with pytest.raises((ValueError, OSError)):
        verify_package(changed)


def test_verifier_rejects_old_pdf_relabelled_as_the_current_drawing_edition(control):
    """Matching file hashes alone cannot prove the four files share an edition."""
    from drawform.workbench.project_package import verify_package
    data = seed(control); root = data['session_id']; service = control[1]
    assert_success(publish(control, root, data))
    variant = child(control, service.get(root, control[2]))
    assert_success(publish(control, root, variant, revision=1, key='new-version'))
    original = service.projects.package(root, control[2]).getvalue()
    changed = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(original)) as source, zipfile.ZipFile(changed, 'w') as output:
        manifest = json.loads(source.read('manifest.json'))
        previous_pdf = 'payload/' + data['job_id'] + '/' + data['job_id'] + '.pdf'
        assert previous_pdf in source.namelist()
        old_entry = next(entry for entry in manifest['files'] if entry['path'] == previous_pdf)
        selected_entry = next(entry for entry in manifest['files']
                              if entry['path'] == manifest['selected_artifacts']['pdf'])
        manifest['selected_artifacts']['pdf'] = previous_pdf
        # Recompute the package's self-declared labels. The authoritative Job
        # record still points at the newer PDF, so a semantic verifier must fail.
        old_entry.update({key: value for key, value in selected_entry.items()
                          if key not in ('path', 'sha256', 'bytes')})
        for entry in source.infolist():
            content = (json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode()
                       if entry.filename == 'manifest.json' else source.read(entry.filename))
            output.writestr(entry.filename, content)
    changed.seek(0)
    with pytest.raises(ValueError):
        verify_package(changed)


@pytest.mark.parametrize('damage', ['original-drawing-bytes', 'missing-parent-step'])
def test_verifier_checks_source_and_historical_model_dependencies_even_after_rehash(control, damage):
    from drawform.workbench.project_package import verify_package
    data = seed(control, drawing=damage == 'original-drawing-bytes')
    root = data['session_id']; service = control[1]
    variant = child(control, service.get(root, control[2]))
    assert_success(publish(control, root, variant))
    original = service.projects.package(root, control[2]).getvalue()
    changed = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(original)) as source, zipfile.ZipFile(changed, 'w') as output:
        manifest = json.loads(source.read('manifest.json'))
        if damage == 'original-drawing-bytes':
            target = 'payload/workbench/inputs/' + data['bundle_id'] + '/source.dxf'
            replacement = source.read(target) + b'999\nchanged original drawing\n'
            entry = next(item for item in manifest['files'] if item['path'] == target)
            entry.update(bytes=len(replacement), sha256=sha(replacement))
        else:
            target = 'payload/workbench/sessions/' + root + '/artifacts/model.step'
            manifest['files'] = [entry for entry in manifest['files'] if entry['path'] != target]
        for entry in source.infolist():
            if damage == 'missing-parent-step' and entry.filename == target:
                continue
            if entry.filename == 'manifest.json':
                content = json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode()
            elif damage == 'original-drawing-bytes' and entry.filename == target:
                content = replacement
            else:
                content = source.read(entry.filename)
            output.writestr(entry.filename, content)
    changed.seek(0)
    with pytest.raises(ValueError):
        verify_package(changed)

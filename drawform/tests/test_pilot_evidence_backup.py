"""外置台账不能掉出首批证据的备份/恢复链。"""

import hashlib
import json

import pytest

from drawform.eval.pilot import create_book, register_case
from drawform.service import maintenance, operations
from tests.test_local_maintenance import JOB, work, content


@pytest.fixture
def ledger(work,tmp_path):
    path=tmp_path/'external-book.json'
    create_book(path,title='台账备份夹具',source='rehearsal')
    register_case(path,work,JOB,'part')
    return path


def test_external_ledger_is_hashed_restored_and_original_never_moved(work,ledger,tmp_path):
    before=content(work)
    book_bytes=ledger.read_bytes()
    backup,restored=tmp_path/'backup',tmp_path/'restored'
    maintenance.backup(work,backup,book=ledger)
    manifest=json.loads((backup/'manifest.json').read_text())
    record=next(e for e in manifest['files'] if e['path']==maintenance.PILOT_LEDGER)
    assert record['sha256']==hashlib.sha256(book_bytes).hexdigest()
    maintenance.restore(backup,restored)
    assert (restored/maintenance.PILOT_LEDGER).read_bytes()==book_bytes
    assert content(work)==before and ledger.read_bytes()==book_bytes


def test_first_cohort_missing_step_is_rejected_before_creating_backup(work,ledger,tmp_path):
    (work/JOB/'model.step').unlink()
    with pytest.raises(ValueError,match='STEP'):
        maintenance.backup(work,tmp_path/'backup',book=ledger)
    assert not (tmp_path/'backup').exists()


def test_matching_manifest_hash_cannot_hide_ledger_step_mismatch(work,ledger,tmp_path):
    backup=tmp_path/'backup'
    maintenance.backup(work,backup,book=ledger)
    file=backup/'data'/maintenance.PILOT_LEDGER
    book=json.loads(file.read_text());book['cases'][0]['step_sha256']='0'*64
    file.write_text(json.dumps(book))
    path=backup/'manifest.json';manifest=json.loads(path.read_text())
    entry=next(e for e in manifest['files'] if e['path']==maintenance.PILOT_LEDGER)
    entry.update(bytes=file.stat().st_size,sha256=hashlib.sha256(file.read_bytes()).hexdigest())
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError,match='指纹'):
        maintenance.restore(backup,tmp_path/'restored')
    assert not (tmp_path/'restored').exists()


def test_book_changing_during_copy_has_no_valid_manifest(work,ledger,tmp_path,monkeypatch):
    copy=maintenance._copy
    def changing(*args):
        copy(*args)
        ledger.write_text(ledger.read_text()+'\n')
    monkeypatch.setattr(maintenance,'_copy',changing)
    with pytest.raises(ValueError,match='台账发生变化'):
        maintenance.backup(work,tmp_path/'backup',book=ledger)
    assert not (tmp_path/'backup/manifest.json').exists()


def test_a_business_only_backup_cannot_claim_to_be_a_trial_restore(work,tmp_path):
    backup=tmp_path/'backup'
    maintenance.backup(work,backup)
    with pytest.raises(ValueError,match='未包含试点台账'):
        operations.storage_action('restore-check',backup,tmp_path/'restored',tmp_path/'ops/state.json')
    assert not (tmp_path/'restored').exists()

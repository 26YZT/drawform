import json
from pathlib import Path
import hashlib
import pytest
from drawform.service.jobs import JobQueue


def test_submission_is_idempotent_before_and_after_restart(tmp_path):
    source=tmp_path/'input.step';source.write_bytes(b'ISO-10303-21; immutable test source')
    sha=hashlib.sha256(source.read_bytes()).hexdigest();work=tmp_path/'work'
    first=JobQueue(work)
    job=first.submit_once(source,request_id='model:request1',expected_sha=sha,sheet='A3')
    assert first.submit_once(source,request_id='model:request1',expected_sha=sha,sheet='A3')==job
    restored=JobQueue(work)
    assert restored.submit_once(source,request_id='model:request1',expected_sha=sha,sheet='A3')==job
    assert len(restored.listing())==1
    with pytest.raises(ValueError):restored.submit_once(source,request_id='model:request1',expected_sha=sha,sheet='A2')


def test_prepared_submission_failure_reuses_allocated_job(tmp_path,monkeypatch):
    source=tmp_path/'input.step';source.write_bytes(b'controlled source')
    sha=hashlib.sha256(source.read_bytes()).hexdigest();work=tmp_path/'work'
    q=JobQueue(work)
    def fail(*args):raise OSError('injected record failure')
    monkeypatch.setattr(q,'_record',fail)
    with pytest.raises(OSError):q.submit_once(source,request_id='req',expected_sha=sha)
    receipts=list((work/'job-submissions').glob('*.json'));assert len(receipts)==1
    planned=json.loads(receipts[0].read_text())['job_id']
    new=JobQueue(work)
    assert new.submit_once(source,request_id='req',expected_sha=sha)==planned
    assert len(new.listing())==1

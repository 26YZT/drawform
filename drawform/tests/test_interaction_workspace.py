"""Workspace metadata and review submission safety, without running OCCT."""
import hashlib
import io
import json
import threading
from dataclasses import replace

import pytest

from drawform.review import ReviewLog
from drawform.service.app import create_app
from drawform.service.interaction import (ReviewConflict, display_name, metadata,
    submit_review, task_context)
from drawform.service.jobs import Job, JobQueue

AUTH = {"Authorization": "Bearer workspace-test"}


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    queues = []
    monkeypatch.setattr(JobQueue, "start", lambda self: queues.append(self))
    work = tmp_path / "work"
    app = create_app(work, token="QA=workspace-test")
    app.testing = True
    queue = queues[-1]
    directory = work / "fixture01"
    directory.mkdir(parents=True)
    source = directory / "model.step"
    source.write_bytes(b"isolated source fixture")
    dxf = directory / "draft.dxf"
    # Publication API test data need not invoke a geometry kernel.
    dxf.write_bytes(b"original DXF fixture")
    job = Job("fixture01", "drawn", directory, source, files=(("dxf", dxf),),
              display_name="联轴器.step", created_at="2026-10-15T00:00:00+00:00",
              source_sha256=hashlib.sha256(source.read_bytes()).hexdigest())
    with queue._lock:
        queue._jobs[job.id] = job
        queue._record(job)
    return app.test_client(), queue, job, work


def bound_body(job, **changes):
    context = task_context(job)
    return {"decision": "accepted", "reviewer": "隔离交互验证",
            "reviewed_sha": context["effective_sha"], "reviewed_version": context["head_version"],
            "submission_id": "fixture-review-00001", **changes}


def test_filename_is_display_only_and_persists_across_restart(workspace):
    http, queue, _, work = workspace
    response = http.post("/jobs", headers=AUTH,
        data={"step": (io.BytesIO(b"test"), "../../folder/联轴器.step")})
    assert response.status_code == 202
    job = queue.get(response.json["job_id"])
    assert job.display_name == "联轴器.step"
    assert job.source.name == "model.step"
    assert job.created_at and job.source_sha256 == hashlib.sha256(b"test").hexdigest()
    restored = JobQueue(work).get(job.id)
    assert (restored.display_name, restored.created_at, restored.source_sha256) == (
        job.display_name, job.created_at, job.source_sha256)
    assert display_name("..\\evil\x00\u202e.step") == "evil.step"


def test_detail_is_opt_in_and_read_only_with_honest_legacy_metadata(workspace):
    http, queue, job, work = workspace
    original = (work / "jobs.jsonl").read_bytes()
    assert set(http.get("/jobs", headers=AUTH).json["jobs"][0]) == {"job_id", "state", "failed"}
    rich = http.get(f"/jobs/{job.id}?detail=1", headers=AUTH).json
    assert rich["display_name"] == "联轴器.step"
    assert rich["context"]["head_version"] == 0 and rich["context"]["source_valid"]
    assert "context" not in http.get(f"/jobs/{job.id}", headers=AUTH).json
    legacy = replace(job, display_name="", created_at="", source_sha256="")
    assert metadata(legacy) == {"display_name": "", "created_at": "", "name_origin": "unknown"}
    known = {hashlib.sha256(job.source.read_bytes()).hexdigest(): "catalog.step"}
    assert metadata(legacy, known)["name_origin"] == "verified_catalog"
    job.source.write_bytes(b"changed source")
    assert not task_context(job)["source_valid"]
    assert metadata(legacy, known)["display_name"] == ""
    assert (work / "jobs.jsonl").read_bytes() == original


def test_stale_review_rejected_without_audit_and_current_bound_review_records_version_zero(workspace):
    http, queue, job, work = workspace
    body = bound_body(job)
    wrong = {**body, "reviewed_sha": "0" * 64}
    rejected = http.post(f"/jobs/{job.id}/review", json=wrong, headers=AUTH)
    assert rejected.status_code == 409 and rejected.json["reason"] == "stale_base"
    assert not (work / "reviews.jsonl").exists()
    row = http.post(f"/jobs/{job.id}/review", json=body, headers=AUTH).json["review"]
    assert row["client_bound"] and row["reviewed_version"] == 0
    assert row["reviewed_sha"] == body["reviewed_sha"]
    assert "effective_sha" not in row  # Ordinary review must not masquerade as a CAD upload.


def test_retry_survives_restart_without_duplicating_and_changed_payload_conflicts(workspace):
    http, queue, job, work = workspace
    body = bound_body(job)
    first = http.post(f"/jobs/{job.id}/review", json=body, headers=AUTH)
    assert first.status_code == 201
    persisted = (work / "reviews.jsonl").read_bytes()
    restarted = create_app(work, token="QA=workspace-test").test_client()
    assert restarted.post(f"/jobs/{job.id}/review", json=body, headers=AUTH).json == first.json
    assert (work / "reviews.jsonl").read_bytes() == persisted
    conflict = restarted.post(f"/jobs/{job.id}/review", json={**body, "note": "different"}, headers=AUTH)
    assert conflict.status_code == 409
    assert (work / "reviews.jsonl").read_bytes() == persisted


@pytest.mark.parametrize("value", [1, True, {}, [{}], [["wrong_value"]], "wrong_value"])
def test_malformed_reasons_are_400_without_any_audit(workspace, value):
    http, _, job, work = workspace
    response = http.post(f"/jobs/{job.id}/review", json=bound_body(job, reasons=value), headers=AUTH)
    assert response.status_code == 400
    assert not (work / "reviews.jsonl").exists()


@pytest.mark.parametrize("value", [None, [], 0, ""])
def test_invalid_present_idempotency_key_cannot_silently_disable_deduplication(workspace, value):
    http, _, job, work = workspace
    response = http.post(f"/jobs/{job.id}/review", json=bound_body(job, submission_id=value), headers=AUTH)
    assert response.status_code == 400
    assert not (work / "reviews.jsonl").exists()


def test_legacy_review_still_works_and_new_submission_is_seat_scoped(workspace):
    http, queue, job, work = workspace
    assert http.post(f"/jobs/{job.id}/review", json={"decision": "accepted", "reviewer": "legacy"}, headers=AUTH).status_code == 201
    body = bound_body(job)
    a = submit_review(queue, queue._audit, job.id, body, "seat-a")
    b = submit_review(queue, queue._audit, job.id, body, "seat-b")
    assert a["token_hint"] != b["token_hint"]
    assert len(queue._audit.for_job(job.id)) == 3


def test_short_token_fingerprint_collision_does_not_merge_review_submissions(workspace):
    _, queue, job, _ = workspace
    tokens = ["drawform-scope-demo-132316", "drawform-scope-demo-170021"]
    hashes = [hashlib.sha256(t.encode()).hexdigest() for t in tokens]
    assert hashes[0][:8] == hashes[1][:8] and hashes[0] != hashes[1]
    rows = [submit_review(queue, queue._audit, job.id, bound_body(job), sha[:8], "QA", sha)
            for sha in hashes]
    assert rows[0]["submission_scope"] != rows[1]["submission_scope"]
    assert len(queue._audit.for_job(job.id)) == 2


def test_legacy_reference_source_hash_invalidates_changed_source_context(workspace):
    _, _, job, _ = workspace
    legacy = replace(job, source_sha256="", reference_snapshot={"source_sha256": job.source_sha256})
    assert task_context(legacy)["source_valid"]
    job.source.write_bytes(b"replacement")
    assert not task_context(legacy)["source_valid"]


def test_review_and_version_freeze_are_serialized_under_same_guard(workspace):
    _, queue, job, _ = workspace
    entered, release, attempted, finished = (threading.Event() for _ in range(4))
    errors = []
    old = bound_body(job)
    def publish():
        with queue.review_guard():
            queue.put_version(job.id, b"changed DXF", old["reviewed_sha"])
            entered.set()
            assert release.wait(3)
            queue._audit.record(job_id=job.id, decision="amended", reviewer="fixture",
                                reasons=["wrong_value"])
    def review():
        attempted.set()
        try:
            submit_review(queue, queue._audit, job.id, old, "fixture")
        except ReviewConflict as exc:
            errors.append(exc.context)
        finally:
            finished.set()
    writer = threading.Thread(target=publish)
    reader = threading.Thread(target=review)
    writer.start()
    assert entered.wait(3)
    reader.start()
    assert attempted.wait(3) and not finished.wait(.05)
    release.set()
    writer.join(3); reader.join(3)
    assert finished.is_set() and errors == [{"reason": "stale_base", "head": 1}]
    assert [r["decision"] for r in queue._audit.for_job(job.id)] == ["amended"]

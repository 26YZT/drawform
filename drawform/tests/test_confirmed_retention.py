"""批准的 A 方案：数量及时间均不构成自动删除授权。"""

from datetime import datetime, timedelta, timezone
import json
import time

import pytest

from drawform.service.jobs import JobQueue
from drawform.service.retention import backup_plan, close_trial, evidence_plan, log_plan
from drawform.eval.pilot import create_book, register_case
from tests.test_pilot_records import add_job


def test_more_than_two_hundred_jobs_survive_new_render_and_restart(tmp_path, bearing_seat_step):
    work = tmp_path / "work"
    work.mkdir()
    rows = []
    for i in range(201):
        job = f"{i:012x}"
        folder = work / job
        folder.mkdir()
        (folder / "model.step").write_bytes(b"old source evidence")
        rows.append({"job": job, "state": "crashed", "source": "model.step", "files": []})
    (work / "jobs.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    queue = JobQueue(work)
    queue.start()
    try:
        job = queue.submit(bearing_seat_step)
        deadline = time.monotonic() + 30
        while not queue.get(job).finished and time.monotonic() < deadline:
            time.sleep(.05)
        assert queue.get(job).state == "drawn"
        assert len(queue.listing()) == 202
    finally:
        queue.stop()
    assert len(JobQueue(work).listing()) == 202
    assert all((work / row["job"] / "model.step").read_bytes() == b"old source evidence" for row in rows)


@pytest.fixture
def registered(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    book = tmp_path / "book.json"
    create_book(book, title="留存演练", source="rehearsal")
    row = add_job(work)
    register_case(book, work, row["job"], "part")
    return book, work, row["job"]


def test_open_trial_never_expires_and_closed_trial_only_proposes_review(registered):
    book, work, job = registered
    before = {p: p.read_bytes() for p in work.rglob('*') if p.is_file()}
    far = datetime.now(timezone.utc) + timedelta(days=200)
    assert evidence_plan(book, work, now=far)["requires_manual_review"] == []
    close_trial(book, datetime.now(timezone.utc).isoformat(), "测试登记人")
    report = evidence_plan(book, work, now=far)
    assert report["requires_manual_review"] == [job]
    assert report["first_cohort"] == [job] and not report["deletion_authorized"]
    assert all(p.read_bytes() == data for p, data in before.items())


def test_trial_end_cannot_be_silently_overwritten(registered):
    book, work, job = registered
    ended = datetime.now(timezone.utc).isoformat()
    close_trial(book, ended, "甲")
    before = book.read_bytes()
    with pytest.raises(ValueError, match="覆盖"):
        close_trial(book, ended, "乙")
    assert book.read_bytes() == before


def test_missing_evidence_is_reported_instead_of_removed_from_cohort(registered):
    book, work, job = registered
    import shutil
    shutil.rmtree(work / job)  # 本测试的临时夹具；产品无删除操作。
    report = evidence_plan(book, work)
    assert report["missing_evidence"] == report["protected_jobs"] == [job]


def test_backup_rotation_is_a_plan_and_holds_archive(tmp_path):
    for kind, number in (("daily", 9), ("weekly", 6)):
        for i in range(number):
            p = tmp_path / kind / str(i)
            p.mkdir(parents=True)
            (p / "manifest.json").write_text(json.dumps({"created_at": f"2026-10-{i+1:02}T00:00:00+00:00"}))
    result = backup_plan(tmp_path)
    assert len(result["keep"]) == 11 and len(result["requires_manual_review"]) == 4
    assert len(list(tmp_path.rglob('manifest.json'))) == 15
    assert str(tmp_path / 'archive') in result['held_outside_rotation']


def test_audit_directory_cannot_be_treated_as_ordinary_logs(tmp_path):
    with pytest.raises(ValueError, match="业务审计"):
        log_plan(tmp_path, tmp_path / "work")


def test_ordinary_logs_are_only_listed_after_thirty_days(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    old = logs / "server.log"
    old.write_text("evidence")
    now = datetime.now(timezone.utc) + timedelta(days=31)
    report = log_plan(logs, tmp_path / "work", now=now)
    assert report['requires_manual_review'] == [str(old)]
    assert old.read_text() == "evidence"

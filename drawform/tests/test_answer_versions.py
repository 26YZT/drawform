"""T-13：回答沿已有 amended 版本链生效，重启后仍能查证父版与人工选择。"""

import hashlib
import io
import json
import subprocess
import sys
import zipfile

from drawform.delivery import BLOCK, DeliveryBlocked, Verdict
from drawform.review import ReviewLog
from drawform.service.jobs import JobQueue, versions_of
from tests.test_live_answers import _answer, _drawn, _fit, _submit, _wait
from tests.test_live_questions import AUTH, TOKEN, service_factory


def _first(service, step):
    job_id, initial, questions = _drawn(service, step)
    response = _submit(service, job_id, [_answer(_fit(questions))])
    assert response.status_code == 202, response.data
    status = _wait(service, job_id)
    assert status["state"] == "drawn", status
    return job_id, initial, status


def _audit(service):
    path = service.work / "reviews.jsonl"
    assert path.is_file(), "回答必须进入既有 append-only 审计文件"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_two_answer_rounds_share_the_existing_version_chain_and_keep_the_first_choice(plate_step, service_factory):
    service = service_factory(seeded=False)
    job_id, initial, first = _first(service, plate_step)
    job = service.queue.get(job_id)
    parent = versions_of(job)[-1]
    parent_bytes = parent.read_bytes()
    first_svg = service.http.get(first["files"]["svg"], headers=AUTH).data
    question = next(q for q in first["questions"] if q["kind"] == "datum_choice")
    response = _submit(service, job_id, [_answer(question, None)])
    assert response.status_code == 202, response.data
    second = _wait(service, job_id)
    assert second["state"] == "drawn", second
    assert second["version"] == 2 and second["answered_count"] == 2
    assert second["unknown_count"] == 1
    assert second["diff_from_previous"] == {"changed_texts": 0, "changed_positions": 0}
    assert service.http.get(second["files"]["svg"], headers=AUTH).data == first_svg
    assert parent.read_bytes() == parent_bytes
    assert [p.name for p in versions_of(service.queue.get(job_id))] == ["amended-1.dxf", "amended-2.dxf"]
    rows = [row for row in _audit(service) if row.get("event") == "answers" and row["state"] == "drawn"]
    assert [row["version"] for row in rows] == [1, 2]
    assert rows[1]["draft_sha"] == hashlib.sha256(parent_bytes).hexdigest()
    assert rows[0]["draft_sha"] == hashlib.sha256(service.http.get(initial["files"]["dxf"], headers=AUTH).data).hexdigest()


def test_answer_audit_is_not_a_review_decision_and_does_not_store_plaintext_tokens(plate_step, service_factory):
    service = service_factory(seeded=False)
    job_id, _, _ = _first(service, plate_step)
    rows = _audit(service)
    assert [row["state"] for row in rows] == ["queued", "drawn"]
    assert all(row["event"] == "answers" and "decision" not in row for row in rows)
    assert all(row["token_hint"] == hashlib.sha256(TOKEN.encode()).hexdigest()[:8] for row in rows)
    assert TOKEN not in (service.work / "reviews.jsonl").read_text()
    stats = service.http.get("/review/stats", headers=AUTH).get_json()
    assert stats["total"] == 0 and stats["damaged_lines"] == 0
    assert service.http.get(f"/jobs/{job_id}/review", headers=AUTH).get_json()["reviews"] == []
    response = service.http.post(f"/jobs/{job_id}/review", headers=AUTH,
                                 json={"reviewer": "复核人乙", "decision": "accepted"})
    assert response.status_code == 201, response.data
    assert service.http.get("/review/stats", headers=AUTH).get_json()["decisions"] == {"accepted": 1}


def test_a_fresh_process_can_continue_answering_and_keeps_prior_answers(plate_step, service_factory):
    service = service_factory(seeded=False)
    job_id, _, first = _first(service, plate_step)
    question = next(q for q in first["questions"] if q["kind"] == "datum_choice")
    first_svg = service.http.get(first["files"]["svg"], headers=AUTH).data
    service.queue.stop()
    script = '''
import hashlib, json, sys, time
from drawform.service.app import create_app
work, job_id, question_id = sys.argv[1:]
app = create_app(work, token="questions-test-token")
auth = {"Authorization": "Bearer questions-test-token"}
with app.test_client() as http:
    before = http.get(f"/jobs/{job_id}", headers=auth).get_json()
    posted = http.post(f"/jobs/{job_id}/answers", headers=auth,
                       json={"reviewer": "续答人", "answers": [{"question_id": question_id,
                              "option_id": None, "free_note": "不知道"}]})
    limit = time.monotonic() + 30
    while time.monotonic() < limit:
        after = http.get(f"/jobs/{job_id}", headers=auth).get_json()
        if after["state"] in ("drawn", "refused", "crashed"):
            break
        time.sleep(0.02)
    svg = http.get(after["files"]["svg"], headers=auth).data
    document = http.get(after["files"]["json"], headers=auth).get_json()
print(json.dumps({"before": before, "post_status": posted.status_code, "after": after,
                  "svg_sha": hashlib.sha256(svg).hexdigest(), "document": document}))
'''
    result = subprocess.run([sys.executable, "-c", script, str(service.work), job_id, question["id"]],
                            capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["before"].get("version") == 1
    assert data["post_status"] == 202
    assert data["after"]["state"] == "drawn" and data["after"]["version"] == 2
    assert data["after"]["answered_count"] == 2
    assert data["svg_sha"] == hashlib.sha256(first_svg).hexdigest()
    assert any(a["chosen_option_id"] == "H7" for a in data["document"]["answers"])


def test_a_queued_answer_is_marked_interrupted_after_restart_without_becoming_effective(plate_step, service_factory):
    service = service_factory(seeded=False)
    job_id, _, questions = _drawn(service, plate_step)
    service.queue.stop()
    response = _submit(service, job_id, [_answer(_fit(questions))])
    assert response.status_code == 202
    restored = JobQueue(service.work).get(job_id)
    assert restored.state == "crashed"
    assert restored.answer_runs and restored.answer_runs[-1].state == "crashed"
    assert restored.answer_runs[-1].reason == "interrupted"
    assert not versions_of(restored)


def test_the_package_keeps_distinct_version_files_and_the_answer_audit(plate_step, service_factory):
    service = service_factory(seeded=False)
    job_id, _, _ = _first(service, plate_step)
    response = service.http.get(f"/jobs/{job_id}/package", headers=AUTH)
    assert response.status_code == 200, response.data
    with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
        names = archive.namelist()
        assert len(names) == len(set(names)), "不同版本不能用同名 ZIP 项相互遮盖"
        assert f"{job_id}/{job_id}.svg" in names
        assert f"{job_id}/amended-1/{job_id}.svg" in names
        assert f"{job_id}/amended-1.dxf" in names
        rows = [json.loads(line) for line in archive.read(f"{job_id}/review.jsonl").decode().splitlines()]
        assert any(row.get("event") == "answers" and row["state"] == "drawn" for row in rows)


def test_a_refused_answer_render_keeps_the_prior_version_and_records_why(plate_step, service_factory, monkeypatch):
    service = service_factory(seeded=False)
    job_id, _, first = _first(service, plate_step)
    parent = versions_of(service.queue.get(job_id))[-1]
    original = parent.read_bytes()
    question = next(q for q in first["questions"] if q["kind"] == "datum_choice")

    def refused(*args, **kwargs):
        raise DeliveryBlocked(Verdict(BLOCK, ("illegal_plan",), ("验证交付门拒绝答案重出图",)))

    monkeypatch.setattr(service.queue, "_render", refused)
    response = _submit(service, job_id, [_answer(question, None)])
    assert response.status_code == 202
    status = _wait(service, job_id)
    assert status["state"] == "refused"
    assert versions_of(service.queue.get(job_id)) == (parent,)
    assert parent.read_bytes() == original
    assert not (parent.parent / "amended-2.dxf").exists()
    assert any(row.get("event") == "answers" and row["state"] == "refused" for row in _audit(service))

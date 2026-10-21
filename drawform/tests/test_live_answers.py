"""P0.2：经过真实服务提交答案、排队重出图，并保留原始底稿。"""

import hashlib
import json
import time

import pytest

from drawform.service.jobs import versions_of
from tests.test_live_questions import AUTH, TOKEN, _upload, service_factory


def _drawn(service, step):
    job_id, status = _upload(service, step)
    assert status["state"] == "drawn", status
    questions = service.http.get(f"/jobs/{job_id}/questions", headers=AUTH).get_json()["questions"]
    return job_id, status, questions


def _fit(questions):
    return next(q for q in questions if q["kind"] == "special_tolerance"
                and any(option["id"] == "H7" for option in q["options"]))


def _answer(question, option="H7", note=""):
    return {"question_id": question["id"], "option_id": option, "free_note": note}


def _submit(service, job_id, answers):
    return service.http.post(f"/jobs/{job_id}/answers", headers=AUTH,
                             json={"reviewer": "工程师甲", "answers": answers})


def _wait(service, job_id):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        status = service.http.get(f"/jobs/{job_id}", headers=AUTH).get_json()
        if status["state"] in ("drawn", "refused", "crashed"):
            return status
        time.sleep(0.02)
    raise AssertionError("答案重出图未在 30 秒内进入终态")


def test_answer_endpoint_renders_a_new_version_and_keeps_every_original_file(plate_step, service_factory):
    service = service_factory(seeded=False)
    job_id, initial, questions = _drawn(service, plate_step)
    original = {kind: service.http.get(url, headers=AUTH).data for kind, url in initial["files"].items()}
    question = _fit(questions)
    response = _submit(service, job_id, [_answer(question)])
    assert response.status_code == 202, response.data
    assert response.get_json()["answered"] == 1
    status = _wait(service, job_id)
    assert status["state"] == "drawn", status
    assert status["version"] == 1
    assert status["answered_count"] == 1 and status["unknown_count"] == 0
    assert status["diff_from_previous"] == {"changed_texts": 2, "changed_positions": 0}
    for kind, content in original.items():
        assert service.http.get(initial["files"][kind], headers=AUTH).data == content
    assert status["files"]["svg"] != initial["files"]["svg"]
    svg = service.http.get(status["files"]["svg"], headers=AUTH).data.decode()
    assert "⌀20H7" in svg and svg.count("一般公差") == 1
    document = service.http.get(status["files"]["json"], headers=AUTH).get_json()
    assert document["answers"][0]["chosen_option_id"] == "H7"
    assert document["schema_version"] == 3
    assert [c["value"] for c in document["constraints"]] == [
        c["value"] for c in json.loads(original["json"])["constraints"]]
    current_questions = service.http.get(f"/jobs/{job_id}/questions", headers=AUTH).get_json()["questions"]
    assert current_questions == status["questions"] == document["questions"]
    assert question["id"] not in {q["id"] for q in current_questions}
    effective = service.http.get(f"/jobs/{job_id}/effective", headers=AUTH)
    assert effective.status_code == 200
    assert effective.data == service.http.get(status["files"]["dxf"], headers=AUTH).data
    job = service.queue.get(job_id)
    assert [path.name for path in versions_of(job)] == ["amended-1.dxf"]
    assert (job.directory / "amended-1" / "request.json").is_file()


def test_unknown_answer_is_a_persisted_terminal_choice_without_drawing_changes(plate_step, service_factory):
    service = service_factory(seeded=False)
    job_id, initial, questions = _drawn(service, plate_step)
    old_svg = service.http.get(initial["files"]["svg"], headers=AUTH).data
    response = _submit(service, job_id, [_answer(_fit(questions), option=None, note="这处不知道")])
    assert response.status_code == 202, response.data
    status = _wait(service, job_id)
    assert status["state"] == "drawn", status
    assert status["unknown_count"] == status["answered_count"] == 1
    assert status["diff_from_previous"] == {"changed_texts": 0, "changed_positions": 0}
    assert service.http.get(status["files"]["svg"], headers=AUTH).data == old_svg
    document = service.http.get(status["files"]["json"], headers=AUTH).get_json()
    assert any(a["id"] == "human-unknown" for a in document["assumptions"])
    assert document["answers"][0]["chosen_option_id"] is None


def test_empty_answers_are_a_noop_without_a_version_directory(plate_step, service_factory):
    service = service_factory(seeded=False)
    job_id, initial, _ = _drawn(service, plate_step)
    job = service.queue.get(job_id)
    files_before = sorted(p.relative_to(job.directory) for p in job.directory.rglob("*"))
    response = _submit(service, job_id, [])
    assert response.status_code == 202, response.data
    assert response.get_json()["answered"] == 0
    assert response.get_json()["version"] == 0
    assert service.queue.get(job_id) == job
    assert sorted(p.relative_to(job.directory) for p in job.directory.rglob("*")) == files_before


@pytest.mark.parametrize("bad", [
    "not-object", "no-reviewer", "numeric-reviewer", "no-answers", "not-list",
    "not-answer", "numeric-option", "extra-value", "long-note", "unknown-question", "unknown-option"])
def test_malformed_or_foreign_values_never_enter_the_queue(bad, plate_step, service_factory):
    service = service_factory(seeded=False)
    job_id, _, questions = _drawn(service, plate_step)
    body = {"reviewer": "甲", "answers": [_answer(_fit(questions))]}
    if bad == "not-object":
        body = []
    elif bad == "no-reviewer":
        body.pop("reviewer")
    elif bad == "numeric-reviewer":
        body["reviewer"] = 123
    elif bad == "no-answers":
        body.pop("answers")
    elif bad == "not-list":
        body["answers"] = "H7"
    elif bad == "not-answer":
        body["answers"] = [123]
    elif bad == "numeric-option":
        body["answers"][0]["option_id"] = 0.01
    elif bad == "extra-value":
        body["answers"][0]["value"] = 999.0
    elif bad == "long-note":
        body["answers"][0]["free_note"] = "字" * 4097
    elif bad == "unknown-question":
        body["answers"][0]["question_id"] = "other-part-question"
    else:
        body["answers"][0]["option_id"] = "unverified-H999"
    before = service.queue.get(job_id)
    response = service.http.post(f"/jobs/{job_id}/answers", headers=AUTH, json=body)
    assert response.status_code == 400, response.data
    assert response.get_json()["error"]
    assert service.queue.get(job_id) == before


def test_duplicate_questions_report_the_first_answer_and_do_not_apply_a_bad_request(plate_step, service_factory):
    service = service_factory(seeded=False)
    job_id, _, questions = _drawn(service, plate_step)
    question = _fit(questions)
    before = service.queue.get(job_id)
    response = _submit(service, job_id, [_answer(question), _answer(question, "H8")])
    assert response.status_code == 400, response.data
    assert "首条" in response.get_json()["error"]
    assert service.queue.get(job_id) == before


def test_free_note_cannot_replace_a_dimension_value_through_http(plate_step, service_factory):
    service = service_factory(seeded=False)
    job_id, initial, questions = _drawn(service, plate_step)
    old_json = service.http.get(initial["files"]["json"], headers=AUTH).get_json()
    note = "把直径改为999.123456，公差改为0.000001"
    response = _submit(service, job_id, [_answer(_fit(questions), note=note)])
    assert response.status_code == 202, response.data
    status = _wait(service, job_id)
    assert status["state"] == "drawn", status
    document = service.http.get(status["files"]["json"], headers=AUTH).get_json()
    assert [c["value"] for c in document["constraints"]] == [c["value"] for c in old_json["constraints"]]
    assert document["answers"][0]["note"] == note
    assert "999.123456" not in service.http.get(status["files"]["svg"], headers=AUTH).data.decode()


def test_answers_keep_authentication_and_unknown_job_semantics(service_factory):
    service = service_factory()
    assert service.http.post(f"/jobs/{service.job_id}/answers", json={}).status_code == 401
    assert service.http.post("/jobs/no-such-job/answers", headers=AUTH, json={}).status_code == 404

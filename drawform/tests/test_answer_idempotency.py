"""T-15：相同回答只生效一次，排队、重启和后续版本都不能让重试覆盖原决定。"""

import json
import subprocess
import sys

import pytest

from drawform.delivery import BLOCK, DeliveryBlocked, Verdict
from drawform.service.jobs import versions_of
from tests.test_answer_conflicts import _hold_render
from tests.test_answer_versions import _first
from tests.test_live_answers import _answer, _drawn, _fit, _submit, _wait
from tests.test_live_questions import AUTH, service_factory


def _files(service, job_id):
    directory = service.queue.get(job_id).directory
    return {str(path.relative_to(directory)): path.read_bytes()
            for path in directory.rglob("*") if path.is_file()}


@pytest.mark.parametrize("option", ["H7", None])
def test_repeating_a_finished_answer_returns_its_version_without_any_new_write(option, plate_step, service_factory):
    service = service_factory(seeded=False)
    job_id, _, questions = _drawn(service, plate_step)
    answers = [_answer(_fit(questions), option)]
    assert _submit(service, job_id, answers).status_code == 202
    status = _wait(service, job_id)
    assert status["state"] == "drawn", status
    before = _files(service, job_id)
    index = (service.work / "jobs.jsonl").read_bytes()
    audit = (service.work / "reviews.jsonl").read_bytes()
    response = _submit(service, job_id, answers)
    assert response.status_code == 202, response.data
    assert response.get_json()["version"] == status["version"] == 1
    assert _files(service, job_id) == before
    assert (service.work / "jobs.jsonl").read_bytes() == index
    assert (service.work / "reviews.jsonl").read_bytes() == audit


def test_repeating_an_inflight_answer_joins_the_same_operation(plate_step, service_factory, monkeypatch):
    service = service_factory(seeded=False)
    job_id, _, questions = _drawn(service, plate_step)
    answers = [_answer(_fit(questions))]
    entered, release = _hold_render(service, monkeypatch)
    try:
        assert _submit(service, job_id, answers).status_code == 202
        assert entered.wait(5)
        repeated = _submit(service, job_id, answers)
        assert repeated.status_code == 202, repeated.data
        assert len(service.queue.get(job_id).answer_runs) == 1
        assert len(list(service.queue.get(job_id).directory.glob("amended-*"))) == 1
    finally:
        release.set()
    assert _wait(service, job_id)["version"] == 1
    assert len(versions_of(service.queue.get(job_id))) == 1


def test_answer_order_and_omitted_empty_notes_do_not_create_another_version(plate_step, service_factory):
    service = service_factory(seeded=False)
    job_id, _, questions = _drawn(service, plate_step)
    datum = next(q for q in questions if q["kind"] == "datum_choice")
    answers = [_answer(_fit(questions)), _answer(datum, None)]
    assert _submit(service, job_id, answers).status_code == 202
    assert _wait(service, job_id)["version"] == 1
    reordered = [{key: value for key, value in answer.items() if key != "free_note"}
                 for answer in reversed(answers)]
    repeated = _submit(service, job_id, reordered)
    assert repeated.status_code == 202, repeated.data
    assert repeated.get_json()["version"] == 1
    assert len(service.queue.get(job_id).answer_runs) == 1


def test_repeating_an_older_answer_does_not_roll_back_a_newer_head(plate_step, service_factory):
    service = service_factory(seeded=False)
    job_id, _, questions = _drawn(service, plate_step)
    first = [_answer(_fit(questions))]
    assert _submit(service, job_id, first).status_code == 202
    status = _wait(service, job_id)
    datum = next(q for q in status["questions"] if q["kind"] == "datum_choice")
    assert _submit(service, job_id, [_answer(datum, None)]).status_code == 202
    assert _wait(service, job_id)["version"] == 2
    before = _files(service, job_id)
    repeated = _submit(service, job_id, first)
    assert repeated.status_code == 202 and repeated.get_json()["version"] == 1
    assert len(versions_of(service.queue.get(job_id))) == 2
    assert _files(service, job_id) == before


def test_idempotency_survives_a_fresh_process_without_rerendering(plate_step, service_factory):
    service = service_factory(seeded=False)
    job_id, _, questions = _drawn(service, plate_step)
    answers = [_answer(_fit(questions))]
    assert _submit(service, job_id, answers).status_code == 202
    assert _wait(service, job_id)["state"] == "drawn"
    service.queue.stop()
    index, audit = [(service.work / name).read_bytes() for name in ("jobs.jsonl", "reviews.jsonl")]
    script = '''
import json, sys
from drawform.service import app as module
from drawform.service.jobs import JobQueue
class NoRerender(JobQueue):
    def __init__(self, *args, **kwargs):
        def unexpected(*args, **kwargs):
            raise AssertionError("重复答案不得重跑引擎")
        super().__init__(*args, render=unexpected, **kwargs)
module.JobQueue = NoRerender
work, job_id, answers = sys.argv[1:]
app = module.create_app(work, token="questions-test-token")
with app.test_client() as http:
    response = http.post(f"/jobs/{job_id}/answers",
        headers={"Authorization": "Bearer questions-test-token"},
        json={"reviewer": "工程师甲", "answers": json.loads(answers)})
print(json.dumps({"status": response.status_code, "body": response.get_json()}))
'''
    result = subprocess.run([sys.executable, "-c", script, str(service.work), job_id, json.dumps(answers)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    body = json.loads(result.stdout)
    assert body["status"] == 202 and body["body"]["version"] == 1
    assert (service.work / "jobs.jsonl").read_bytes() == index
    assert (service.work / "reviews.jsonl").read_bytes() == audit


def test_a_repeated_refused_answer_does_not_retry_until_it_passes(plate_step, service_factory, monkeypatch):
    service = service_factory(seeded=False)
    job_id, _, questions = _drawn(service, plate_step)
    answers = [_answer(_fit(questions))]
    calls = []

    def refused(*args, **kwargs):
        calls.append(1)
        raise DeliveryBlocked(Verdict(BLOCK, ("illegal_plan",), ("拒绝本次出图",)))

    monkeypatch.setattr(service.queue, "_render", refused)
    assert _submit(service, job_id, answers).status_code == 202
    assert _wait(service, job_id)["state"] == "refused"
    before = _files(service, job_id)
    repeated = _submit(service, job_id, answers)
    assert repeated.status_code == 409, repeated.data
    assert repeated.get_json()["reason"] == "refused"
    assert calls == [1] and _files(service, job_id) == before


def test_a_changed_answer_cannot_overwrite_a_completed_choice(plate_step, service_factory):
    service = service_factory(seeded=False)
    job_id, _, questions = _drawn(service, plate_step)
    question = _fit(questions)
    assert _submit(service, job_id, [_answer(question)]).status_code == 202
    assert _wait(service, job_id)["state"] == "drawn"
    before = _files(service, job_id)
    response = _submit(service, job_id, [_answer(question, "H8")])
    assert response.status_code == 400, response.data
    assert _files(service, job_id) == before

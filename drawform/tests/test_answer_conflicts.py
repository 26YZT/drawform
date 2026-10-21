"""T-14：状态冲突使用 409，并在提交和生效两个边界守住父版与冻结状态。"""

from dataclasses import replace
import hashlib
import threading

import pytest

from drawform.service.jobs import StaleBase, versions_of
from tests.test_live_answers import _answer, _drawn, _fit, _submit, _wait
from tests.test_live_questions import AUTH, service_factory


@pytest.mark.parametrize("state", ["queued", "running", "refused", "crashed"])
def test_each_unanswerable_state_has_a_specific_conflict_reason(state, service_factory):
    service = service_factory()
    service.queue._set(service.job_id, state=state, failed=("fixture",), reasons=("状态验证",))
    response = _submit(service, service.job_id, [])
    assert response.status_code == 409, response.data
    body = response.get_json()
    assert body["reason"] == f"job_{state}"
    assert body["state"] == state and body["failed"] == ["fixture"]
    assert body["reasons"] == ["状态验证"]


def test_an_external_amended_head_is_a_stale_base_for_engine_answers(plate_step, service_factory):
    service = service_factory(seeded=False)
    job_id, _, questions = _drawn(service, plate_step)
    draft = dict(service.queue.get(job_id).files)["dxf"].read_bytes()
    service.queue.put_version(job_id, draft, hashlib.sha256(draft).hexdigest())
    response = _submit(service, job_id, [_answer(_fit(questions))])
    assert response.status_code == 409, response.data
    assert response.get_json()["reason"] == "stale_base"
    assert "重新" in response.get_json()["error"]
    assert len(versions_of(service.queue.get(job_id))) == 1


def test_a_numeric_error_freeze_blocks_answer_submission(plate_step, service_factory):
    service = service_factory(seeded=False)
    job_id, _, questions = _drawn(service, plate_step)
    reviewed = service.http.post(f"/jobs/{job_id}/review", headers=AUTH,
                                  json={"reviewer": "复核人", "decision": "rejected",
                                        "reasons": ["wrong_value"]})
    assert reviewed.status_code == 201
    response = _submit(service, job_id, [_answer(_fit(questions))])
    assert response.status_code == 409, response.data
    assert response.get_json()["reason"] == "frozen"
    assert not service.queue.get(job_id).answer_runs


def _hold_render(service, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    render = service.queue._render

    def held(*args, **kwargs):
        entered.set()
        assert release.wait(10), "测试未释放唯一 worker"
        return render(*args, **kwargs)

    monkeypatch.setattr(service.queue, "_render", held)
    return entered, release


def test_an_inflight_answer_excludes_another_answer_and_manual_version(plate_step, service_factory, monkeypatch):
    service = service_factory(seeded=False)
    job_id, _, questions = _drawn(service, plate_step)
    question = _fit(questions)
    entered, release = _hold_render(service, monkeypatch)
    try:
        assert _submit(service, job_id, [_answer(question)]).status_code == 202
        assert entered.wait(5)
        response = _submit(service, job_id, [_answer(question, "H8")])
        assert response.status_code == 409
        assert response.get_json()["reason"] == "job_running"
        with pytest.raises(StaleBase):
            service.queue.put_version(job_id, b"manual replacement")
        assert not versions_of(service.queue.get(job_id))
    finally:
        release.set()
    status = _wait(service, job_id)
    assert status["state"] == "drawn" and status["version"] == 1


def test_a_parent_change_during_render_is_refused_and_reported_as_stale_base(plate_step, service_factory, monkeypatch):
    service = service_factory(seeded=False)
    job_id, _, questions = _drawn(service, plate_step)
    entered, release = _hold_render(service, monkeypatch)
    try:
        assert _submit(service, job_id, [_answer(_fit(questions))]).status_code == 202
        assert entered.wait(5)
        draft = dict(service.queue.get(job_id).files)["dxf"]
        draft.write_bytes(draft.read_bytes() + b"\nchanged outside the queue\n")
    finally:
        release.set()
    status = _wait(service, job_id)
    assert status["state"] == "refused", status
    assert status["reason"] == "stale_base"
    assert not versions_of(service.queue.get(job_id))


def test_a_freeze_arriving_during_render_is_checked_before_publication(plate_step, service_factory, monkeypatch):
    service = service_factory(seeded=False)
    job_id, _, questions = _drawn(service, plate_step)
    entered, release = _hold_render(service, monkeypatch)
    try:
        assert _submit(service, job_id, [_answer(_fit(questions))]).status_code == 202
        assert entered.wait(5)
        service.queue._audit.record(job_id=job_id, reviewer="已进入审计的并发复核", decision="rejected",
                                     reasons=["wrong_value"])
    finally:
        release.set()
    status = _wait(service, job_id)
    assert status["state"] == "refused", status
    assert status["reason"] == "frozen"
    assert not versions_of(service.queue.get(job_id))


def test_a_datum_without_engine_direction_metadata_is_not_silently_accepted(plate_step, service_factory):
    service = service_factory(seeded=False)
    job_id, _, _ = _drawn(service, plate_step)
    job = service.queue.get(job_id)
    datum = next(q for q in job.questions if q.kind == "datum_choice")
    chosen = next(o for o in datum.options if o.axis == datum.axis)
    incomplete = replace(datum, datum="", axis="", options=tuple(replace(o, axis="") for o in datum.options))
    service.queue._set(job_id, questions=tuple(incomplete if q.id == datum.id else q for q in job.questions))
    response = _submit(service, job_id, [{"question_id": datum.id, "option_id": chosen.id}])
    assert response.status_code == 409, response.data
    assert response.get_json()["reason"] == "missing_basis"
    assert not service.queue.get(job_id).answer_runs

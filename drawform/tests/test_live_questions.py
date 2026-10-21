"""P0.1 服务契约；成功上传链路也必须经过 create_app + HTTP test client。"""

from dataclasses import asdict, replace
import json
import inspect
import re
import subprocess
import sys
import time
from types import SimpleNamespace
import zlib

import pytest

from drawform.dimensioning.constraints import Plan, Sized
from drawform.dimensioning.scheme import Scheme
from drawform.service import app as app_module
from drawform.service.questions import special_tolerance_questions

TOKEN = "questions-test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _document(*, empty=False, truncated=False):
    scheme = Scheme(plan=Plan((Sized("outline", "length", 2.0),),
                              general_tolerance="ISO 2768-v"),
                    unknowns=(("outline", "length"),))
    questions = special_tolerance_questions(scheme)
    if truncated:
        questions = tuple(replace(q, options=q.options[:1], truncated=True) for q in questions)
    document = {"questions": [] if empty else [asdict(q) for q in questions],
                "truncated": truncated}
    return json.loads(json.dumps(document))


@pytest.fixture
def service_factory(tmp_path, monkeypatch):
    queues = []
    original_queue = app_module.JobQueue

    class ObservedQueue(original_queue):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            queues.append(self)

    monkeypatch.setattr(app_module, "JobQueue", ObservedQueue)

    def create(document=None, *, seeded=True, has_json=True, work=None):
        work = tmp_path / f"work-{len(queues)}" if work is None else work
        job_id = "seeded-job"
        source = work / job_id / "drawing.json"
        if seeded:
            directory = work / job_id
            directory.mkdir(parents=True)
            (directory / "model.step").write_text("API 边界夹具，不参与渲染", encoding="utf-8")
            (directory / "drawing.dxf").write_bytes(b"contract fixture")
            files = [["dxf", "drawing.dxf"]]
            if has_json:
                source.write_text(json.dumps(_document() if document is None else document,
                                             ensure_ascii=False), encoding="utf-8")
                files.append(["json", source.name])
            row = {"job": job_id, "state": "drawn", "source": "model.step",
                   "files": files, "failed": [], "error": ""}
            (work / "jobs.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
        app = app_module.create_app(work, token=TOKEN)
        app.testing = True
        return SimpleNamespace(app=app, http=app.test_client(), queue=queues[-1],
                               work=work, job_id=job_id, source=source)

    yield create
    for queue in queues:
        queue.stop()


def test_questions_endpoint_returns_structured_engine_candidates(service_factory):
    document = _document()
    service = service_factory(document)
    response = service.http.get(f"/jobs/{service.job_id}/questions", headers=AUTH)
    assert response.status_code == 200, response.data
    assert response.get_json() == {"job_id": service.job_id, **document}


def test_questions_endpoint_reports_candidate_truncation(service_factory):
    service = service_factory(_document(truncated=True))
    response = service.http.get(f"/jobs/{service.job_id}/questions", headers=AUTH)
    assert response.status_code == 200, response.data
    assert response.get_json()["truncated"] is True


def test_a_drawn_job_with_no_questions_returns_an_empty_list(service_factory):
    service = service_factory(_document(empty=True))
    response = service.http.get(f"/jobs/{service.job_id}/questions", headers=AUTH)
    assert response.status_code == 200, response.data
    assert response.get_json() == {"job_id": service.job_id, "questions": [], "truncated": False}


@pytest.mark.parametrize("state", ["queued", "running", "refused", "crashed"])
def test_questions_require_a_drawn_job_even_if_a_json_path_exists(state, service_factory):
    service = service_factory()
    service.queue._set(service.job_id, state=state, failed=("illegal_plan",),
                       reasons=("方案未通过",))
    response = service.http.get(f"/jobs/{service.job_id}/questions", headers=AUTH)
    assert response.status_code == 409, response.data
    body = response.get_json()
    assert body["state"] == state
    assert body["failed"] == ["illegal_plan"]
    assert body["reasons"] == ["方案未通过"]
    assert "questions" not in body
    assert "重新" in body["error"]


def test_a_job_without_json_basis_is_a_conflict(service_factory):
    service = service_factory(has_json=False)
    response = service.http.get(f"/jobs/{service.job_id}/questions", headers=AUTH)
    assert response.status_code == 409, response.data
    assert "questions" not in response.get_json()


def test_a_missing_json_file_is_a_conflict(service_factory):
    service = service_factory()
    service.source.rename(service.source.with_suffix(".missing"))
    response = service.http.get(f"/jobs/{service.job_id}/questions", headers=AUTH)
    assert response.status_code == 409, response.data
    assert "questions" not in response.get_json()


def test_invalid_json_is_not_reported_as_no_questions(service_factory):
    service = service_factory()
    service.source.write_text("{incomplete", encoding="utf-8")
    response = service.http.get(f"/jobs/{service.job_id}/questions", headers=AUTH)
    assert response.status_code == 409, response.data
    assert "questions" not in response.get_json()


def test_a_json_without_question_data_is_not_reported_as_no_questions(service_factory):
    service = service_factory({"part": {}})
    response = service.http.get(f"/jobs/{service.job_id}/questions", headers=AUTH)
    assert response.status_code == 409, response.data
    assert "重新" in response.get_json()["error"]


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}])
def test_questions_use_the_existing_token_boundary(headers, service_factory):
    service = service_factory()
    assert service.http.get(f"/jobs/{service.job_id}/questions", headers=headers).status_code == 401


def test_unknown_job_questions_return_404(service_factory):
    service = service_factory()
    assert service.http.get("/jobs/no-such-job/questions", headers=AUTH).status_code == 404


def _pipeline():
    from drawform import pipeline
    assert "with_questions" in inspect.signature(pipeline.render_part).parameters, \
        "出图主流程尚未接入问题清单"
    return pipeline


def _pdf_page_streams(path):
    streams = []
    for blob in re.findall(rb"stream\r?\n(.*?)endstream", path.read_bytes(), re.S):
        try:
            content = zlib.decompress(blob)
        except zlib.error:
            content = blob
        if b"BT" in content:
            streams.append(content)
    assert streams, "没有找到 PDF 文字内容流"
    return streams


def _dxf_entities(path):
    import ezdxf
    return [(entity.dxftype(), {key: value for key, value in entity.dxfattribs().items()
                               if key not in ("handle", "owner")})
            for entity in ezdxf.readfile(str(path)).modelspace()]


def test_enabling_questions_preserves_all_three_drawing_outputs(bearing_seat_step, tmp_path):
    pipeline = _pipeline()
    before, after = tmp_path / "before", tmp_path / "after"
    before.mkdir()
    after.mkdir()
    default = pipeline.render_part(bearing_seat_step, before, stem="same")
    enabled = pipeline.render_part(bearing_seat_step, after, stem="same", with_questions=True)
    assert default.questions == ()
    assert enabled.questions
    assert enabled.dimensions == default.dimensions
    assert enabled.notes == default.notes
    assert enabled.verdict == default.verdict
    original, current = dict(default.files), dict(enabled.files)
    assert original["svg"].read_bytes() == current["svg"].read_bytes()
    old_entities, new_entities = _dxf_entities(original["dxf"]), _dxf_entities(current["dxf"])
    assert old_entities == new_entities
    assert any(kind == "DIMENSION" for kind, _ in new_entities)
    assert _pdf_page_streams(original["pdf"]) == _pdf_page_streams(current["pdf"])
    old_json = json.loads(original["json"].read_text(encoding="utf-8"))
    new_json = json.loads(current["json"].read_text(encoding="utf-8"))
    assert "questions" not in old_json and "truncated" not in old_json
    assert new_json.pop("questions") == json.loads(json.dumps([asdict(q) for q in enabled.questions]))
    assert new_json.pop("truncated") is False
    assert new_json == old_json, "问题清单不得改变任何既有数值或推断"


def test_default_render_skips_question_enumeration(plate_step, tmp_path, monkeypatch):
    pipeline = _pipeline()
    from drawform.delivery import ALLOW, Verdict

    assert pipeline.Part(Verdict(ALLOW), ()).questions == ()

    def unexpected(*args, **kwargs):
        raise AssertionError("默认调用不应枚举候选")

    monkeypatch.setattr(pipeline, "questions_for", unexpected)
    assert pipeline.render_part(plate_step, tmp_path).questions == ()


def test_question_generation_keeps_the_delivery_gate(refused_step, tmp_path):
    pipeline = _pipeline()
    from drawform.delivery import DeliveryBlocked

    with pytest.raises(DeliveryBlocked):
        pipeline.render_part(refused_step, tmp_path, with_questions=True)
    assert list(tmp_path.iterdir()) == []


def _upload(service, step):
    with step.open("rb") as handle:
        response = service.http.post("/jobs", headers=AUTH,
                                     data={"step": (handle, "upload.step")},
                                     content_type="multipart/form-data")
    assert response.status_code == 202, response.data
    job_id = response.get_json()["job_id"]
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        body = service.http.get(f"/jobs/{job_id}", headers=AUTH).get_json()
        if body["state"] in ("drawn", "refused", "crashed"):
            return job_id, body
        time.sleep(0.02)
    raise AssertionError(f"上传任务 {job_id} 未在 30 秒内进入终态")


def test_live_upload_returns_the_same_questions_on_part_job_json_and_http(
        bearing_seat_step, service_factory):
    service = service_factory(seeded=False)
    job_id, status = _upload(service, bearing_seat_step)
    assert status["state"] == "drawn", status
    response = service.http.get(f"/jobs/{job_id}/questions", headers=AUTH)
    assert response.status_code == 200, response.data
    body = response.get_json()
    assert body["job_id"] == job_id
    assert body["questions"] and body["truncated"] is False
    document = service.http.get(status["files"]["json"], headers=AUTH).get_json()
    assert body["questions"] == document["questions"]
    assert document["schema_version"] == 2
    job = service.queue.get(job_id)
    assert body["questions"] == json.loads(json.dumps([asdict(q) for q in job.questions]))
    for question in body["questions"]:
        assert question["kind"] in ("special_tolerance", "datum_choice", "unnoted_angle")
        assert question["options"] and question["allow_unknown"] is True
        assert question["impact"].strip()
    assert service.http.get(f"/jobs/{job_id}/questions", headers=AUTH).get_json() == body


def test_questions_and_job_cache_survive_a_fresh_process(bearing_seat_step, service_factory):
    service = service_factory(seeded=False)
    job_id, status = _upload(service, bearing_seat_step)
    assert status["state"] == "drawn", status
    response = service.http.get(f"/jobs/{job_id}/questions", headers=AUTH)
    assert response.status_code == 200, response.data
    expected = response.get_json()
    service.queue.stop()
    index_before = (service.work / "jobs.jsonl").read_bytes()
    script = '''
from dataclasses import asdict
import json, sys
from drawform.service.app import create_app
from drawform.service.jobs import JobQueue
work, job_id = sys.argv[1:]
cached = JobQueue(work).get(job_id)
app = create_app(work, token="questions-test-token")
with app.test_client() as http:
    response = http.get(f"/jobs/{job_id}/questions",
                        headers={"Authorization": "Bearer questions-test-token"})
print(json.dumps({"status": response.status_code, "body": response.get_json(),
                  "cached": [asdict(q) for q in cached.questions]}))
'''
    result = subprocess.run([sys.executable, "-c", script, str(service.work), job_id],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    actual = json.loads(result.stdout)
    assert actual["status"] == 200
    assert actual["body"] == expected
    assert actual["cached"] == expected["questions"]
    assert (service.work / "jobs.jsonl").read_bytes() == index_before


def test_a_real_part_without_questions_returns_an_empty_list(tmp_path, service_factory):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from tests.conftest import _write_step

    step = _write_step(BRepPrimAPI_MakeBox(80.0, 50.0, 12.0).Shape(), tmp_path / "box.step")
    service = service_factory(seeded=False)
    job_id, status = _upload(service, step)
    assert status["state"] == "drawn", status
    response = service.http.get(f"/jobs/{job_id}/questions", headers=AUTH)
    assert response.status_code == 200, response.data
    assert response.get_json() == {"job_id": job_id, "questions": [], "truncated": False}
    assert service.queue.get(job_id).questions == ()


@pytest.mark.parametrize("fixture_name,state", [
    ("refused_step", "refused"), ("not_a_step", "crashed")])
def test_an_unsuccessful_upload_has_no_questions_object(
        fixture_name, state, request, service_factory):
    service = service_factory(seeded=False)
    job_id, status = _upload(service, request.getfixturevalue(fixture_name))
    assert status["state"] == state, status
    response = service.http.get(f"/jobs/{job_id}/questions", headers=AUTH)
    assert response.status_code == 409, response.data
    assert response.get_json()["state"] == state
    assert "questions" not in response.get_json()
    assert status["files"] == {}


def test_the_live_service_reports_real_candidate_overflow(plate_step, service_factory, monkeypatch):
    from drawform.service import questions

    monkeypatch.setattr(questions, "MAX_OPTIONS", 1)
    service = service_factory(seeded=False)
    job_id, status = _upload(service, plate_step)
    assert status["state"] == "drawn", status
    response = service.http.get(f"/jobs/{job_id}/questions", headers=AUTH)
    assert response.status_code == 200, response.data
    body = response.get_json()
    assert body["truncated"] is True
    assert all(q["truncated"] and len(q["options"]) == 1 for q in body["questions"])
    assert all(q.truncated for q in service.queue.get(job_id).questions)


def test_each_upload_reads_its_own_question_candidates(plate_step, bearing_seat_step, service_factory):
    service = service_factory(seeded=False)
    results = []
    for step in (plate_step, bearing_seat_step):
        job_id, status = _upload(service, step)
        assert status["state"] == "drawn", status
        response = service.http.get(f"/jobs/{job_id}/questions", headers=AUTH)
        assert response.status_code == 200, response.data
        body = response.get_json()
        document = service.http.get(status["files"]["json"], headers=AUTH).get_json()
        assert body["job_id"] == job_id
        assert body["questions"] == document["questions"]
        results.append(body)
    assert results[0]["job_id"] != results[1]["job_id"]
    assert {o["id"] for q in results[0]["questions"] for o in q["options"]} != {
        o["id"] for q in results[1]["questions"] for o in q["options"]}

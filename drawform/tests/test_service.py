"""HTTP 层：上传 STEP → 查任务 → 取文件 / 取判定清单。

这一层唯一需要小心的是"客户端给的东西不能直接当路径用"，其余都是搬运。
"""
import json
import time

import pytest

from drawform.service.app import create_app


TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def client(tmp_path, bearing_seat_step):
    app = create_app(tmp_path / "work", token=TOKEN)
    app.testing = True
    return app.test_client(), bearing_seat_step


def _wait_for_result(client, job_id, timeout=120.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = json.loads(client.get(f"/jobs/{job_id}", headers=AUTH).data)
        if body["state"] in ("drawn", "refused", "crashed"):
            return body
        time.sleep(0.05)
    raise AssertionError(f"任务 {job_id} 超时未结束")


def test_uploading_a_step_returns_files_you_can_download(client, tmp_path):
    """端到端：一次上传，三个出口都能取回来。这是"能出图"第一次变成可以点的东西。"""
    http, step = client
    posted = http.post("/jobs", headers=AUTH,
                       data={"step": (open(step, "rb"), "bearing.step")},
                       content_type="multipart/form-data")
    assert posted.status_code == 202, posted.data
    job_id = json.loads(posted.data)["job_id"]

    body = _wait_for_result(http, job_id)
    assert body["state"] == "drawn", body
    assert set(body["files"]) == {"dxf", "svg", "pdf", "json"}

    svg = http.get(f"/jobs/{job_id}/files/svg", headers=AUTH)
    assert svg.status_code == 200
    assert b"<svg" in svg.data
    assert http.get(f"/jobs/{job_id}/files/pdf", headers=AUTH).status_code == 200


def test_a_refused_drawing_reports_which_checks_failed(client, refused_step):
    """判定清单是这个接口的正事：工程师要知道"为什么没有图"，而不只是"失败了"。"""
    http, _ = client
    job_id = json.loads(http.post(
        "/jobs", headers=AUTH,
        data={"step": (open(refused_step, "rb"), "refused.step")},
        content_type="multipart/form-data").data)["job_id"]

    body = _wait_for_result(http, job_id)
    assert body["state"] == "refused"
    assert body["failed"], "必须带机器认的项目 id"
    assert body["reasons"], "必须带给人看的中文理由"
    assert body["files"] == {}
    assert http.get(f"/jobs/{job_id}/files/svg", headers=AUTH).status_code == 404


def test_a_client_supplied_filename_never_becomes_a_path(client, tmp_path):
    """上传名里带 `../` 是必然会发生的事。任务目录必须由服务自己编号，
    文件名不参与寻址，否则一次上传就能把文件写到 work 目录之外。
    """
    http, step = client
    job_id = json.loads(http.post(
        "/jobs", headers=AUTH,
        data={"step": (open(step, "rb"), "../../../../etc/evil.step")},
        content_type="multipart/form-data").data)["job_id"]

    _wait_for_result(http, job_id)
    assert not list((tmp_path / "work").parent.glob("**/evil.step"))
    assert list((tmp_path / "work" / job_id).glob("*.step"))


def test_an_unknown_job_is_404_not_a_stack_trace(client):
    http, _ = client
    assert http.get("/jobs/doesnotexist", headers=AUTH).status_code == 404
    assert http.get("/jobs/doesnotexist/files/svg", headers=AUTH).status_code == 404

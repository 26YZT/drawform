"""服务端的最小边界：谁能用、能塞多大、留多少。

这三条都是自用场景下**依然存在**的风险 —— 自用去掉的是计费、多租户、对外 SLA，
没去掉"任何能连上端口的人都能让服务往磁盘写文件"这一条。

刻意不做用户体系：集团内网该由已有的反向代理与单点登录决定"你是谁"，
这里只保证"没带令牌的服务根本不接请求"。
"""
import json
import time

import pytest

from drawform.service.app import create_app


def _client(tmp_path, **kwargs):
    app = create_app(tmp_path / "work", **kwargs)
    app.testing = True
    return app.test_client()


def _auth(token="s3cret"):
    return {"Authorization": f"Bearer {token}"}


def test_the_service_refuses_to_start_without_a_token(tmp_path):
    """默认必须是"关"。启动时不配令牌就悄悄开着一个能写盘的端口，
    比不启动更糟 —— 运维会以为内网就等于安全。
    """
    with pytest.raises(RuntimeError) as caught:
        create_app(tmp_path / "work", token="")

    assert "token" in str(caught.value).lower() or "令牌" in str(caught.value)


def test_requests_without_a_token_are_rejected(tmp_path, bearing_seat_step):
    http = _client(tmp_path, token="s3cret")

    assert http.get("/jobs/whatever").status_code == 401
    posted = http.post("/jobs", data={"step": (open(bearing_seat_step, "rb"), "a.step")},
                       content_type="multipart/form-data")
    assert posted.status_code == 401, "未鉴权就连排队都不该发生"


def test_a_wrong_token_is_rejected_the_same_way(tmp_path):
    http = _client(tmp_path, token="s3cret")

    wrong = dict(Authorization="Bearer nope")
    assert http.get("/jobs/whatever", headers=wrong).status_code == 401


def test_the_right_token_gets_through(tmp_path, bearing_seat_step):
    http = _client(tmp_path, token="s3cret")

    posted = http.post("/jobs", headers=_auth(),
                       data={"step": (open(bearing_seat_step, "rb"), "a.step")},
                       content_type="multipart/form-data")
    assert posted.status_code == 202
    assert json.loads(posted.data)["job_id"]


def test_an_oversized_upload_is_refused_instead_of_filling_the_disk(tmp_path,
                                                                    bearing_seat_step):
    """没上限的上传接口 = 一个请求就能把服务器写满。
    这里把上限调到比夹具还小，测的是"有这道闸"，不是某个具体数字。
    """
    http = _client(tmp_path, token="s3cret", max_upload_bytes=64)

    refused = http.post("/jobs", headers=_auth(),
                        data={"step": (open(bearing_seat_step, "rb"), "a.step")},
                        content_type="multipart/form-data")
    assert refused.status_code == 413


def test_finished_jobs_remain_available_for_evidence_review(tmp_path,
                                                            bearing_seat_step):
    """已确认的留存规则：不按结束任务数量自动删除证据。"""
    http = _client(tmp_path, token="s3cret")

    ids = []
    for _ in range(4):
        posted = http.post("/jobs", headers=_auth(),
                           data={"step": (open(bearing_seat_step, "rb"), "a.step")},
                           content_type="multipart/form-data")
        ids.append(json.loads(posted.data)["job_id"])
        _wait(http, ids[-1])

    alive = [job_id for job_id in ids if _status(http, job_id) is not None]
    assert alive == ids, "较早任务不能因数量增加而消失"
    assert ids[-1] in alive, "最新的任务被自己挤掉了"
    assert (tmp_path / "work" / ids[-1]).is_dir()


def _status(http, job_id):
    response = http.get(f"/jobs/{job_id}", headers=_auth())
    return json.loads(response.data)["state"] if response.status_code == 200 else None


def _wait(http, job_id, timeout=180.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = _status(http, job_id)
        if state in ("drawn", "refused", "crashed"):
            return state
        time.sleep(0.05)
    raise AssertionError(f"任务 {job_id} 超时未结束")

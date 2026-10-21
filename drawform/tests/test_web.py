"""单页前端：把"能出图"变成能点的东西。

产品形态定的是网页端（不做桌面 App），而网页只做三件事：看图、改机器拿不准的地方、
拖模板进来。这一版先做第一件的一半 —— 上传、看图、下载四件套、看"我做了哪些假设"。

不引入任何前端构建链：这台机器 16 GB、迭代节奏一天一版，养不起 npm。
一个 HTML 字符串 + 原生 fetch 够演示用，也够内网真实用一小阵。
"""
import json
import pathlib
import time

import pytest

from drawform.service.app import create_app

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def http(tmp_path):
    app = create_app(tmp_path / "work", token=TOKEN)
    app.testing = True
    return app.test_client()


def _draw(http, step):
    posted = http.post("/jobs", headers=AUTH,
                       data={"step": (open(step, "rb"), "bearing.step")},
                       content_type="multipart/form-data")
    job_id = json.loads(posted.data)["job_id"]
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        body = json.loads(http.get(f"/jobs/{job_id}", headers=AUTH).data)
        if body["state"] in ("drawn", "refused", "crashed"):
            return job_id, body
        time.sleep(0.05)
    raise AssertionError(f"任务 {job_id} 超时：{body}")


def test_the_page_itself_needs_no_token_but_the_api_does(http):
    """页面是空壳，放开它不降低安全性；API 一律仍要 Bearer。

    这条边界要写明，否则以后有人"顺手"给 /jobs 也开免鉴权。
    """
    page = http.get("/")
    assert page.status_code == 200
    assert b"<form" in page.data

    assert http.get("/jobs").status_code == 401
    assert http.post("/jobs").status_code == 401


def test_the_page_offers_upload_view_download_and_the_assumption_panel(http):
    """演示要点的四样东西必须在同一页上，不能靠 curl。"""
    body = http.get("/").text

    assert 'name="step"' in body, "没有上传入口"
    assert 'id="drawing"' in body, "没有放 SVG 的图区"
    # 下载链接是 JS 按这个列表生成的，所以查列表本身；写死四个 data-kind 反而会和实现脱节
    assert '["dxf","svg","pdf","json"]' in body.replace(" ", ""), f"下载区缺出口"
    assert "出图说明" in body, "假设清单没有展示位 —— 这是这次演示最该被看到的东西"


def test_the_page_offers_a_question_box_and_preset_questions(http):
    """"这张图为什么这么画"要能点出来，不能靠人先想起来去 curl 一个接口。"""
    body = http.get("/").text

    assert 'id="qform"' in body and 'id="answers"' in body
    assert "问这张图" in body
    assert "CHIPS" in body and "公差" in body, "没有预置问题，演示时没人会现编一句"


def test_the_page_offers_the_three_review_decisions_and_the_real_reason_list(http):
    """复核三个动作 + 理由分类要在页面上，否则试点期只能靠人工登记，数据一定收不齐。"""
    from drawform.review import REASONS

    body = http.get("/").text

    for decision in ("accepted", "amended", "rejected"):
        assert f'data-d="{decision}"' in body, f"缺 {decision} 按钮"
    assert "__REASONS__" not in body, "理由分类没被替换进去"
    for key in REASONS:
        assert f'value="{key}"' in body, f"页面上没有 {key} 这条理由"
    # 提问的预置按钮与复核按钮共用 .chip 类；选择器不收窄的话点"驳回"会连带触发提问
    assert 'querySelectorAll("#chips .chip")' in body


def test_the_job_list_reports_states_so_a_page_can_poll(http, bearing_seat_step):
    step = bearing_seat_step
    job_id, drawn = _draw(http, step)

    listed = json.loads(http.get("/jobs", headers=AUTH).data)

    assert [j["job_id"] for j in listed["jobs"]] == [job_id]
    assert listed["jobs"][0]["state"] == "drawn"


def test_the_delivered_json_carries_assumptions_the_page_can_render(http, bearing_seat_step):
    """页面上那份"出图说明"不是前端文案，是 JSON 里真有的条目。"""
    step = bearing_seat_step
    job_id, _ = _draw(http, step)

    document = json.loads(http.get(f"/jobs/{job_id}/files/json", headers=AUTH).data)

    ids = {item["id"] for item in document["assumptions"]}
    assert "general-tolerance" in ids
    assert "views-fixed" in ids
    for item in document["assumptions"]:
        assert item["basis"], f"{item['id']} 没有依据，读的人无从核对"


def test_asking_about_a_drawn_job_returns_the_fact_it_rests_on(http, bearing_seat_step):
    job_id, _ = _draw(http, bearing_seat_step)

    reply = http.post("/ask", headers=AUTH,
                      json={"job_id": job_id, "question": "这张图的公差是多少？"})
    body = json.loads(reply.data)

    assert reply.status_code == 200
    assert body["answers"][0]["fact_id"] == "general-tolerance"
    assert body["answers"][0]["basis"], "答案必须能被核对，光有结论不行"
    assert body["answers"][0]["checked"] is False


def test_asking_something_outside_the_computed_facts_is_refused(http, bearing_seat_step):
    job_id, _ = _draw(http, bearing_seat_step)

    body = json.loads(http.post("/ask", headers=AUTH,
                                json={"job_id": job_id,
                                      "question": "这批件该用什么热处理工艺？"}).data)

    assert [a["fact_id"] for a in body["answers"]] == ["no-answer"]


def test_asking_needs_the_token_like_every_other_endpoint(http, bearing_seat_step):
    job_id, _ = _draw(http, bearing_seat_step)

    assert http.post("/ask", json={"job_id": job_id, "question": "公差"}).status_code == 401


def test_a_refused_job_says_there_is_nothing_to_answer_instead_of_answering_nothing(
        http, refused_step):
    """被门拦下的任务没有 JSON。返回空答案会被前端读成"这张图没有问题"。"""
    job_id, drawn = _draw(http, refused_step)
    assert drawn["state"] == "refused", "这条测试要的就是被拦下的那种任务"

    reply = http.post("/ask", headers=AUTH,
                      json={"job_id": job_id, "question": "公差"})

    assert reply.status_code == 409
    assert json.loads(reply.data)["failed"], "409 里要带上被拦下的原因 id"


def _draft_bytes(http, job_id):
    return http.get(f"/jobs/{job_id}/files/dxf", headers=AUTH).data


def _draft_of(http, job_id):
    """把机器底稿落到本地文件再传回去：生效版本的入口收的是文件，不是内存串。"""
    import pathlib
    import tempfile

    path = pathlib.Path(tempfile.mkdtemp()) / "draft.dxf"
    path.write_bytes(_draft_bytes(http, job_id))
    return path


def _sha(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def _sha_of(http, job_id):
    return _sha(_draft_bytes(http, job_id))


def _amend(client, job_id, path, sha, who="设计-甲"):
    with open(path, "rb") as handle:
        return client.post(f"/jobs/{job_id}/amended", headers=AUTH,
                           data={"file": (handle, "amended.dxf"),
                                 "reviewer": who, "draft_sha": sha},
                           content_type="multipart/form-data")


def test_each_configured_token_is_attributed_instead_of_anonymous(http):
    """共享令牌 + "改后图算生效版本" 合起来 = 任何拿到令牌的人都能产出生效图纸。
    多令牌至少让这件事能追溯到席位。"""
    from drawform.service.app import create_app

    app = create_app("/tmp/unused", token="设计-甲=tokA,工艺-乙=tokB")
    app.testing = True
    client = app.test_client()

    assert client.get("/jobs", headers={"Authorization": "Bearer tokB"}).status_code == 200
    assert client.get("/jobs", headers={"Authorization": "Bearer tokC"}).status_code == 401


def test_an_amended_sheet_becomes_effective_without_touching_the_draft(
        http, bearing_seat_step, tmp_path):
    job_id, _ = _draw(http, bearing_seat_step)
    draft = _draft_bytes(http, job_id)

    reply = _amend(http, job_id, _draft_of(http, job_id), _sha(draft))

    assert reply.status_code == 201, reply.data
    got = http.get(f"/jobs/{job_id}/effective", headers=AUTH)
    assert got.status_code == 200
    assert got.data == draft, "生效版本就是传回来的那一份，一个字节都不该再改"
    assert _draft_bytes(http, job_id) == draft, "回写不许动机器底稿"
    body = json.loads(http.get(f"/jobs/{job_id}", headers=AUTH).data)
    assert "amended-1" in body["files"]


# ---------------------------------------------------------------------------
# 决策②（产品负责人 2026-10-09 批准）：一份底稿允许多份生效版本。
#
# 只收一份意味着第二版必须**覆盖**第一版，而覆盖掉的那一版会在审计里留下一条指向
# 已被替换的文件的记录 —— 审计的价值全在于"当时那张图现在还能不能拿出来对"。
# 链：机器底稿 → amended-1 → amended-2 …，每一版只与它的直接父版比。
# ---------------------------------------------------------------------------


def test_a_second_effective_version_chains_instead_of_replacing_the_first(
        http, bearing_seat_step, amend_sheet):
    job_id, _ = _draw(http, bearing_seat_step)
    draft = _draft_bytes(http, job_id)

    first = _amend(http, job_id, amend_sheet(draft, text="99.5"), _sha(draft))
    assert json.loads(first.data)["review"]["version"] == 1
    head = http.get(f"/jobs/{job_id}/effective", headers=AUTH).data

    second = _amend(http, job_id, amend_sheet(head, drop=True, index=1), _sha(head))
    assert json.loads(second.data)["review"]["version"] == 2

    assert http.get(f"/jobs/{job_id}/files/amended-1", headers=AUTH).data == head
    assert http.get(f"/jobs/{job_id}/effective", headers=AUTH).data != head
    assert _draft_bytes(http, job_id) == draft, "链是加出来的，不是覆盖出来的"


def test_an_upload_based_on_a_superseded_version_is_rejected(
        http, bearing_seat_step, amend_sheet):
    """第二版必须改自当前头版。拿底稿改第二次，中间那一版就被无声盖掉了。"""
    job_id, _ = _draw(http, bearing_seat_step)
    draft = _draft_bytes(http, job_id)
    _amend(http, job_id, amend_sheet(draft, text="99.5"), _sha(draft))

    reply = _amend(http, job_id, amend_sheet(draft, text="88.8"), _sha(draft))

    assert reply.status_code == 409
    assert "版本" in json.loads(reply.data)["error"]
    rows = json.loads(http.get(f"/jobs/{job_id}/review", headers=AUTH).data)["reviews"]
    assert len(rows) == 1, "被拒的回写不许留审计行"
    assert http.get(f"/jobs/{job_id}/files/amended-2", headers=AUTH).status_code == 404


def test_each_effective_version_is_its_own_audit_row(
        http, bearing_seat_step, amend_sheet):
    job_id, _ = _draw(http, bearing_seat_step)
    draft = _draft_bytes(http, job_id)

    first = json.loads(_amend(http, job_id, amend_sheet(draft, text="99.5"),
                              _sha(draft)).data)["review"]
    head = http.get(f"/jobs/{job_id}/effective", headers=AUTH).data
    second = json.loads(_amend(http, job_id, amend_sheet(head, drop=True, index=1),
                               _sha(head)).data)["review"]

    assert [first["version"], second["version"]] == [1, 2]
    assert second["draft_sha"] == first["effective_sha"], "第二版要写明它改的是第一版"
    # 每一版的差异只与直接父版比：第二版只是删了一条，它不该背上第一版那个错号。
    assert first["diff"] == {"value_changed": 1}, first["diff"]
    assert second["diff"] == {"removed": 1}, second["diff"]


def test_the_diff_endpoint_answers_how_much_the_machine_got_wrong(
        http, bearing_seat_step, amend_sheet):
    """链上每一版与**直接父版**比；/diff 答的是"机器底稿一共要改几处"。"""
    job_id, _ = _draw(http, bearing_seat_step)
    draft = _draft_bytes(http, job_id)

    _amend(http, job_id, amend_sheet(draft, text="99.5"), _sha(draft))
    head = http.get(f"/jobs/{job_id}/effective", headers=AUTH).data
    _amend(http, job_id, amend_sheet(head, drop=True, index=1), _sha(head))

    body = json.loads(http.get(f"/jobs/{job_id}/diff", headers=AUTH).data)

    assert body["diff"] == {"value_changed": 1, "removed": 1}, body


def test_the_package_carries_the_whole_effective_chain(
        http, bearing_seat_step, amend_sheet):
    import io
    import zipfile

    job_id, _ = _draw(http, bearing_seat_step)
    draft = _draft_bytes(http, job_id)
    _amend(http, job_id, amend_sheet(draft, text="99.5"), _sha(draft))
    head = http.get(f"/jobs/{job_id}/effective", headers=AUTH).data
    _amend(http, job_id, amend_sheet(head, drop=True, index=1), _sha(head))

    blob = http.get(f"/jobs/{job_id}/package", headers=AUTH).data
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        names = archive.namelist()

    assert any(n.endswith("amended-1.dxf") for n in names), "第一版被覆盖掉就等于没归档"
    assert any(n.endswith("amended-2.dxf") for n in names)


def test_the_effective_version_lists_which_dimension_changed(
        http, bearing_seat_step, amend_sheet):
    """只报"改了 1 处"的复核台没人会用：要当场看到是哪一条、从几改成几。"""
    job_id, _ = _draw(http, bearing_seat_step)
    draft = _draft_bytes(http, job_id)

    body = json.loads(_amend(http, job_id, amend_sheet(draft, text="99.5"),
                             _sha(draft)).data)

    row = body["changes"][0]
    assert row["action"] == "value_changed"
    assert row["after"] == "99.5" and row["before"], row
    assert len(row["at"]) == 2, "带位置，页面才能把这条挂回图上那条尺寸"
    # 明细必须进审计行：不然事后只查得到"改了几处"，查不到"改了哪一处"
    rows = json.loads(http.get(f"/jobs/{job_id}/review", headers=AUTH).data)["reviews"]
    assert rows[-1]["changes"][0]["after"] == "99.5"


def test_the_diff_endpoint_returns_the_same_list(http, bearing_seat_step, amend_sheet):
    job_id, _ = _draw(http, bearing_seat_step)
    draft = _draft_bytes(http, job_id)
    _amend(http, job_id, amend_sheet(draft, text="99.5"), _sha(draft))

    body = json.loads(http.get(f"/jobs/{job_id}/diff", headers=AUTH).data)

    assert [i["after"] for i in body["changes"]] == ["99.5"]


def test_the_page_shows_the_changed_dimensions(http):
    """页面上要有一张逐项清单，不是只有一行计数文案。"""
    body = http.get("/").text

    assert "j.changes" in body.replace(" ", "")
    assert "→" in body, "改前 → 改后要能一眼读出方向"


def test_the_page_retargets_the_next_upload_at_the_new_head(http):
    """页面必须把下一次上传的基线换成刚生效那一版，否则第二版必然吃 409，
    而工程师看到的是"我明明就是在这张图上改的"。"""
    body = http.get("/").text.replace(" ", "")

    assert "draftSha=j.review.effective_sha" in body
    assert "j.review.version" in body, "页面上要显示这是第几版"
    assert "/effective" in body, "没有下载当前生效版本的入口，就没法接着改下一版"


def test_the_job_list_reopens_a_finished_job_so_the_chain_can_continue(http):
    """生效版本链要能"隔天接着改第 3 版"，前提是页面回得去那张任务。

    刷新后 `jobId` 清空，而任务列表只是 `<li>` 里的死文本 —— 点不动的列表等于
    把刚上线的多版链锁在"一次会话内"。
    """
    body = http.get("/").text

    assert "data-job=" in body, "列表项要带上 job id"
    assert 'closest("#jobs li")' in body or 'querySelectorAll("#jobs li")' in body, \
        "列表项没有点击绑定"
    # 被拦下/崩掉的任务没有图可看，点它必须给话，不能静默失败
    assert "没有出图" in body, "点不开的任务要有说明"


def test_a_stale_or_foreign_draft_is_rejected(http, bearing_seat_step):
    """传的是**另一张图的合法 DXF**（或旧版）：能读，但不是这份底稿改出来的。"""
    job_id, _ = _draw(http, bearing_seat_step)
    other = (pathlib.Path(__file__).resolve().parents[2]
             / "tools" / "dwg_verify" / "samples" / "part_0004.dxf")

    reply = _amend(http, job_id, other, "deadbeef")

    assert reply.status_code == 409
    assert "版本" in json.loads(reply.data)["error"] or "对不上" in json.loads(reply.data)["error"]


def test_a_dwg_or_garbage_upload_is_refused_with_the_reason(http, bearing_seat_step):
    """这台机器读不了 DWG。收下就等于承诺了我们做不到的事。"""
    import pathlib

    job_id, _ = _draw(http, bearing_seat_step)
    bad = pathlib.Path("/tmp/notadxf.dxf")
    bad.write_bytes(b"MCAD\x00\x00\x00\x00" + b"\x00" * 512)

    reply = _amend(http, job_id, bad, "x")

    assert reply.status_code == 400
    assert "DXF" in json.loads(reply.data)["error"]


def test_an_upload_alone_records_the_adoption(http, bearing_seat_step):
    """传了生效图却还要再点一次"改后采用"，一定会出现传了没记的漏。"""
    job_id, _ = _draw(http, bearing_seat_step)

    _amend(http, job_id, _draft_of(http, job_id), _sha_of(http, job_id))

    stats = json.loads(http.get("/review/stats", headers=AUTH).data)
    assert stats["decisions"].get("amended") == 1


def test_a_refused_job_cannot_receive_an_effective_version(http, refused_step):
    job_id, body = _draw(http, refused_step)
    assert body["state"] == "refused"

    assert _amend(http, job_id, refused_step, "x").status_code == 409


def test_the_package_endpoint_bundles_what_pdm_needs(http, bearing_seat_step):
    import io
    import zipfile

    job_id, _ = _draw(http, bearing_seat_step)
    _amend(http, job_id, _draft_of(http, job_id), _sha_of(http, job_id))

    blob = http.get(f"/jobs/{job_id}/package", headers=AUTH).data
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        names = archive.namelist()

    assert any(n.endswith(".dxf") and "amended" not in n for n in names), "底稿没进包"
    assert any(n.endswith("amended-1.dxf") for n in names)
    assert any(n.endswith(".json") for n in names)
    assert any("review" in n for n in names), "交付包里没有审计行，PDM 那边就查不到是谁签的"


def test_the_page_offers_an_upload_box_for_the_amended_sheet(http):
    body = http.get("/").text

    assert 'id="amend"' in body and 'name="file"' in body
    assert "生效版本" in body
    # 浏览器里踩过：从 e.target 上取文件拿到的是 undefined，点按钮毫无反应
    assert 'querySelector("#amend input[type=file]").files[0]' in body

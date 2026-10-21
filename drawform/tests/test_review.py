"""复核闭环：接受 / 改后采用 / 驳回，加一份活得比任务久的审计日志。

这一层存在的理由不是"补一个接口"，而是《接入与推广方案》第三节里那条**没闭合的责任链**：
试点期真正要埋的五个数（有没有被采用、改了哪几处、谁驳回的、单张耗时、手工基线）
里，前三个只能从这里来。没有它，试点全靠人工登记 —— 而人工登记的数据一定收不齐。

两条不可谈判的判据写在测试里：
1. **驳回了却没说为什么，等于没驳回** —— 理由分类是必填项；
2. **审计日志必须活得比任务记录久** —— 队列只保留 200 个任务，
   而"这张图谁签的、当时判了什么"是责任链的证据，不能跟着一起被清掉。
"""

from __future__ import annotations

import hashlib
import json
import time

import pytest

from drawform.review import (
    AMENDED,
    ACCEPTED,
    REJECTED,
    ReviewError,
    ReviewLog,
    is_reviewable,
)

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def http(tmp_path):
    from drawform.service.app import create_app

    app = create_app(tmp_path / "work", token=TOKEN)
    app.testing = True
    return app.test_client()


def _draw(http, step, name="part.step"):
    posted = http.post("/jobs", headers=AUTH,
                       data={"step": (open(step, "rb"), name)},
                       content_type="multipart/form-data")
    job_id = json.loads(posted.data)["job_id"]
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        body = json.loads(http.get(f"/jobs/{job_id}", headers=AUTH).data)
        if body["state"] in ("drawn", "refused", "crashed"):
            return job_id, body
        time.sleep(0.05)
    raise AssertionError(f"任务 {job_id} 超时：{body}")


def _draft_bytes(http, job_id):
    return http.get(f"/jobs/{job_id}/files/dxf", headers=AUTH).data


def _accept(http, job_id, who="设计-甲"):
    return http.post(f"/jobs/{job_id}/review", headers=AUTH,
                     json={"decision": ACCEPTED, "reviewer": who})


def test_a_review_is_recorded_and_can_be_read_back(tmp_path, bearing_seat_step, http):
    job_id, _ = _draw(http, bearing_seat_step)

    reply = http.post(f"/jobs/{job_id}/review", headers=AUTH,
                      json={"decision": ACCEPTED, "reviewer": "设计-甲"})
    assert reply.status_code == 201, reply.data

    history = json.loads(http.get(f"/jobs/{job_id}/review", headers=AUTH).data)
    assert [r["decision"] for r in history["reviews"]] == [ACCEPTED]
    assert history["reviews"][0]["reviewer"] == "设计-甲"


def test_a_rejection_without_a_reason_is_refused(tmp_path, bearing_seat_step, http):
    """"驳回了"却不记为什么，试点期就拿不到"机器该学什么"这个唯一有用的信号。"""
    job_id, _ = _draw(http, bearing_seat_step)

    reply = http.post(f"/jobs/{job_id}/review", headers=AUTH,
                      json={"decision": REJECTED, "reviewer": "设计-甲"})

    assert reply.status_code == 400
    assert "reason" in json.loads(reply.data)["error"]


def test_an_unknown_reason_id_is_refused_not_stored(tmp_path, bearing_seat_step, http):
    job_id, _ = _draw(http, bearing_seat_step)

    reply = http.post(f"/jobs/{job_id}/review", headers=AUTH,
                      json={"decision": REJECTED, "reviewer": "设计-甲",
                            "reasons": ["随便写的"]})

    assert reply.status_code == 400
    assert json.loads(http.get(f"/jobs/{job_id}/review", headers=AUTH).data)["reviews"] == []


def test_an_unknown_decision_is_refused(tmp_path, bearing_seat_step, http):
    job_id, _ = _draw(http, bearing_seat_step)

    assert http.post(f"/jobs/{job_id}/review", headers=AUTH,
                     json={"decision": "lgtm", "reviewer": "甲"}).status_code == 400


def test_a_job_without_a_reviewer_name_is_refused(tmp_path, bearing_seat_step, http):
    """审计链的最小要素是"谁"。自称是谁在 SSO 之前不算身份凭证，但不填连自称都没有。"""
    job_id, _ = _draw(http, bearing_seat_step)

    assert http.post(f"/jobs/{job_id}/review", headers=AUTH,
                     json={"decision": ACCEPTED, "reviewer": "  "}).status_code == 400


def test_a_refused_job_cannot_be_accepted(refused_step, tmp_path):
    """被门拦下的任务没有图可采用。返回 409，而不是让它记一条"已接受"。"""
    from drawform.service.app import create_app

    app = create_app(tmp_path / "work", token=TOKEN)
    app.testing = True
    client = app.test_client()
    job_id, body = _draw(client, refused_step)
    assert body["state"] == "refused"

    reply = client.post(f"/jobs/{job_id}/review", headers=AUTH,
                        json={"decision": ACCEPTED, "reviewer": "甲"})

    assert reply.status_code == 409
    assert is_reviewable(body["state"]) is False


def test_review_needs_the_token_like_every_other_endpoint(bearing_seat_step, http):
    job_id, _ = _draw(http, bearing_seat_step)

    assert http.post(f"/jobs/{job_id}/review",
                     json={"decision": ACCEPTED, "reviewer": "甲"}).status_code == 401
    assert http.get(f"/jobs/{job_id}/review").status_code == 401


def test_a_review_for_an_unknown_job_is_404(http):
    assert http.post("/jobs/deadbeef/review", headers=AUTH,
                     json={"decision": ACCEPTED, "reviewer": "甲"}).status_code == 404


def test_the_audit_log_outlives_the_job_records(tmp_path):
    """队列按个数清理任务目录；审计日志是独立的一份 JSONL，不能跟着一起消失。"""
    log = ReviewLog(tmp_path / "work" / "reviews.jsonl")
    log.record(job_id="j1", decision=AMENDED, reviewer="工艺-乙",
               reasons=["extra_dimension"], note="删了重复的厚度")

    import shutil
    shutil.rmtree(tmp_path / "work" / "j1", ignore_errors=True)

    assert [r["decision"] for r in log.for_job("j1")] == [AMENDED]


def test_stats_count_by_decision_and_reason(tmp_path):
    log = ReviewLog(tmp_path / "reviews.jsonl")
    log.record(job_id="a", decision=ACCEPTED, reviewer="甲")
    log.record(job_id="b", decision=REJECTED, reviewer="乙", reasons=["datum_choice"])
    log.record(job_id="c", decision=REJECTED, reviewer="丙", reasons=["datum_choice",
                                                                      "tolerance_missing"])

    stats = log.stats()

    assert stats["decisions"] == {ACCEPTED: 1, REJECTED: 2}
    assert stats["reasons"] == {"datum_choice": 2, "tolerance_missing": 1}


def test_a_malformed_log_line_is_skipped_and_reported_not_silently_ignored(tmp_path):
    """审计文件被人手工编辑过是常态。跳过坏行可以，但必须说有几行没读进来 ——
    悄悄少算的审计数比读不出更危险。"""
    path = tmp_path / "reviews.jsonl"
    log = ReviewLog(path)
    log.record(job_id="ok", decision=ACCEPTED, reviewer="甲")
    path.write_text(path.read_text() + "{不是 json\n")

    assert log.stats()["decisions"] == {ACCEPTED: 1}
    assert log.damaged_lines == 1


def test_an_amended_review_without_reason_is_allowed(tmp_path):
    """改后采用不强制填理由：改了什么由改前改后两张 DXF 对比得出，理由只是补充。"""
    log = ReviewLog(tmp_path / "reviews.jsonl")

    log.record(job_id="a", decision=AMENDED, reviewer="甲")

    assert log.for_job("a")[0]["decision"] == AMENDED


def test_record_rejects_bad_input_at_the_boundary(tmp_path):
    log = ReviewLog(tmp_path / "reviews.jsonl")

    with pytest.raises(ReviewError):
        log.record(job_id="a", decision="approved", reviewer="甲")
    with pytest.raises(ReviewError):
        log.record(job_id="", decision=ACCEPTED, reviewer="甲")


def test_an_acceptance_cannot_carry_reason_codes(tmp_path):
    """理由分类是给"这张图哪里不对"统计用的。接受记录里带理由会把试点统计污染成
    —— 实测就发生过：页面上勾着理由点「直接用」，那条理由被算进了驳回原因分布。"""
    log = ReviewLog(tmp_path / "reviews.jsonl")

    with pytest.raises(ReviewError):
        log.record(job_id="a", decision=ACCEPTED, reviewer="甲", reasons=["extra_dimension"])


def test_the_page_clears_reason_boxes_before_accepting(http):
    """前端必须自己把这条规则做对，否则用户会收到一个莫名其妙的 400。"""
    body = http.get("/").text

    assert "querySelectorAll('#reasons input:checked')" in body
    assert ".forEach(i=>i.checked=false)" in body, "点「直接用」前没清空理由勾选"


def test_the_page_says_the_sheet_is_frozen_right_after_a_numeric_error(http):
    """回传了含数值级错误的生效版本，页面上要当场说明这张图被冻结。

    否则规则只在工程师点一次「直接用」、收到一条 409 时才存在 —— 而他会以为是坏了。
    """
    body = http.get("/").text

    assert "j.blocking" in body.replace(" ", ""), "冻结提示必须挂在后端算出的 blocking 位上"
    assert "冻结" in body and "直接用" in body, "提示要说清代价，不只是「有错误」"


def test_each_reason_carries_the_judgement_text_the_reviewer_sees():
    """口径 v2 的每条理由都要有一句当场看得懂的判据，否则兜底项会变成垃圾桶。"""
    from drawform.review import REASONS

    assert len(REASONS) == 14
    for key, hint in REASONS.items():
        assert len(hint) >= 6, f"{key} 没有判据文案"
    assert "other_needs_text" in REASONS


def test_group_a_is_the_blocking_kind_and_is_counted_separately(tmp_path):
    """「数值标错了」和「漏标了」严重度差一个量级。v1 把它们混在一起统计。"""
    log = ReviewLog(tmp_path / "reviews.jsonl")
    log.record(job_id="a", decision=REJECTED, reviewer="甲", reasons=["wrong_value"])
    log.record(job_id="b", decision=REJECTED, reviewer="乙", reasons=["missing_dimension"])

    stats = log.stats()

    assert stats["blocking"] == 1, "A 组（数值正确性）必须单独成项"
    assert stats["reasons"]["wrong_value"] == 1


def test_other_needs_text_must_carry_a_note(tmp_path):
    log = ReviewLog(tmp_path / "reviews.jsonl")

    with pytest.raises(ReviewError):
        log.record(job_id="a", decision=REJECTED, reviewer="甲",
                   reasons=["other_needs_text"], note="")
    log.record(job_id="b", decision=REJECTED, reviewer="乙",
               reasons=["other_needs_text"], note="剖面方向反了")
    assert log.for_job("b")


def test_an_effective_version_records_its_parent_hash(tmp_path):
    """生效版本必须说清"基于哪一版改的"，否则审计里是一条改了什么都不知道的记录。"""
    log = ReviewLog(tmp_path / "reviews.jsonl")

    row = log.record(job_id="a", decision=AMENDED, reviewer="甲",
                     draft_sha="ab12cd34", effective_sha="ef567890",
                     diff={"removed": 1, "value_changed": 2})

    assert row["draft_sha"] == "ab12cd34"
    assert row["diff"]["value_changed"] == 2


def test_stats_roll_up_diff_actions(tmp_path):
    log = ReviewLog(tmp_path / "reviews.jsonl")
    log.record(job_id="a", decision=AMENDED, reviewer="甲",
               diff={"removed": 1, "value_changed": 2})
    log.record(job_id="b", decision=AMENDED, reviewer="乙",
               diff={"value_changed": 1})

    assert log.stats()["diff"] == {"removed": 1, "value_changed": 3}


# ---------------------------------------------------------------------------
# 决策①（产品负责人 2026-10-09 批准）：出现数值级错误 → 立刻冻结这张图。
#
# 「直接用」这句话的含义是**机器底稿可以照它加工**。一旦有人当场指出某个数是错的
# —— 不管是改后图比对算出来的，还是驳回时勾出来的 —— 再让人标"直接用"就是在审计里
# 留一条自相矛盾的记录，而试点期这份审计是唯一的验收依据。
#
# 冻结只拦 accepted，不拦「改后采用」与「驳回」：那条通道正是修掉错误的地方。
# ---------------------------------------------------------------------------

def _amend(http, job_id, path, sha, who="设计-甲"):
    with open(path, "rb") as handle:
        return http.post(f"/jobs/{job_id}/amended", headers=AUTH,
                         data={"file": (handle, "amended.dxf"),
                               "reviewer": who, "draft_sha": sha},
                         content_type="multipart/form-data")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_a_numeric_error_found_in_the_effective_version_freezes_the_sheet(
        http, bearing_seat_step, amend_sheet):
    job_id, _ = _draw(http, bearing_seat_step)
    draft = _draft_bytes(http, job_id)
    amended = amend_sheet(draft, text="99.5")

    posted = _amend(http, job_id, amended, _sha(draft))
    assert json.loads(posted.data)["blocking"] is True, "改数值必须算成数值级"

    reply = _accept(http, job_id)
    assert reply.status_code == 409, "有数值级错误的图不许标「直接用」"
    error = json.loads(reply.data)["error"]
    assert "数值级" in error and "生效版本" in error, "拦下要能说清为什么、下一步做什么"
    # 冻结是拦下这条决定，不是清掉已有记录
    rows = json.loads(http.get(f"/jobs/{job_id}/review", headers=AUTH).data)["reviews"]
    assert [r["decision"] for r in rows] == [AMENDED]


def test_a_rejection_for_a_wrong_number_freezes_direct_acceptance(
        http, bearing_seat_step):
    """错误是复核人**勾出来的**（没回传图纸）时，同样冻结。"""
    job_id, _ = _draw(http, bearing_seat_step)

    rejected = http.post(f"/jobs/{job_id}/review", headers=AUTH,
                         json={"decision": REJECTED, "reviewer": "甲",
                               "reasons": ["wrong_value"]})
    assert rejected.status_code == 201, rejected.data

    assert _accept(http, job_id).status_code == 409


def test_dropping_a_redundant_dimension_does_not_freeze_the_sheet(
        http, bearing_seat_step, amend_sheet):
    """对照组：删掉一条多标的尺寸是**取舍**，不是把这个数写错。

    没有这条，「任何改动都冻结」也能全绿 —— 而那条规则会把工具废掉。
    """
    job_id, _ = _draw(http, bearing_seat_step)
    draft = _draft_bytes(http, job_id)

    posted = _amend(http, job_id, amend_sheet(draft, drop=True), _sha(draft))
    assert json.loads(posted.data)["blocking"] is False

    assert _accept(http, job_id).status_code == 201


def test_a_style_rejection_does_not_freeze_the_sheet(http, bearing_seat_step):
    """对照组：C 组（图面表达）分歧不该被当成数值错误。"""
    job_id, _ = _draw(http, bearing_seat_step)

    http.post(f"/jobs/{job_id}/review", headers=AUTH,
              json={"decision": REJECTED, "reviewer": "甲",
                    "reasons": ["unreadable_sheet"]})

    assert _accept(http, job_id).status_code == 201


def test_the_freeze_covers_only_the_sheet_it_was_found_on(
        http, bearing_seat_step, plate_step, amend_sheet):
    """冻结按任务算。做成全局开关，一张图出错就没人能用这个工具了。"""
    first, _ = _draw(http, bearing_seat_step)
    second, _ = _draw(http, plate_step, "plate.step")
    draft = _draft_bytes(http, first)

    _amend(http, first, amend_sheet(draft, text="99.5"), _sha(draft))

    assert _accept(http, first).status_code == 409
    assert _accept(http, second).status_code == 201, "别的图没被判过数值错误"


def test_a_frozen_sheet_can_still_be_rejected_and_stays_frozen(
        http, bearing_seat_step, amend_sheet):
    """冻结拦的只有「底稿直接可用」这一句，且不会因为后来又操作一次就自己解开。

    能继续驳回是必须的：错误被冻结之后如果连记录都记不了，台账就断在这里。
    """
    job_id, _ = _draw(http, bearing_seat_step)
    draft = _draft_bytes(http, job_id)

    _amend(http, job_id, amend_sheet(draft, text="99.5"), _sha(draft))

    again = http.post(f"/jobs/{job_id}/review", headers=AUTH,
                      json={"decision": REJECTED, "reviewer": "乙",
                            "reasons": ["wrong_value"]})
    assert again.status_code == 201, again.data
    assert _accept(http, job_id).status_code == 409


def test_the_log_knows_which_sheets_carry_a_numeric_error(tmp_path):
    log = ReviewLog(tmp_path / "reviews.jsonl")
    log.record(job_id="a", decision=REJECTED, reviewer="甲", reasons=["wrong_symbol"])
    log.record(job_id="b", decision=REJECTED, reviewer="乙", reasons=["datum_choice"])
    log.record(job_id="c", decision=AMENDED, reviewer="丙", reasons=["wrong_count"])

    assert sorted(log.frozen()) == ["a", "c"], "A 组出现过即冻结"
    assert "b" not in log.frozen()

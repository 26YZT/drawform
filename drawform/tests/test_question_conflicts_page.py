"""状态冲突必须有页面内的解释与可操作下一步。"""

from drawform.service.app import PAGE
from tests.test_question_page import Page


def test_state_notice_is_live_neutral_and_initially_hidden():
    notice = Page(PAGE).by_id("state-notice")
    assert notice["attrs"]["role"] == "status"
    assert notice["attrs"]["aria-live"] == "polite"
    assert "hidden" in notice["attrs"]
    assert "t-frozen" not in notice["attrs"].get("class", "")


def test_conflicts_offer_refresh_review_upload_and_latest_download_actions():
    page = Page(PAGE)
    notice = page.by_id("state-notice")
    actions = {node["attrs"].get("data-recovery"): node for node in page.nodes
               if notice in node["parents"] and "data-recovery" in node["attrs"]}
    assert set(actions) == {"refresh", "amend", "reject", "latest", "upload", "token", "jobs", "questions"}
    assert all(node["tag"] == "button" and node["attrs"]["type"] == "button"
               for node in actions.values())
    assert "重新读取问题卡" in actions["refresh"]["text"]
    assert "下载" in actions["latest"]["text"]


def test_conflict_details_and_state_have_separate_text_slots():
    page = Page(PAGE)
    notice = page.by_id("state-notice")
    for name in ("notice-title", "notice-detail"):
        assert notice in page.by_id(name)["parents"]

"""答案流程需要可操作的版本比较、等待反馈和交付入口。"""

from drawform.service.app import PAGE
from tests.test_question_page import Page


def test_before_and_after_are_native_buttons_with_pressed_state():
    page = Page(PAGE)
    for name in ("vb-before", "vb-after"):
        button = page.by_id(name)
        assert button["tag"] == "button" and button["attrs"]["type"] == "button"
        assert "aria-pressed" in button["attrs"]
    assert "disabled" in page.by_id("vb-after")["attrs"]


def test_history_selection_is_labelled_and_has_a_return_to_current_action():
    page = Page(PAGE)
    assert page.by_id("version-select")["tag"] == "select"
    assert any(n["tag"] == "label" and n["attrs"].get("for") == "version-select" for n in page.nodes)
    assert page.by_id("back-to-current")["tag"] == "button"
    assert page.by_id("history-notice")["attrs"]["role"] == "status"


def test_answer_receipt_and_refresh_are_available_without_reloading_the_page():
    page = Page(PAGE)
    assert page.by_id("answer-receipt")["attrs"]["aria-live"] == "polite"
    assert page.by_id("refresh-job")["tag"] == "button"
    assert "location.reload" not in PAGE


def test_delivery_package_is_an_explicit_action_disabled_until_a_version_exists():
    page = Page(PAGE)
    button = page.by_id("package-download")
    assert button["tag"] == "button" and "disabled" in button["attrs"]
    assert "交付包" in button["text"]

"""键盘和复核控件的语义契约，交互用浏览器另验。"""

from drawform.review import NOTE_REQUIRED_REASONS, REASON_GROUPS
from drawform.service.app import PAGE
from tests.test_question_page import Page


def test_drawing_can_take_keyboard_focus_and_explains_arrow_key_panning():
    page = Page(PAGE)
    stage = page.by_id("stage")
    assert stage["attrs"]["tabindex"] == "0"
    assert "方向键" in page.by_id(stage["attrs"]["aria-describedby"])["text"]


def test_zoom_has_four_native_buttons_and_a_live_display_only_scale_label():
    page = Page(PAGE)
    zoom = page.by_id("zoomctl")
    buttons = [node for node in page.nodes if node["tag"] == "button" and zoom in node["parents"]]
    assert {node["attrs"].get("data-z") for node in buttons} == {"-", "fit", "1", "+"}
    assert all(node["attrs"]["type"] == "button" for node in buttons)
    assert page.by_id("zoominfo")["attrs"]["aria-live"] == "polite"
    assert "显示" in page.by_id("zoom-hint")["text"]


def test_review_reason_groups_and_note_requirement_come_from_the_backend():
    page = Page(PAGE)
    reasons = [node for node in page.nodes if node["tag"] == "input" and node["attrs"].get("name") == "reason"]
    assert {node["attrs"]["value"]: node["attrs"]["data-group"] for node in reasons} == REASON_GROUPS
    assert {node["attrs"]["value"] for node in reasons if node["attrs"]["data-note-required"] == "true"} == NOTE_REQUIRED_REASONS
    assert "不能" in page.by_id("blocking-warning")["text"]


def test_review_note_has_a_label_and_required_note_instructions():
    page = Page(PAGE)
    note = page.by_id("review-note")
    assert note["tag"] == "textarea"
    assert any(n["tag"] == "label" and n["attrs"].get("for") == "review-note" for n in page.nodes)
    assert "必填" in page.by_id(note["attrs"]["aria-describedby"])["text"]


def test_task_ask_review_shortcuts_are_exposed_and_history_rows_are_native_buttons():
    page = Page(PAGE)
    for section, shortcut in (("task-section", "U"), ("ask-section", "Q"), ("review-section", "R")):
        parent = page.by_id(section)
        assert any(n["tag"] == "button" and parent in n["parents"]
                   and n["attrs"].get("aria-keyshortcuts") == shortcut for n in page.nodes)
    row = page.by_id("job-row")
    assert any(n["tag"] == "button" and row in n["parents"] for n in page.nodes)

"""问题卡的封闭输入、未知状态和可提交条件必须在原生控件中明确表达。"""

from drawform.service.answers import MAX_NOTE_LENGTH
from drawform.service.app import PAGE
from tests.test_question_page import Page


def test_question_card_has_a_legend_and_all_four_explanation_fields():
    page = Page(PAGE)
    template = page.by_id("question-card")
    nodes = [node for node in page.nodes if template in node["parents"]]
    assert any(n["tag"] == "fieldset" for n in nodes)
    assert any(n["tag"] == "legend" for n in nodes)
    for field in ("target", "claim", "basis", "impact"):
        assert any(f"data-{field}" in n["attrs"] for n in nodes)


def test_candidates_are_native_radios_without_an_automatic_default_or_numeric_input():
    page = Page(PAGE)
    template = page.by_id("question-option")
    radio = next(n for n in page.nodes if n["tag"] == "input" and template in n["parents"])
    assert radio["attrs"]["type"] == "radio"
    assert "checked" not in radio["attrs"]
    assert any(n["tag"] == "label" for n in radio["parents"])
    assert not any(n["tag"] == "input" and n["attrs"].get("type") == "number" for n in page.nodes)


def test_unknown_is_an_independent_button_and_notes_have_the_server_limit():
    page = Page(PAGE)
    template = page.by_id("question-card")
    nodes = [n for n in page.nodes if template in n["parents"]]
    unknown = next(n for n in nodes if n["attrs"].get("data-action") == "unknown")
    assert unknown["tag"] == "button" and "我不知道" in unknown["text"]
    assert "hidden" not in unknown["attrs"]
    note = next(n for n in nodes if n["tag"] == "textarea")
    assert note["attrs"]["maxlength"] == str(MAX_NOTE_LENGTH)
    assert any("不参与数值" in n["text"] for n in note["parents"] if n["tag"] == "label")


def test_submission_starts_disabled_and_has_a_remaining_question_status():
    page = Page(PAGE)
    assert "disabled" in page.by_id("submit-answers")["attrs"]
    assert page.by_id("question-progress")["attrs"]["role"] == "status"
    assert page.by_id("who") in page.nodes
    assert "question-section" in [n["attrs"].get("id") for n in page.by_id("who")["parents"]]


def test_candidate_truncation_is_explained_on_the_card():
    page = Page(PAGE)
    node = next((n for n in page.nodes if "data-truncated" in n["attrs"]), None)
    assert node is not None and "截断" in node["text"]

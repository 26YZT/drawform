"""颜色只表达约定的语义，工程事实和人的选择分别保留文字标记。"""

from drawform.service.app import PAGE
from tests.test_question_page import Page


def test_semantic_palette_uses_the_approved_tokens():
    for declaration in ("--pass:#1B7F4B", "--machine:#B26A00", "--outlib:#6B4FBB",
                        "--frozen:#B3261E", "--accent:#1E6FD9"):
        assert declaration in PAGE


def test_machine_assumptions_have_a_word_badge_and_an_unverified_notice():
    page = Page(PAGE)
    template = page.by_id("assumption-card")
    nodes = [n for n in page.nodes if template in n["parents"]]
    assert any("data-fact-status" in n["attrs"] and "机器已决定" in n["text"] for n in nodes)
    assert "未与集团真实图纸核对" in template["text"]


def test_answer_records_have_their_own_explicit_status_and_non_numeric_note_label():
    page = Page(PAGE)
    template = page.by_id("answer-record")
    nodes = [n for n in page.nodes if template in n["parents"]]
    assert any("data-answer-status" in n["attrs"] for n in nodes)
    assert any("data-answer-choice" in n["attrs"] for n in nodes)
    assert "不参与数值" in template["text"]

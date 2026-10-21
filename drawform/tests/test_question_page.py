"""P0.3 页面结构与原生控件契约；交互另外通过真实浏览器验收。"""

from html.parser import HTMLParser

import pytest

from drawform.service.app import PAGE


class Page(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.nodes, self.stack = [], []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        node = {"tag": tag, "attrs": dict(attrs), "parents": tuple(self.stack), "text": ""}
        self.nodes.append(node)
        if tag not in ("meta", "input", "br", "hr", "link", "img"):
            self.stack.append(node)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index]["tag"] == tag:
                self.stack = self.stack[:index]
                return

    def handle_data(self, data):
        for node in self.stack:
            node["text"] += data

    def by_id(self, name):
        node = next((node for node in self.nodes if node["attrs"].get("id") == name), None)
        assert node is not None, f"页面缺少 {name}"
        return node


def test_sidebar_has_five_sections_in_the_required_order_and_default_state():
    page = Page(PAGE)
    sections = [node for node in page.nodes if node["tag"] == "section"]
    assert [(n["attrs"].get("id"), n["attrs"].get("data-open")) for n in sections] == [
        ("question-section", "1"), ("assumption-section", "1"), ("ask-section", "0"),
        ("review-section", "0"), ("task-section", "0")]


def test_each_section_uses_a_native_keyboard_button_with_an_expanded_state():
    page = Page(PAGE)
    sections = [n for n in page.nodes if n["tag"] == "section"]
    assert len(sections) == 5
    for section in sections:
        button = next((node for node in page.nodes if node["tag"] == "button"
                       and section in node["parents"] and "aria-expanded" in node["attrs"]), None)
        assert button is not None
        assert button["attrs"]["aria-expanded"] == ("true" if section["attrs"]["data-open"] == "1" else "false")
        assert section in page.by_id(button["attrs"]["aria-controls"])["parents"]


def test_a_verified_empty_question_list_has_its_own_visible_message_slot():
    page = Page(PAGE)
    empty = page.by_id("questions-empty")
    assert "暂无待确认项" in empty["text"]
    assert "hidden" in empty["attrs"], "尚未出图时不能冒充没有问题"
    assert "上传" in page.by_id("questions-context")["text"]


def test_existing_upload_review_ask_download_and_history_controls_remain():
    page = Page(PAGE)
    for name in ("up", "amend", "qform", "answers", "rvBtns", "who", "token", "jobs", "downloads", "drawing"):
        page.by_id(name)
    assert {n["attrs"].get("name") for n in page.nodes if n["tag"] == "input"} >= {"step", "file", "reason"}


def test_page_declares_chinese_and_keeps_the_drawing_review_boundary():
    page = Page(PAGE)
    assert next(n for n in page.nodes if n["tag"] == "html")["attrs"]["lang"] == "zh-CN"
    assert "未经制图审核" in next(n for n in page.nodes if n["tag"] == "footer")["text"]
    assert "SSO" in page.by_id("who")["attrs"]["placeholder"]

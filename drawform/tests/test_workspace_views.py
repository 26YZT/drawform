"""WS-1 contracts: persistent project view and presentation-only tool grouping.

These tests deliberately precede the implementation.  They execute the actual
URL helper and inspect the actual themed document, without a server or CAD run.
"""
import json
import re
import shutil
import subprocess
from html.parser import HTMLParser

import pytest
from flask import Flask, request

from drawform.service.app import PAGE as STEP_PAGE
from drawform.service.workspace_theme import themed_workspace
from drawform.workbench.page import PAGE as MODEL_PAGE


class Document(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.nodes = []
        self.stack = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        node = {"tag": tag, "attrs": dict(attrs), "parents": tuple(self.stack)}
        self.nodes.append(node)
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index]["tag"] == tag:
                del self.stack[index:]
                break

    def by_id(self, name):
        matches = [node for node in self.nodes if node["attrs"].get("id") == name]
        assert len(matches) == 1, f"Expected one native #{name}, found {len(matches)}"
        return matches[0]

    def grouping(self, name):
        matches = [node for node in self.nodes if node["tag"] == "details"
                   and name in node["attrs"].get("class", "").split()]
        assert len(matches) == 1, f"Expected one native details.{name}"
        assert any(node["tag"] == "summary" and matches[0] in node["parents"]
                   for node in self.nodes), f"details.{name} needs a keyboard-operable summary"
        return matches[0]


def render_view(view):
    try:
        return themed_workspace(MODEL_PAGE, "workbench", view=view)
    except TypeError as error:
        if "unexpected keyword argument 'view'" in str(error):
            pytest.fail("WS-1 missing: themed_workspace has no keyword-only view contract", pytrace=False)
        raise


@pytest.mark.parametrize("query,sid,mode,expected_query,expected_method", [
    ("?view=projects", "version01", "replace", "view=projects&session=version01", "replaceState"),
    ("?view=projects&session=old", "version02", "push", "view=projects&session=version02", "pushState"),
    ("?view=projects&session=old", "", "replace", "view=projects", "replaceState"),
    ("?view=projects&session=old", "", "push", "view=projects", "pushState"),
    ("?view=projects&mode=drawing&token=secret&extra=unknown&session=old", "version03", "replace", "view=projects&session=version03", "replaceState"),
    ("?mode=drawing&token=secret&extra=unknown", "version04", "replace", "session=version04", "replaceState"),
    ("?view=unknown&mode=text&token=secret", "version05", "push", "session=version05", "pushState"),
    ("?view=projects-invalid&token=secret", "", "replace", "", "replaceState"),
    ("?mode=text&token=secret", "", "push", "", "pushState"),
])
def test_session_url_only_preserves_the_project_view(query, sid, mode, expected_query, expected_method):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required to execute the actual PAGE URL helper")
    source = "function setSessionURL" + MODEL_PAGE.split("function setSessionURL", 1)[1].split("function urlSession", 1)[0]
    url = "http://127.0.0.1:8455/workbench/" + query
    script = """const assert=require('node:assert/strict');
const location=new URL(INITIAL_URL),calls=[];
const history={pushState:(state,title,url)=>calls.push({method:'pushState',state,title,url:String(url)}),replaceState:(state,title,url)=>calls.push({method:'replaceState',state,title,url:String(url)})};
""".replace("INITIAL_URL", json.dumps(url))
    expected = "http://127.0.0.1:8455/workbench/" + ("?" + expected_query if expected_query else "")
    script += source + "\nsetSessionURL(" + json.dumps(sid) + "," + json.dumps(mode) + ");\n"
    script += "assert.equal(calls.length,1);assert.equal(calls[0].method," + json.dumps(expected_method) + ");"
    script += "assert.deepEqual(calls[0].state,{});assert.equal(calls[0].title,'');"
    script += "const actualURL=new URL(calls[0].url),expectedURL=new URL(" + json.dumps(expected) + ");"
    script += "assert.equal(actualURL.origin+actualURL.pathname,expectedURL.origin+expectedURL.pathname);assert.equal(actualURL.hash,'');"
    script += "assert.equal([...actualURL.searchParams].length,[...expectedURL.searchParams].length);assert.deepEqual(Object.fromEntries(actualURL.searchParams),Object.fromEntries(expectedURL.searchParams));"
    result = subprocess.run([node, "-e", script], capture_output=True, text=True)
    assert result.returncode == 0, "Actual setSessionURL violated the view/identity contract:\n" + result.stderr


@pytest.mark.parametrize("view,expected", [("text", "modeling"), ("drawing", "modeling"), ("projects", "projects")])
def test_workbench_view_has_a_whitelisted_presentation_marker(view, expected):
    html = render_view(view)
    body = next(node for node in Document(html).nodes if node["tag"] == "body")
    assert body["attrs"].get("data-df-view") == expected


def test_step_tool_has_its_own_presentation_marker():
    body = next(node for node in Document(themed_workspace(STEP_PAGE, "step")).nodes if node["tag"] == "body")
    assert body["attrs"].get("data-df-view") == "step"


def test_project_view_is_retained_with_a_session_restore_link():
    # The host must keep the presentation view even when session restoration is
    # present.  A dedicated project layout does not override the session's input.
    host = Flask(__name__)
    with host.test_request_context("/workbench/?view=projects&session=abcdef123456"):
        html = render_view(request.args.get("view"))
    document = Document(html)
    body = next(node for node in document.nodes if node["tag"] == "body")
    assert body["attrs"].get("data-df-view") == "projects"
    assert 'function urlSession()' in html
    assert 'setInputMode(session.input_kind===' in html
    assert document.by_id("project-list")
    assert document.by_id("model-history")
    assert document.by_id("download-package")


@pytest.mark.parametrize("view,expanded", [("text", False), ("drawing", False), ("projects", True)])
def test_native_model_metadata_disclosure_preserves_both_sections(view, expanded):
    document = Document(render_view(view))
    details = document.grouping("df-model-meta")
    assert ("open" in details["attrs"]) is expanded
    for name in ("version-section", "project-section"):
        assert details in document.by_id(name)["parents"]


@pytest.mark.parametrize("view", ["text", "drawing", "projects"])
def test_source_context_stays_in_an_open_native_disclosure(view):
    document = Document(render_view(view))
    details = document.grouping("df-source-context")
    assert "open" in details["attrs"]
    assert details in document.by_id("source-section")["parents"]


@pytest.mark.parametrize("view", ["text", "drawing", "projects"])
def test_grouping_keeps_native_controls_scripts_and_all_four_entry_links(view):
    html = render_view(view)
    before, after = Document(MODEL_PAGE), Document(html)
    controls = {"input", "button", "select", "textarea", "canvas", "form"}
    assert [(n["tag"], n["attrs"]) for n in after.nodes if n["tag"] in controls] == [
        (n["tag"], n["attrs"]) for n in before.nodes if n["tag"] in controls]
    for original in before.nodes:
        if "id" in original["attrs"]:
            after.by_id(original["attrs"]["id"])
    assert re.findall(r"<script\b[^>]*>.*?</script>", html, re.S) == re.findall(
        r"<script\b[^>]*>.*?</script>", MODEL_PAGE, re.S)
    links = {node["attrs"].get("href") for node in after.nodes if node["tag"] == "a"}
    assert {"/?tool=step", "/workbench/?mode=text", "/workbench/?mode=drawing", "/workbench/?view=projects"} <= links


@pytest.mark.parametrize('query,expected,source_expanded', [
    ('?mode=text', 'modeling', True),
    ('?mode=drawing', 'modeling', True),
    ('?view=projects', 'projects', False),
    ('?view=projects&session=saved&mode=drawing', 'projects', False),
    ('?mode=text&session=saved', 'modeling', False),
    ('?view=%3Cscript%3E&mode=unknown', 'modeling', True),
])
def test_real_host_maps_view_without_overriding_business_session(query, expected, source_expanded):
    from drawform.service.frontend import FRONTEND_ENV, presentation_page
    host = Flask(__name__)
    host.config[FRONTEND_ENV] = '/release/build'
    with host.test_request_context('/workbench/' + query):
        html = presentation_page(host, MODEL_PAGE, 'workbench')
    document = Document(html)
    body = next(n for n in document.nodes if n['tag'] == 'body')
    assert body['attrs']['data-df-view'] == expected
    assert ('open' in document.grouping('df-source-context')['attrs']) is source_expanded
    assert re.findall(r'<script\b[^>]*>.*?</script>', html, re.S) == re.findall(
        r'<script\b[^>]*>.*?</script>', MODEL_PAGE, re.S)
    assert 'data-df-view="<script>"' not in html


def test_host_keeps_legacy_page_byte_identical_when_shell_disabled():
    from drawform.service.frontend import presentation_page
    host = Flask(__name__)
    with host.test_request_context('/workbench/?view=projects&session=saved'):
        assert presentation_page(host, MODEL_PAGE, 'workbench') == MODEL_PAGE


@pytest.mark.parametrize('view', ['text', 'drawing', 'projects'])
def test_connection_failures_remain_visible_outside_collapsed_settings(view):
    document = Document(render_view(view))
    settings = document.grouping('df-connection-settings')
    assert settings not in document.by_id('connection-status')['parents']
    assert settings in document.by_id('connection-form')['parents']
    assert document.by_id('connection-status')['attrs']['role'] == 'status'

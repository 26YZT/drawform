"""Optional tool styling must preserve its actual workflow and UI-only entry links."""
import json
import re
import shutil
import subprocess
from html.parser import HTMLParser

import pytest

from drawform.service.app import PAGE as REVIEW_PAGE
from drawform.service.workspace_theme import themed_workspace
from drawform.workbench.page import PAGE as MODEL_PAGE


class Controls(HTMLParser):
    def __init__(self, page):
        super().__init__()
        self.ids = []
        self.control_attributes = []
        self.feed(page)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.ids.append(attrs["id"])
        if tag in ("input", "button", "select", "textarea", "canvas", "form"):
            self.control_attributes.append((tag, attrs))


@pytest.mark.parametrize("page,kind", [(REVIEW_PAGE, "step"), (MODEL_PAGE, "modeling")])
def test_theme_keeps_all_workflow_controls_and_scripts(page, kind):
    themed = themed_workspace(page, kind)
    original, changed = Controls(page), Controls(themed)
    assert all(changed.ids.count(name) == 1 for name in original.ids)
    assert changed.control_attributes == original.control_attributes
    assert re.findall(r"<script\b[^>]*>.*?</script>", themed, re.S) == re.findall(
        r"<script\b[^>]*>.*?</script>", page, re.S)
    assert themed_workspace(themed, kind) == themed
    assert '<nav class="df-suite-nav"' in themed
    assert 'href="/?tool=step"' in themed
    assert 'href="/workbench/?mode=drawing"' in themed
    assert 'href="/workbench/?view=projects"' in themed
    assert not re.search(r"<(?:script|link|iframe)\b[^>]*(?:src|href)=", themed)


def test_theme_is_opt_in_and_rejects_unknown_context():
    assert 'id="drawform-workspace-theme"' not in REVIEW_PAGE
    assert 'id="drawform-workspace-theme"' not in MODEL_PAGE
    with pytest.raises(ValueError):
        themed_workspace(MODEL_PAGE, "../../private")
    with pytest.raises(ValueError):
        themed_workspace("missing shell", "step")


@pytest.mark.parametrize("query,session,mode,project_focus", [
    ("?mode=drawing", "", "drawing", False),
    ("?mode=text", "", "text", False),
    ("?mode=unknown&view=unknown", "", None, False),
    ("?view=projects", "", None, True),
    ("?session=existing&mode=drawing&view=projects", "existing", None, False),
])
def test_entry_query_only_selects_ui_and_session_takes_precedence(query, session, mode, project_focus):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required to run the actual entry query initializer")
    source = MODEL_PAGE.split("const entryQuery=new URLSearchParams(location.search);", 1)[1]
    source = "const entryQuery=new URLSearchParams(location.search);" + source.split(
        'state.token=storageRead("dfToken")', 1)[0]
    script = """const assert=require('node:assert/strict');
const calls=[];
const location={search:QUERY};
const urlSession=()=>SESSION;
const setInputMode=mode=>calls.push(['mode',mode]);
const requestAnimationFrame=callback=>callback();
const list={disabled:true,closest:()=>({scrollIntoView:()=>calls.push(['scroll'])})};
const title={setAttribute:()=>{},focus:()=>calls.push(['focus'])};
const $=selector=>selector==='#project-list'?list:title;
""".replace("QUERY", json.dumps(query)).replace("SESSION", json.dumps(session))
    expected = ([['mode', mode]] if mode else []) + ([['scroll'], ['focus']] if project_focus else [])
    script += source + "\nassert.deepEqual(calls," + json.dumps(expected) + ");"
    subprocess.run([node, "-e", script], check=True, capture_output=True, text=True)

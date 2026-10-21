"""Opt-in presentation shell; native CAD/workbench controls remain authoritative."""
import re
from .workspace_style import STYLE


def _group(document: str, start: str, end: str, shell: str) -> str:
    """Wrap known static siblings without rewriting their native contents."""
    if document.count(start) != 1 or document.count(end) != 1:
        raise ValueError("Workspace grouping landmarks changed")
    before, rest = document.split(start, 1)
    content, after = rest.split(end, 1)
    return before + shell + start + content + "</div></details>\n" + end + after


def themed_workspace(page: str, kind: str, *, view: str | None = None,
                     source_open: bool = True) -> str:
    """Preserve controls/scripts; views select presentation, never model identity."""
    kinds = {"step": "step", "review": "step", "legacy": "step",
             "modeling": "modeling", "workbench": "modeling"}
    if kind not in kinds:
        raise ValueError("Unknown DrawForm workspace kind")
    if not isinstance(page, str):
        raise TypeError("Workspace page must be HTML text")
    if 'id="drawform-workspace-theme"' in page:
        return page
    if "</head>" not in page or not re.search(r"<body\b[^>]*>", page):
        raise ValueError("Workspace page is missing its document shell")
    theme = kinds[kind]
    context = "projects" if theme == "modeling" and view == "projects" else theme
    links = [("/", "概览", "overview"), ("/?tool=step", "模型转工程图", "step"),
             ("/workbench/?mode=text", "文字建模", "text"),
             ("/workbench/?mode=drawing", "图纸建模", "drawing"),
             ("/workbench/?view=projects", "项目 / 交付", "projects")]
    items = "".join(f'<a href="{url}" data-df-link="{label_kind}"'
                    + (' aria-current="page"' if label_kind == context else "")
                    + f'>{label}</a>' for url, label, label_kind in links)
    nav = ('<nav class="df-suite-nav" aria-label="汽绘主导航">'
           '<a class="df-suite-brand" href="/" aria-label="汽绘概览">'
           '<span class="df-saic-tile"><img src="/assets/branding/saic-group-blue.png" '
           'width="32" height="32" alt="上汽集团"></span>'
           '<span class="df-saic-caption">上汽集团</span>'
           '<span class="df-brand-divider" aria-hidden="true"></span>'
           '<span class="df-brand-product"><span class="df-suite-wordmark">汽绘</span>'
           '<span class="df-brand-caption">工程设计工作台</span></span></a>'
           f'<div class="df-suite-links">{items}</div></nav>')
    descriptions = {
        "step": ("模型转工程图", "导入 STEP，核对工程信息，完成出图与审阅。"),
        "projects": ("项目交付", "找回历史模型，核对有效版本，下载完整交付包。"),
        "text": ("文字建模", "描述简单零件，补齐关键尺寸，生成模型与工程图。"),
        "drawing": ("图纸建模", "上传单视图图纸，核对尺寸出处，继续建模与出图。"),
    }
    hero_parts = "".join(f'<div class="df-intro-copy df-intro-{name}"><h1>{descriptions[name][0]}</h1>'
                         f'<p>{descriptions[name][1]}</p></div>'
                         for name in ([context] if context != "modeling" else ["text", "drawing"]))
    intro = ('<div class="df-page-intro">'
             '<div class="df-intro-label"><span aria-hidden="true">↗</span> 设计工作区</div>'
             f'{hero_parts}<span class="df-local-label">本地工作区 · 历史可追溯</span></div>')
    # Only static copy is translated; state/format IDs and filenames are untouched.
    document, separator, scripts = page.partition("<script>")
    if theme == "step":
        document = document.replace("用文字生成带孔板模型 → STEP → 工程图",
                                    "文字 / 图纸 → 简单零件3D → STEP → 工程图")
    document = document.replace("汽绘 DrawForm", "汽绘").replace("DrawForm", "汽绘")
    document = document.replace("3D", "三维").replace("2D", "二维")
    if theme == "modeling":
        # Keep live success/error feedback outside the collapsed credential form.
        document = _group(document, '<h2 id="connection-title">',
                          '<p id="connection-status"',
                          '<details class="df-connection-settings"><summary>连接设置<span>访问令牌</span></summary><div>')
        document = _group(document, '<section aria-labelledby="projects-title">',
                          '<section aria-labelledby="input-title">',
                          '<details class="df-sidebar-extra"' + (' open' if context == "projects" else '')
                          + '><summary>已保存项目<span>恢复历史任务</span></summary><div>')
        document = _group(document, '<section id="version-section"', '<section id="source-section"',
                          '<details class="df-model-meta"' + (' open' if context == "projects" else '')
                          + '><summary>模型版本与项目交付<span>查看历史 / 管理有效版本 / 下载交付包</span>'
                          '</summary><div class="df-meta-grid">')
        document = _group(document, '<section id="source-section"', '<section id="clarification-section"',
                          '<details class="df-source-context"' + (' open' if source_open else '')
                          + '><summary>输入与尺寸出处<span>原始输入、识别结果与核对依据</span></summary><div>')
        if context == "projects":
            document = _group(document, '<section aria-labelledby="input-title">', '<section id="session-section"',
                              '<details class="df-input-extra"><summary>继续建模或新建模型</summary><div>')
    styled = (document + separator + scripts).replace("</head>", STYLE + "\n</head>", 1)
    return re.sub(r"<body\b([^>]*)>",
                  lambda match: f'<body{match.group(1)} data-df-theme="{theme}" data-df-view="{context}">\n{nav}{intro}',
                  styled, count=1)

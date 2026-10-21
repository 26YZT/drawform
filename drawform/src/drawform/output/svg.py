"""把一张图序列化成 SVG —— 浏览器与 PDF 共用的那份几何在 `output/drawing.py`。

为什么必须自研：`ezdxf.addons.drawing` 用绝对 mm 线宽画线时，在整图缩放下
0.50 与 0.25 看不出差别；而它根本不渲染 TOLERANCE 实体。选网页端之后，
"屏幕上看到的和打印出来的是不是同一张图"就成了交付问题，不能再借预览插件。

坐标系：SVG 的 y 向下、图纸 y 向上，统一在 y = 纸高 - y 处落地。
"""

from __future__ import annotations

import pathlib

from drawform.output.drawing import STYLES, TEXT_HEIGHT, build_drawing
from drawform.output.sheet import SHEETS

_FONT = "osifm, isocp, sans-serif"


def escape(text: str) -> str:
    """图上的文字来自模型与客户模板，尖括号与 & 必须转义，否则是注入面。"""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _run(points, sheet_height: float) -> str:
    return " ".join(f"{x:.3f},{sheet_height - y:.3f}" for x, y in points)


def markup(drawing) -> str:
    """一张图 → SVG 文本。PDF 序列化器读同一份 `drawing`。"""
    width, height = SHEETS[drawing.sheet]
    lines = dict(drawing.lines)

    body = []
    for layer, (weight, dash) in STYLES.items():
        runs = lines.get(layer)
        if not runs:
            continue
        dashes = ""
        if dash:
            pattern = " ".join(f"{abs(v):g}" for v in dash)
            dashes = f' stroke-dasharray="{pattern}"'
        shapes = "".join(f'<polyline points="{_run(p, height)}"/>' for p in runs)
        body.append(f'<g id="{layer}" stroke="black" stroke-width="{weight}" '
                    f'fill="none"{dashes}>{shapes}</g>')

    if drawing.arrows:
        body.append('<g id="ARROW" stroke="none" fill="black">'
                    + "".join(f'<polygon points="{_run(a, height)}"/>'
                              for a in drawing.arrows) + "</g>")

    for text in drawing.texts:
        paper_y = height - text.y
        turn = (f' transform="rotate({text.rotate:g} {text.x:.3f} {paper_y:.3f})"'
                if text.rotate else "")
        body.append(f'<text x="{text.x:.3f}" y="{paper_y:.3f}" font-family="{_FONT}" '
                    f'font-size="{text.height:g}" text-anchor="{text.anchor}"{turn} '
                    f'stroke="none">{escape(text.content)}</text>')

    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:g}mm" '
            f'height="{height:g}mm" viewBox="0 0 {width:g} {height:g}">'
            + "".join(body) + "</svg>")


def write_svg(placed, path, sheet: str = "A3", dimensions=(), notes=(),
              section_marks=(), datum_marks=(), title_meta=None, text_overrides=None,
              layout_dimensions=None, layout_reference=None, changed_owners=(), drawing=None) -> pathlib.Path:
    """输出 SVG。视图在这里才搬进图框，见 `drawing.sheet_views`。"""
    if drawing is None:
        drawing = build_drawing(placed, sheet, dimensions, notes, section_marks,
                                datum_marks, title_meta, text_overrides=text_overrides,
                                layout_dimensions=layout_dimensions, layout_reference=layout_reference,
                                changed_owners=changed_owners)
    svg = markup(drawing)
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(svg, encoding="utf-8")
    return path

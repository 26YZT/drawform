"""PDF 出口。与 SVG 共用 `output.drawing` 那份几何，只换序列化器。

刻意不做「先出 SVG 再转 PDF」：转换器要重新理解 mm 线宽、GB/T 线型、字头方向，
每理解错一处就多出一张对不上的图。共用同一份坐标、各自只写自己那套语法，
才是"网页上的图与纸上的图是同一张"的可证实现法。

字体：matplotlib 对画不出来的字形是**静默留白**，而 PDF 照样生成、大小看着正常。
所以这里先在本机字体里找一个能画下全图文字的，找不到就抛 `MissingGlyph`。
Linux 服务器上常常只有 DejaVu，中文技术要求就是这种情况 —— 宁可不出图。
"""

from __future__ import annotations

import functools
import pathlib

from drawform.output.drawing import STYLES, TEXT_HEIGHT, build_drawing
from drawform.output.sheet import SHEETS

MM_TO_PT = 72.0 / 25.4
_ALIGN = {"start": "left", "middle": "center"}


class MissingGlyph(Exception):
    """本机没有任何字体能画下这张图上的文字。"""


#: 兜底字体：给每个码位都塞一个字形（不认识的画成方框加码点）。让它入选，
#: 等于把"字体不全"伪装成"字体齐全"，整张图还会改用那套诊断字形。
_FALLBACK_HINTS = ("last resort",)


@functools.lru_cache(maxsize=32)
def _font_path(chars: frozenset[str]) -> str:
    from matplotlib import font_manager
    from matplotlib.ft2font import FT2Font

    wanted = frozenset(c for c in chars if c.strip())
    default = font_manager.findfont(font_manager.FontProperties())
    if _covers(default, wanted):
        return default
    for entry in font_manager.fontManager.ttflist:
        if any(hint in entry.name.lower() for hint in _FALLBACK_HINTS):
            continue
        if _covers(entry.fname, wanted):
            return entry.fname
    raise MissingGlyph(
        f"本机字体画不出这些字符：{''.join(sorted(wanted))}。"
        "服务器上需要装一套覆盖工程符号与中文的字体（GB/T 图样通常用 isocp/osifm 加中文字体）。")


@functools.lru_cache(maxsize=64)
def _face(path: str):
    from matplotlib.ft2font import FT2Font

    return FT2Font(str(path))


def _covers(path: str, wanted: frozenset[str]) -> bool:
    try:
        face = _face(path)
    except Exception:
        return False
    return all(face.get_char_index(ord(ch)) for ch in wanted)


def _linestyle(weight_dash):
    _weight, pattern = weight_dash
    if not pattern:
        return "solid"
    return (0.0, [abs(v) * MM_TO_PT for v in pattern])


def write_pdf(placed, path, sheet: str = "A3", dimensions=(), notes=(),
              section_marks=(), datum_marks=(), title_meta=None, text_overrides=None,
              layout_dimensions=None, layout_reference=None, changed_owners=(), drawing=None) -> pathlib.Path:
    """把一张图写成矢量 PDF。坐标、线宽、字头方向全部来自 `build_drawing`。"""
    from matplotlib import rc_context
    from matplotlib.font_manager import FontProperties
    from matplotlib.figure import Figure
    from matplotlib.patches import Polygon

    if drawing is None:
        drawing = build_drawing(placed, sheet, dimensions, notes, section_marks,
                                datum_marks, title_meta, text_overrides=text_overrides,
                                layout_dimensions=layout_dimensions, layout_reference=layout_reference,
                                changed_owners=changed_owners)
    width, height = SHEETS[drawing.sheet]
    font = FontProperties(fname=_font_path(
        frozenset(ch for text in drawing.texts for ch in text.content)))

    figure = Figure(figsize=(width / 25.4, height / 25.4))
    # pdf.fonttype=42：默认值 3 会把字打成位图，放大就糊、也不能选中复制。
    with rc_context({"pdf.fonttype": 42, "svg.fonttype": "none"}):
        axes = figure.add_axes([0.0, 0.0, 1.0, 1.0])
        axes.set_xlim(0.0, width * MM_TO_PT)
        axes.set_ylim(0.0, height * MM_TO_PT)
        axes.set_axis_off()
        axes.set_facecolor("white")

        by_layer = dict(drawing.lines)
        for layer, (weight, _pattern) in STYLES.items():
            for run in by_layer.get(layer, ()):
                axes.plot([x * MM_TO_PT for x, _ in run],
                          [y * MM_TO_PT for _, y in run],
                          color="black", linewidth=weight * MM_TO_PT,
                          linestyle=_linestyle((weight, _pattern)),
                          solid_capstyle="butt")

        for arrow in drawing.arrows:
            axes.add_patch(Polygon([(x * MM_TO_PT, y * MM_TO_PT) for x, y in arrow],
                                   closed=True, facecolor="black",
                                   edgecolor="none", linewidth=0))

        for text in drawing.texts:
            axes.text(text.x * MM_TO_PT, text.y * MM_TO_PT, text.content,
                      fontproperties=font, fontsize=text.height * MM_TO_PT,
                      ha=_ALIGN[text.anchor], va="baseline",
                      rotation=-text.rotate,
                      rotation_mode='anchor' if abs(text.rotate % 90) > 1e-9 else None,
                      color="black")

        target = pathlib.Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        figure.savefig(str(target), format="pdf")
    return target

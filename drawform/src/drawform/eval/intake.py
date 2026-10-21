"""取数体检：判定一张外部 DXF 能用于哪几类对照。

这块代码只回答"能不能用"，不回答"画得好不好"。判错的方向很贵：把尺寸已经被炸开的图纸
当成可用样本，后面所有"基准怎么选、尺寸标哪些"的统计就都是在拿版式冒充数据。

`flags` 与 `uses` 是稳定 id，测试和下游只按 id 断言；给人看的中文由调用方渲染。
"""

from __future__ import annotations

import pathlib
import re
from dataclasses import dataclass, field

import ezdxf

#: 可用于：标题栏版式、字段词汇、技术要求条目 —— 最低门槛，只要图不是空的。
USE_LAYOUT = "layout"
#: 可用于：尺寸选择与基准选择的对照。必须有原生 DIMENSION 实体。
USE_PAIRING = "pairing"
#: 可用于：图面互斥尺子（text/line、leader/line）。要有尺寸和矢量线。
USE_RULER = "ruler"
#: 可用于：公差写法口径。至少一处 ± 或上下偏差文字。
USE_TOLERANCE = "tolerance"

_VECTOR_TYPES = frozenset({"LINE", "LWPOLYLINE", "POLYLINE", "ARC",
                           "CIRCLE", "SPLINE", "ELLIPSE"})
_TEXT_TYPES = frozenset({"TEXT", "MTEXT", "ATTRIB"})

#: 筛查用的口径，不是解析：`±`、上下偏差、`⌀20H7` 这类配合代号。
_TOLERANCE_RE = re.compile(
    r"±|[+\-]\s*\d*\.\d+|[⌀ØΦ]\s*\d+(?:\.\d+)?\s*[A-Za-z]{1,2}\d{1,2}")

#: 乱码：替换字符、GBK/UTF-8 互串的标志性字串、连续的重音符号。
_GARBLED_RE = re.compile(r"[\ufffd\x00]|锟斤|Ã[\x80-\xbf][\x80-\xbf]")


@dataclass(frozen=True)
class IntakeReport:
    path: str
    ok: bool
    reason: str = ""
    version: str = ""
    dims: int = 0
    texts: int = 0
    attribs: int = 0
    vector: int = 0
    proxy: int = 0
    garbled: int = 0
    tolerances: int = 0
    flags: tuple[str, ...] = ()
    uses: tuple[str, ...] = ()


@dataclass
class _Tally:
    dims: int = 0
    texts: int = 0
    attribs: int = 0
    vector: int = 0
    proxy: int = 0
    strings: list[str] = field(default_factory=list)


def _strings_of(entity) -> list[str]:
    try:
        text = entity.dxf.text
    except AttributeError:
        return []
    return [] if text is None else [text]


def _tally_layout(entities, tally: _Tally) -> None:
    for entity in entities:
        kind = entity.dxftype()
        if kind == "ATTRIB":
            tally.attribs += 1
            tally.strings += _strings_of(entity)
        elif kind == "INSERT":
            # ATTRIB 挂在 INSERT 上而不是图层里，直接遍历布局永远数不到标题栏字段。
            for attrib in entity.attribs:
                tally.attribs += 1
                tally.strings += _strings_of(attrib)
        elif kind in ("TEXT", "MTEXT"):
            tally.texts += 1
            tally.strings += _strings_of(entity)
        elif kind in _VECTOR_TYPES:
            tally.vector += 1
        elif kind == "ACAD_PROXY_ENTITY":
            tally.proxy += 1


def dimension_entities(doc):
    """一张图里所有 DIMENSION，含块定义里的。

    只遍历布局会漏掉合法画法（模板图、装配里的子图、批量出图把尺寸放进块里），
    漏掉的后果是把一张能用的图判成"没有原生尺寸" —— 判错方向最坏的那种。
    取数体检与 DWG 往返判定都用这一份遍历，两边各写一份迟早会量出不同的"尺寸数"。
    """
    spaces = list(doc.layouts) + [b for b in doc.blocks
                                  if not b.block_record.is_any_layout]
    for space in spaces:
        for entity in space:
            if entity.dxftype() == "DIMENSION":
                yield entity


def _count_dimensions(doc, tally: _Tally) -> None:
    """数尺寸，并把尺寸块里**真正显示**的文字收进来（公差与乱码都从那些字里判）。"""
    for entity in dimension_entities(doc):
        tally.dims += 1
        geometry = entity.get_geometry_block()
        if geometry is not None:
            tally.strings += [s for member in geometry for s in _strings_of(member)]


def inspect_dxf(path) -> IntakeReport:
    """读一张 DXF，给出它能用于哪几类对照。读不了就返回 `ok=False` 加一个稳定 reason id。"""
    path = pathlib.Path(path)
    try:
        doc = ezdxf.readfile(str(path))
    except Exception:
        return IntakeReport(path=str(path), ok=False, reason="not_dxf")

    tally = _Tally()
    for layout in doc.layouts:
        _tally_layout(layout, tally)
    _count_dimensions(doc, tally)

    garbled = sum(1 for s in tally.strings if _GARBLED_RE.search(s))
    tolerances = sum(1 for s in tally.strings if _TOLERANCE_RE.search(s))

    flags: list[str] = []
    if tally.dims == 0:
        # 外部数据教的一课：空白模板也满足"线条多、文字多、尺寸 0"，不能替对方猜是炸开还是没标。
        flags.append("no_native_dimensions")
    if tally.vector == 0:
        flags.append("no_vector_lines")
    if garbled:
        flags.append("garbled_text")
    if tally.attribs == 0:
        flags.append("no_block_attributes")
    if tally.proxy:
        flags.append("proxy_entities")

    uses: list[str] = []
    if tally.dims or tally.texts or tally.attribs or tally.vector:
        uses.append(USE_LAYOUT)
    if tally.dims >= 1:
        uses.append(USE_PAIRING)
        if tally.vector >= 1:
            uses.append(USE_RULER)
    if tolerances >= 1:
        uses.append(USE_TOLERANCE)

    return IntakeReport(
        path=str(path), ok=True, version=str(doc.acad_release),
        dims=tally.dims, texts=tally.texts, attribs=tally.attribs,
        vector=tally.vector, proxy=tally.proxy,
        garbled=garbled, tolerances=tolerances,
        flags=tuple(flags), uses=tuple(uses))

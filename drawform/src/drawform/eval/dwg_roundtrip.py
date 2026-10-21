"""DWG 往返判定：源 DXF 与"存成 DWG 再导回 DXF"的结果相比，尺寸有没有丢。

这台机器上没有 CAD，转换那一步只能在集团某台装了 AutoCAD 的机器上跑
（见 `tools/dwg_verify/`）。本模块负责跑完之后**谁来判、判什么**。

判什么：只判"有没有丢"。
- 尺寸实体条数变少 → `dimensions_lost`（转换器把 DIMENSION 炸成线条与文字，
  这是 DWG 生态里最常见的失败模式，也正是我们交付的核心）；
- 图上显示的数字对不上 → `labels_lost`，并点名丢了哪几个
  （只比条数会放过"87.342 变成 8"这种条数吻合、图纸全错的情况）；
- 转换器多吐几条 → 记进 `added` 但不算失败：超集不是事故，点名让人自己看。
"""

from __future__ import annotations

import collections
import pathlib
from dataclasses import dataclass

import ezdxf
from ezdxf.tools.text import plain_mtext

from drawform.eval.intake import dimension_entities

#: 目标读不出来（多半是给了 .dwg 而不是导回的 .dxf，或者勾了二进制 DXF）。
SOURCE_UNREADABLE = "source_unreadable"
TARGET_UNREADABLE = "target_unreadable"
#: DIMENSION 实体变少 —— 尺寸被炸开了。
DIMENSIONS_LOST = "dimensions_lost"
#: 图上显示的数字对不上。
LABELS_LOST = "labels_lost"


@dataclass(frozen=True)
class Roundtrip:
    source: str
    target: str
    ok: bool
    failed: tuple[str, ...] = ()
    src_dims: int = 0
    dst_dims: int = 0
    lost: tuple[str, ...] = ()
    added: tuple[str, ...] = ()


def shown_label(entity) -> str:
    block = entity.get_geometry_block()
    if block is None:
        return ""
    parts = []
    for member in block:
        if member.dxftype() not in ("TEXT", "MTEXT", "ATTRIB"):
            continue
        raw = member.dxf.get("text") or ""
        parts.append(plain_mtext(raw) if member.dxftype() == "MTEXT" else raw)
    return " ".join(p.strip() for p in parts if p.strip())


def dimension_labels(doc) -> list[str]:
    """每条尺寸在图上真正显示出来的那串字。"""
    return [shown_label(entity) for entity in dimension_entities(doc)]


def _read(path):
    try:
        return ezdxf.readfile(str(pathlib.Path(path)))
    except Exception:
        return None


def compare_pair(source, target) -> Roundtrip:
    src_doc, dst_doc = _read(source), _read(target)
    if src_doc is None or dst_doc is None:
        which = SOURCE_UNREADABLE if src_doc is None else TARGET_UNREADABLE
        return Roundtrip(str(source), str(target), ok=False, failed=(which,))

    src_labels = collections.Counter(dimension_labels(src_doc))
    dst_labels = collections.Counter(dimension_labels(dst_doc))
    lost = sorted((src_labels - dst_labels).elements())
    added = sorted((dst_labels - src_labels).elements())
    # 尺寸条数要按**总条数**算，不是按"有几种数字"：一个零件上标四次 40 是常事，
    # 折叠成一种就会把"丢了其中一条 40"判成无损。
    src_dims = sum(src_labels.values())
    dst_dims = sum(dst_labels.values())

    failed: list[str] = []
    if dst_dims < src_dims:
        failed.append(DIMENSIONS_LOST)
    if lost:
        failed.append(LABELS_LOST)

    return Roundtrip(str(source), str(target), ok=not failed, failed=tuple(failed),
                     src_dims=src_dims, dst_dims=dst_dims,
                     lost=tuple(lost), added=tuple(added))

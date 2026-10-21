"""把外部 DXF 读成 `Drawing`，好让生产用的互斥检测器能量真图。

为什么要这一层：`text/line`（我们 668 处）与 `leader/line`（185 处）两条规则从来没有
对照组，所以一直只当线索、不敢拿来改产物。MISUMI 那 1988 张公开图有矢量线也有文字，
够量一次"真图上这条规则会不会也报几百处"。

纪律：探针必须与生产用同一份实现 —— 这里只负责把 DXF 变成 `Drawing`，
判定仍走 `output.collide.find_collisions`。读不了的实体**计数并报，绝不静默丢**，
否则"真图 0 处冲突"可能只是"我们什么都没读进来"。

已知不展开：`INSERT` 与 `DIMENSION`（块内容要按插入点/比例/旋转变换，尺寸还要取几何块）。
所以这一层能校准 `text/line`，**还不能**校准 `leader/line` —— 那要等带尺寸线的真图。
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass, field

import ezdxf
from ezdxf.tools.text import plain_mtext

from drawform.output.drawing import Drawing, DrawingItem, Text

#: 圆弧/样条离散化的弦高差（mm）。0.2 mm 远小于最小字高，不会把相交判成不相交。
_SAG = 0.2

_CURVE_KINDS = frozenset({"LINE", "LWPOLYLINE", "POLYLINE", "ARC", "CIRCLE",
                          "SPLINE", "ELLIPSE"})
_TEXT_KINDS = frozenset({"TEXT", "MTEXT", "ATTRIB"})


@dataclass(frozen=True)
class Ingested:
    drawing: Drawing
    skipped: dict = field(default_factory=dict)


def _run_of(entity):
    """实体走的是哪些点。展不开就返回 None，由调用方计入 `skipped`。

    `LWPolyline` 没有 `flattening()`，带凸度的折线要先把路径展出来 —— 所以统一走
    `ezdxf.path.make_path`，只有直线例外（它没有弧要离散）。
    """
    try:
        if entity.dxftype() == "LINE":
            start, end = entity.dxf.start, entity.dxf.end
            return [(start.x, start.y), (end.x, end.y)]
        points = [(p.x, p.y) for p in ezdxf.path.make_path(entity).flattening(_SAG)]
    except (AttributeError, IndexError, TypeError, ValueError, ZeroDivisionError):
        return None
    if len(points) < 2:
        return None
    if getattr(entity.dxf, "closed", 0) and points[0] != points[-1]:
        points.append(points[0])
    return points


def _text_of(entity):
    kind = entity.dxftype()
    try:
        raw = entity.dxf.text
        height = entity.dxf.char_height if kind == "MTEXT" else entity.dxf.height
    except AttributeError:
        return None
    if not raw:
        return None
    content = plain_mtext(raw) if kind == "MTEXT" else raw
    point = entity.dxf.insert
    return content, point.x, point.y, float(entity.dxf.get("rotation", 0.0)), height


def read_sheet(path, sheet: str = "A3") -> Ingested:
    """读一张外部 DXF，变成能被 `find_collisions` 判定的 `Drawing`。

    每个实体的 owner 取自己的 handle —— 外部图没有"这条尺寸画的字"这种归属信息，
    于是同归属豁免永不生效，报出来的数只会**偏多不会偏少**。对照组偏严是安全的：
    真图上都过不了关的规则，用在我们自己图上才算严。
    """
    doc = ezdxf.readfile(str(pathlib.Path(path)))
    items: list[DrawingItem] = []
    texts: list[Text] = []
    skipped: dict[str, int] = {}

    for layout in doc.layouts:
        for entity in layout:
            kind = entity.dxftype()
            handle = str(entity.dxf.get("handle", kind))
            if kind in _TEXT_KINDS:
                parsed = _text_of(entity)
                if parsed is None:
                    skipped[kind] = skipped.get(kind, 0) + 1
                    continue
                content, x, y, rotation, height = parsed
                texts.append(Text(content, x, y, "start", rotation,
                                  owner=handle, height=height))
                continue
            run = _run_of(entity) if kind in _CURVE_KINDS else None
            if run is None:
                skipped[kind] = skipped.get(kind, 0) + 1
                continue
            items.append(DrawingItem(owner=handle, layer=entity.dxf.layer,
                                     points=tuple(run)))

    by_layer: dict[str, list] = {}
    for item in items:
        by_layer.setdefault(item.layer, []).append(item.points)
    lines = tuple((layer, tuple(runs)) for layer, runs in by_layer.items() if runs)
    return Ingested(drawing=Drawing(sheet=sheet, lines=lines, arrows=(),
                                    texts=tuple(texts), items=tuple(items)),
                    skipped=skipped)

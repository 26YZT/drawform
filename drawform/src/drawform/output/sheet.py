"""图幅与图框。采用 GB/T 14689-2008 的留装订边图框。

来源核对（2026-10-15）：GB 原文表4及浙江大学2026年制图竞赛应用文件
一致给出 a=25，A3/A4 的 c=5，A2 的 c=10；A 系列外尺寸也已独立核对。
原文、应用文件及 ISO 5457 差异见 out/p0-sheet-source-20261015/source-evidence.json。
ISO 的装订边不同，不作同时符合两套图框规则的声明。仅启用已试验的 A2/A3/A4；
标题栏由 output.titleblock 独立处理。
"""

from __future__ import annotations

from dataclasses import dataclass

#: 幅面代号 → 图纸外尺寸 (宽, 长)，横向使用。两份来源一致。
TITLE_CLEAR = 5.0        # 内容让开标题栏时留的间隙

SHEETS = {"A4": (297.0, 210.0), "A3": (420.0, 297.0), "A2": (594.0, 420.0)}

MARGIN_C = 5.0    # A3/A4 周边空白，GB/T 14689-2008 表4
MARGIN_A = 25.0   # 装订边（左侧）


@dataclass(frozen=True)
class Frame:
    sheet: str
    outer: tuple[float, float]
    inner: tuple[float, float, float, float]   # x0, y0, x1, y1

    def contains(self, box: tuple[float, float, float, float]) -> bool:
        x0, y0, x1, y1 = box
        return (x0 >= self.inner[0] - 1e-6 and y0 >= self.inner[1] - 1e-6
                and x1 <= self.inner[2] + 1e-6 and y1 <= self.inner[3] + 1e-6)

    def overflow(self, box: tuple[float, float, float, float]) -> tuple[float, float]:
        """返回 (横向, 纵向) 各超出多少 mm，没超出就是 0。"""
        x0, y0, x1, y1 = box
        return (max(0.0, self.inner[0] - x0) + max(0.0, x1 - self.inner[2]),
                max(0.0, self.inner[1] - y0) + max(0.0, y1 - self.inner[3]))


def make_frame(sheet: str = "A3") -> Frame:
    if sheet not in SHEETS:
        raise ValueError(
            f"图幅 {sheet} 未支持：本模块只收录来源核实过的 A2/A3/A4 横向幅面")
    width, height = SHEETS[sheet]
    margin = 10.0 if sheet == "A2" else MARGIN_C
    inner = (MARGIN_A, margin, width - margin, height - margin)
    return Frame(sheet=sheet, outer=(width, height), inner=inner)


def content_box(views, dimensions=(), marks=()):
    """已排版内容在图纸上占的包围盒（世界坐标）。

    尺寸线、尺寸界线与文字全都悬在轮廓之外 —— 只按轮廓算，一张放得下零件的图
    会把尺寸甩到图框外面（实测 part_0042 的定位尺寸伸出 40 mm）。
    """
    points = [
        (curve_point[0] + view.origin[0], curve_point[1] + view.origin[1])
        for view in views
        for curve in (tuple(view.projection.visible) + tuple(view.projection.hidden)
                      + tuple(view.centerlines) + tuple(view.hatch))
        for curve_point in curve.sample()
    ]
    origins = {(view.label or view.projection.direction.name): view.origin
               for view in views}
    for dim in dimensions:
        ox, oy = origins[dim.view]
        points += [(ox + dim.p1[0], oy + dim.p1[1]), (ox + dim.p2[0], oy + dim.p2[1])]
        if dim.base is not None:
            points.append((ox + dim.base[0], oy + dim.base[1]))
    for mark in marks:
        ox, oy = origins[mark.view]
        from drawform.output.drawing import datum_points
        anchor, elbow, centre, corners = datum_points(mark.anchor, mark.outward,
                                                                    mark.standoff)
        points += [(ox + px, oy + py) for px, py in (anchor, elbow, centre, *corners)]
    if not points:
        return (0.0, 0.0, 0.0, 0.0)
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def center_on_frame(views, frame, dimensions=(), marks=(), title_block=None):
    """把整组视图平移进图框可用区并居中。

    排版层从 (0,0) 起排，而图框左边是 25 mm 装订边 —— 不平移过去，
    第一个视图会直接压在装订边里，装订后就看不见左边那条尺寸了。

    `title_block` 是标题栏在设计区里的矩形。它锚在图框右下角、不跟着内容跑，
    所以内容居中之后可能正好压在它上面（实测 65 件里有 4 件）。这里把内容整体
    上移到刚好让开 —— 不是预留一整条带，那会把本来放得下的件白判成放不下。
    """
    from dataclasses import replace

    x0, y0, x1, y1 = content_box(views, dimensions, marks)
    fx0, fy0, fx1, fy1 = frame.inner
    dx = fx0 + ((fx1 - fx0) - (x1 - x0)) / 2.0 - x0
    dy = fy0 + ((fy1 - fy0) - (y1 - y0)) / 2.0 - y0

    if title_block is not None:
        bx0, by0, bx1, by1 = title_block
        left, right = x0 + dx, x1 + dx
        bottom, top = y0 + dy, y1 + dy
        # 标准矩形相交判定：任一轴完全错开就不算压到
        if not (right <= bx0 or left >= bx1 or top <= by0 or bottom >= by1):
            dy += (by1 - bottom) + TITLE_CLEAR

    return tuple(replace(view, origin=(view.origin[0] + dx, view.origin[1] + dy))
                 for view in views)

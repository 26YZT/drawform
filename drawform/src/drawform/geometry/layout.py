from __future__ import annotations

from dataclasses import dataclass

from drawform.domain.curves import Centerline, Circle2D, Line2D
from drawform.geometry.projection import Projection

DEFAULT_GAP = 18.0
MODES = ("first_angle", "third_angle")


@dataclass(frozen=True)
class PlacedView:
    projection: Projection
    origin: tuple[float, float]
    #: 细点划线：十字中心线、轴线，以及螺栓圆孔系的分布圆（圆而不是线段）。
    centerlines: tuple[Centerline | Circle2D, ...] = ()
    hatch: tuple[Line2D, ...] = ()
    #: 视图在图纸上的身份（"front"、"A-A"）。投射方向相同的两张视图必须能区分，
    #: 空字符串表示就用方向名。
    label: str = ""


def _box(projection: Projection) -> tuple[float, float, float, float]:
    """视图自身边界的局部坐标，用于将投影结果归一化后摆到图纸上。"""
    curves = tuple(projection.visible) + tuple(projection.hidden)
    points = [point for curve in curves for point in curve.sample()]
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return min(xs), min(ys), max(xs), max(ys)


def layout_views(projections, mode: str = "first_angle",
                 gap: float = DEFAULT_GAP) -> tuple[PlacedView, ...]:
    """按投影关系排版三视图，origin 为「图纸原点 − 视图局部原点」的偏移。

    第一角（GB/T 14666）俯视图落在主视图正下方、左视图落在右方；第三角（ASME）两者镜像。
    对齐依据是投影线而不是视觉居中：俯视图与主视图同 X 起止，左视图与主视图同 Y 起止。
    """
    if mode not in MODES:
        raise ValueError(f"未知的投影角法：{mode}，可选 {MODES}")

    boxes = {p.direction.name: _box(p) for p in projections}
    front = boxes["front"]
    front_width = front[2] - front[0]
    front_height = front[3] - front[1]
    top, left = boxes["top"], boxes["left"]

    targets: dict[str, tuple[float, float]] = {"front": (0.0, 0.0)}
    if mode == "first_angle":
        targets["top"] = (0.0, -(top[3] - top[1]) - gap)
        targets["left"] = (front_width + gap, 0.0)
    else:
        targets["top"] = (0.0, front_height + gap)
        targets["left"] = (-(left[2] - left[0]) - gap, 0.0)

    placed = []
    for projection in projections:
        name = projection.direction.name
        box = boxes[name]
        target_x, target_y = targets[name]
        placed.append(PlacedView(projection=projection,
                                 origin=(target_x - box[0], target_y - box[1])))
    return tuple(placed)

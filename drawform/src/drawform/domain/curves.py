from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

Point2D = tuple[float, float]


@dataclass(frozen=True)
class ViewDirection:
    name: str
    direction: tuple[float, float, float]
    horizontal: tuple[float, float, float]


FRONT = ViewDirection("front", (0.0, -1.0, 0.0), (1.0, 0.0, 0.0))
TOP = ViewDirection("top", (0.0, 0.0, 1.0), (1.0, 0.0, 0.0))
LEFT = ViewDirection("left", (1.0, 0.0, 0.0), (0.0, 1.0, 0.0))


def view_basis(direction: ViewDirection):
    """视图平面的水平与竖直单位向量。OCCT 的投影平面满足 y = z × x。"""
    z = direction.direction
    x = direction.horizontal
    y = (
        z[1] * x[2] - z[2] * x[1],
        z[2] * x[0] - z[0] * x[2],
        z[0] * x[1] - z[1] * x[0],
    )
    return x, y


def to_view(point, x_basis, y_basis) -> Point2D:
    """三维点落到视图平面上的二维坐标，与 OCCT 投影结果同一坐标系。"""
    return (sum(a * b for a, b in zip(point, x_basis)),
            sum(a * b for a, b in zip(point, y_basis)))


def _points_at_angles(center: Point2D, radius: float, angles):
    cx, cy = center
    return tuple((cx+radius*math.cos(angle), cy+radius*math.sin(angle)) for angle in angles)


@dataclass(frozen=True)
class Line2D:
    start: Point2D
    end: Point2D

    def sample(self) -> Sequence[Point2D]:
        return (self.start, self.end)


@dataclass(frozen=True)
class Circle2D:
    center: Point2D
    radius: float

    def sample(self) -> Sequence[Point2D]:
        return _points_at_angles(self.center, self.radius, (2*math.pi*i/72 for i in range(73)))


@dataclass(frozen=True)
class Arc2D:
    center: Point2D
    radius: float
    start_angle: float
    end_angle: float

    def sample(self) -> Sequence[Point2D]:
        start, end = self.start_angle, self.end_angle
        angles = [start+(end-start)*i/72 for i in range(73)]
        low, high = sorted((start, end))
        angles.extend(k*math.pi/2 for k in range(math.ceil(low/(math.pi/2)), math.floor(high/(math.pi/2))+1))
        return _points_at_angles(self.center, self.radius, sorted(set(angles), reverse=end < start))


@dataclass(frozen=True)
class Polyline2D:
    """拟合不出解析形状的折线。可能是真自由曲线，也可能是还没还原的直线/圆弧。"""

    points: tuple[Point2D, ...]

    def sample(self) -> Sequence[Point2D]:
        return self.points


ANALYTIC = (Line2D, Circle2D, Arc2D)


@dataclass(frozen=True)
class Centerline:
    """细点划线中心线。GB/T 4457.4 要求超出轮廓线 2–5 mm。"""

    start: Point2D
    end: Point2D

    def sample(self) -> Sequence[Point2D]:
        return (self.start, self.end)

import math

import pytest

from drawform.geometry.projection import Arc2D, Circle2D, Line2D, Polyline2D
from drawform.geometry.refit import refit


def _line_points(x0, y0, x1, y1, n=20):
    return tuple((x0 + (x1 - x0) * i / n, y0 + (y1 - y0) * i / n) for i in range(n + 1))


def _circle_points(cx, cy, r, n=48):
    return tuple(
        (cx + r * math.cos(2 * math.pi * i / n), cy + r * math.sin(2 * math.pi * i / n))
        for i in range(n + 1)
    )


def _arc_points(cx, cy, r, a1, a2, n=24):
    return tuple(
        (cx + r * math.cos(a1 + (a2 - a1) * i / n), cy + r * math.sin(a1 + (a2 - a1) * i / n))
        for i in range(n + 1)
    )


def test_collinear_polyline_refits_to_line():
    fitted = refit(Polyline2D(points=_line_points(10.0, 20.0, 90.0, 20.0)))

    assert isinstance(fitted, Line2D)
    assert fitted.start == pytest.approx((10.0, 20.0), abs=1e-9)
    assert fitted.end == pytest.approx((90.0, 20.0), abs=1e-9)


def test_closed_equidistant_polyline_refits_to_circle():
    fitted = refit(Polyline2D(points=_circle_points(40.0, 25.0, 10.0)))

    assert isinstance(fitted, Circle2D)
    assert fitted.center == pytest.approx((40.0, 25.0), abs=1e-6)
    assert fitted.radius == pytest.approx(10.0, abs=1e-6)


def test_open_equidistant_polyline_refits_to_arc():
    fitted = refit(Polyline2D(points=_arc_points(40.0, 25.0, 10.0, 0.0, math.pi)))

    assert isinstance(fitted, Arc2D)
    assert fitted.center == pytest.approx((40.0, 25.0), abs=1e-6)
    assert fitted.radius == pytest.approx(10.0, abs=1e-6)


def test_genuinely_curved_polyline_is_left_alone():
    """正弦扰动叠加二次项：既非直线也非圆弧，必须原样保留，不能硬拟合。"""
    pts = tuple(
        (i / 20.0, math.sin(i / 20.0 * 4 * math.pi) * 0.5 + (i / 20.0) ** 2)
        for i in range(21)
    )

    fitted = refit(Polyline2D(points=pts))

    assert isinstance(fitted, Polyline2D)
    assert fitted.points == pts


def test_analytic_curves_pass_through_unchanged():
    line = Line2D(start=(0.0, 0.0), end=(5.0, 5.0))
    circle = Circle2D(center=(1.0, 1.0), radius=2.0)

    assert refit(line) is line
    assert refit(circle) is circle


def test_degenerate_two_point_polyline_refits_to_line():
    """HLR 常给出只有端点的退化边，不能因点数不足而放弃拟合。"""
    fitted = refit(Polyline2D(points=((3.0, 4.0), (9.0, 4.0))))

    assert isinstance(fitted, Line2D)


def test_circle_seen_edge_on_refits_to_a_line():
    """孔沿与视线垂直时，圆在视图里退化成一条往返直线：首尾是同一个点。

    以首尾连线判直线性会得到零弦长，这类边永远还原不出来，客户拿到的就是一坨折线；
    实测阶梯轴（Ø44/Ø30 + Ø16 轴向孔）的正视图隐藏边里就有这么一条。
    """
    points = _line_points(15.0, 0.0, -15.0, 0.0) + _line_points(-15.0, 0.0, 15.0, 0.0)[1:]

    fitted = refit(Polyline2D(points=points))

    assert isinstance(fitted, Line2D), f"退化圆未还原，实际 {type(fitted).__name__}"
    assert sorted([fitted.start[0], fitted.end[0]]) == pytest.approx([-15.0, 15.0], abs=1e-9)
    assert fitted.start[1] == pytest.approx(0.0, abs=1e-9)
    assert fitted.end[1] == pytest.approx(0.0, abs=1e-9)


def test_major_arc_keeps_its_sweep():
    """大于半圆的弧必须保留真实扫掠方向。

    首尾角直接取 atan2 会把 270° 的弧按 −90° 的互补弧采样，弧会跑到零件外面去 ——
    实测 cadsy 语料 part_0016 的 R2 圆角就这么越出实体 X 边界 3.2 mm。
    """
    points = _arc_points(0.0, 0.0, 1.0, math.radians(45.0), math.radians(315.0), n=72)

    fitted = refit(Polyline2D(points=points))

    assert isinstance(fitted, Arc2D)
    xs = [p[0] for p in fitted.sample()]
    assert min(xs) == pytest.approx(-1.0, abs=1e-6), "弧丢了 180° 附近的部分，扫掠被截短"
    assert max(xs) == pytest.approx(math.cos(math.radians(45.0)), abs=1e-6)

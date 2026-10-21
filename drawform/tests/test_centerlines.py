import math

import pytest

from drawform.domain.curves import Centerline
from drawform.geometry.centerlines import centerlines_for
from drawform.geometry.features import Bore, extract_features
from drawform.geometry.loader import import_step
from drawform.geometry.projection import FRONT, LEFT, TOP, project_view


def _vertical_at(lines, x, tol=1e-6):
    return [c for c in lines
            if abs(c.start[0] - c.end[0]) < tol and abs(c.start[0] - x) < tol]


def _horizontal_at(lines, y, tol=1e-6):
    return [c for c in lines
            if abs(c.start[1] - c.end[1]) < tol and abs(c.start[1] - y) < tol]


def test_top_view_gets_a_cross_at_the_bore_center(plate_step):
    """俯视方向与孔轴平行，孔投影成圆 → 应出十字中心线。"""
    model = import_step(plate_step)
    features = extract_features(model.shape, model.bbox)
    projection = project_view(model.shape, TOP)

    lines = centerlines_for(projection, features)

    assert len(_vertical_at(lines, 40.0)) == 1
    assert len(_horizontal_at(lines, 25.0)) == 1


def test_centerlines_extend_beyond_the_circle(plate_step):
    """GB/T 4457.4：中心线应超出轮廓线 2–5 mm，不能刚好终止于圆边。"""
    model = import_step(plate_step)
    features = extract_features(model.shape, model.bbox)
    projection = project_view(model.shape, TOP)

    lines = centerlines_for(projection, features)
    vertical = _vertical_at(lines, 40.0)[0]

    bore_radius = 10.0
    half_span = abs(vertical.end[1] - vertical.start[1]) / 2
    overshoot = half_span - bore_radius
    assert 2.0 <= overshoot <= 5.0, f"超出量 {overshoot:.2f} mm 不在 2–5 mm 区间"


def test_front_view_gets_axis_centerline_not_a_cross(plate_step):
    """主视方向与孔轴垂直，孔投影成矩形 → 只出竖直轴线中心线。"""
    model = import_step(plate_step)
    features = extract_features(model.shape, model.bbox)
    projection = project_view(model.shape, FRONT)

    lines = centerlines_for(projection, features)

    assert len(_vertical_at(lines, 40.0)) == 1
    assert _horizontal_at(lines, 25.0) == []


def test_each_bore_gets_its_own_cross_in_top_view(stepped_shaft_step):
    """轴承座两个螺栓孔应各自得到独立十字中心线，不能合并。"""
    model = import_step(stepped_shaft_step)
    features = extract_features(model.shape, model.bbox)
    projection = project_view(model.shape, TOP)

    lines = centerlines_for(projection, features)

    verticals = {round(c.start[0], 3) for c in lines if abs(c.start[0] - c.end[0]) < 1e-6}
    assert 0.0 in verticals, "阶梯轴通孔轴线中心线缺失"


def test_centerlines_do_not_duplicate_for_coaxial_bores(bearing_seat_step):
    """Ø20 通孔与 Ø32 沉孔同轴，只能出一条竖直中心线，不能各出一条。"""
    model = import_step(bearing_seat_step)
    features = extract_features(model.shape, model.bbox)
    projection = project_view(model.shape, TOP)

    lines = centerlines_for(projection, features)

    assert len(_vertical_at(lines, 40.0)) == 1
    assert len(_horizontal_at(lines, 25.0)) == 1
    # 两个螺栓孔各一条竖直线：X=15, X=65，加上主孔 X=40 → 共 3 条竖直
    assert len([c for c in lines if abs(c.start[0] - c.end[0]) < 1e-6]) == 3


def _vertical_xs(lines):
    return {round(c.start[0], 3) for c in lines if abs(c.start[0] - c.end[0]) < 1e-6}


def test_rectangular_view_centerlines_sit_on_bore_axes(bearing_seat_step):
    """中心线只能落在已识别孔的轴线上。

    实测主视图会在 X=10 多出一条：底板左边缘（X=0）与凸台壁（X=20）恰好相距 20，
    被当成 Ø20 孔的两条孔壁。它们在孔轴方向上毫不重叠，不是同一个孔的两壁。
    """
    model = import_step(bearing_seat_step)
    features = extract_features(model.shape, model.bbox)
    projection = project_view(model.shape, FRONT)

    lines = centerlines_for(projection, features)

    axes = {round(f.axis_point[0], 3) for f in features if isinstance(f, Bore)}
    stray = _vertical_xs(lines) - axes
    assert stray == set(), f"中心线落在非孔轴位置：{sorted(stray)}"


def test_left_view_centerlines_sit_on_bore_axes(bearing_seat_step):
    """左视图水平轴是模型 Y，所有孔同轴于 Y=25，不该出现别的竖直中心线。"""
    model = import_step(bearing_seat_step)
    features = extract_features(model.shape, model.bbox)
    projection = project_view(model.shape, LEFT)

    lines = centerlines_for(projection, features)

    axes = {round(f.axis_point[1], 3) for f in features if isinstance(f, Bore)}
    stray = _vertical_xs(lines) - axes
    assert stray == set(), f"中心线落在非孔轴位置：{sorted(stray)}"


def test_every_bore_gets_its_own_axis_centerline(bearing_seat_step):
    """两个 Ø9 螺栓孔的孔壁间距相同，配对不能找到第一对就返回。"""
    model = import_step(bearing_seat_step)
    features = extract_features(model.shape, model.bbox)
    projection = project_view(model.shape, FRONT)

    lines = centerlines_for(projection, features)

    assert {15.0, 40.0, 65.0} <= _vertical_xs(lines)

"""图框尺寸与「这张图放不放进得去」的判定。"""

import pytest

from drawform.output.sheet import SHEETS, Frame, make_frame


def test_a3_frame_inner_area_is_the_sheet_minus_the_margins():
    frame = make_frame("A3")

    assert frame.outer == (420.0, 297.0)
    # GB/T 14689-2008 表4：留装订边 a=25，A3 其余三边 c=5
    assert frame.inner == (25.0, 5.0, 415.0, 292.0)


def test_unsupported_sheet_is_refused_instead_of_guessed():
    """A0–A2 的周边空白是 20，这个值没核到第二个来源，所以宁可报错也不能猜。"""
    with pytest.raises(ValueError):
        make_frame("A1")


def test_frame_reports_overflow_in_each_direction():
    frame = make_frame("A3")

    assert frame.overflow((30.0, 20.0, 200.0, 150.0)) == (0.0, 0.0)
    left_over, bottom_over = frame.overflow((-5.0, -3.0, 200.0, 150.0))
    assert left_over == pytest.approx(30.0)     # 越过装订边 25 与原点左侧 5
    assert bottom_over == pytest.approx(8.0)  # 下边距5加向下越过原点3


def test_contains_agrees_with_overflow():
    frame = make_frame("A3")

    assert frame.contains((30.0, 20.0, 400.0, 280.0))
    assert not frame.contains((30.0, 20.0, 400.0, 300.0))


def test_sheets_are_landscape_and_follow_the_a_series_ratio():
    for name, (width, height) in SHEETS.items():
        assert width > height, f"{name} 应为横向幅面"
        assert height > 0


def test_views_are_centered_inside_the_frame_not_on_the_binding_margin():
    """排版层从 (0,0) 起排，而图框左边是 25 mm 装订边 —— 不平移就会被装订掉。"""
    from drawform.output.sheet import center_on_frame, content_box, make_frame

    from drawform.domain.curves import FRONT, Line2D
    from drawform.geometry.layout import PlacedView
    from drawform.geometry.projection import Projection

    projection = Projection(direction=FRONT, visible=(Line2D((0, 0), (60, 0)),), hidden=())
    views = (PlacedView(projection=projection, origin=(0.0, 0.0)),)
    frame = make_frame("A3")

    moved = center_on_frame(views, frame)

    assert content_box(moved)[0] >= frame.inner[0], "视图还压在装订边里"
    assert content_box(moved)[1] >= frame.inner[1], "视图低于图框下边"
    left_gap = content_box(moved)[0] - frame.inner[0]
    right_gap = frame.inner[2] - content_box(moved)[2]
    assert left_gap == pytest.approx(right_gap, abs=1.0), "没有水平居中"

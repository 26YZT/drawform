import pytest

from drawform.geometry.layout import layout_views
from drawform.geometry.loader import import_step
from drawform.geometry.projection import FRONT, LEFT, TOP, project_view


def _placed(model, mode):
    return layout_views(
        (project_view(model.shape, FRONT),
         project_view(model.shape, TOP),
         project_view(model.shape, LEFT)),
        mode=mode,
    )


def _box(view):
    pts = [p for c in tuple(view.projection.visible) + tuple(view.projection.hidden) + tuple(view.centerlines)
           for p in c.sample()]
    ox, oy = view.origin
    return (ox + min(p[0] for p in pts), oy + min(p[1] for p in pts),
            ox + max(p[0] for p in pts), oy + max(p[1] for p in pts))


def _view(placed, name):
    return next(v for v in placed if v.projection.direction.name == name)


def test_first_angle_places_top_view_below_front(plate_step):
    """GB/T 第一角：俯视图在主视图正下方。放错方向等于图纸不符合国标。"""
    model = import_step(plate_step)

    placed = _placed(model, "first_angle")

    front = _box(_view(placed, "front"))
    top = _box(_view(placed, "top"))
    assert top[3] < front[1], f"俯视图顶边 {top[3]:.1f} 应在主视图底边 {front[1]:.1f} 之下"


def test_first_angle_places_left_view_to_the_right(plate_step):
    """GB/T 第一角：左视图（从左向右投射）位于主视图右方。"""
    model = import_step(plate_step)

    placed = _placed(model, "first_angle")

    front = _box(_view(placed, "front"))
    left = _box(_view(placed, "left"))
    assert left[0] > front[2], f"左视图左边 {left[0]:.1f} 应在主视图右边 {front[2]:.1f} 之右"


def test_third_angle_flips_the_arrangement(plate_step):
    """ASME 第三角：俯视图在主视图上方，左视图在左方。"""
    model = import_step(plate_step)

    placed = _placed(model, "third_angle")

    front = _box(_view(placed, "front"))
    top = _box(_view(placed, "top"))
    left = _box(_view(placed, "left"))
    assert top[1] > front[3], "第三角俯视图应在主视图上方"
    assert left[2] < front[0], "第三角左视图应在主视图左方"


def test_views_never_overlap(plate_step):
    model = import_step(plate_step)

    for mode in ("first_angle", "third_angle"):
        placed = _placed(model, mode)
        boxes = [_box(v) for v in placed]
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                a, b = boxes[i], boxes[j]
                disjoint = a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1]
                assert disjoint, f"{mode} 下视图 {i} 与 {j} 重叠: {a} vs {b}"


def test_gap_between_views_is_at_least_the_requested_clearance(plate_step):
    model = import_step(plate_step)

    placed = _placed(model, "first_angle")

    front = _box(_view(placed, "front"))
    top = _box(_view(placed, "top"))
    left = _box(_view(placed, "left"))
    assert front[1] - top[3] >= 15.0
    assert left[0] - front[2] >= 15.0


def test_views_are_aligned_on_their_shared_axis(plate_step):
    """投影关系要求俯视图与主视图沿同一投影线对齐（X 起点一致），而不是中心对齐。

    中心对齐只是两者 X 范围恰好相同时的巧合，不是制图规则。
    """
    model = import_step(plate_step)

    placed = _placed(model, "first_angle")

    front = _box(_view(placed, "front"))
    top = _box(_view(placed, "top"))
    assert front[0] == pytest.approx(top[0], abs=1e-6)
    assert front[2] == pytest.approx(top[2], abs=1e-6)


def test_front_and_left_view_share_their_horizontal_projection_line(plate_step):
    """主视图与左视图必须等高对齐（Y 范围一致），否则读图时无法对应。"""
    model = import_step(plate_step)

    placed = _placed(model, "first_angle")

    front = _box(_view(placed, "front"))
    left = _box(_view(placed, "left"))
    assert front[1] == pytest.approx(left[1], abs=1e-6)
    assert front[3] == pytest.approx(left[3], abs=1e-6)

import math

import pytest

from drawform.geometry.projection import FRONT, TOP, project_view


def _key(curve):
    """曲线的几何指纹：同一位置同一直线/圆必须给出同一个 key。"""
    pts = [(round(p[0], 3), round(p[1], 3)) for p in curve.sample()]
    if hasattr(curve, "center"):
        return (type(curve).__name__, (curve.center[0], curve.center[1]),
                round(curve.radius, 6), getattr(curve, "start_angle", None),
                getattr(curve, "end_angle", None))
    return (type(curve).__name__, min(pts[0], pts[-1]), max(pts[0], pts[-1]))


def _extent(curves):
    pts = [p for c in curves for p in c.sample()]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return max(xs) - min(xs), max(ys) - min(ys)


def test_front_view_of_plate_has_correct_projected_extent(plate_step):
    from drawform.geometry.loader import import_step

    model = import_step(plate_step)

    projection = project_view(model.shape, FRONT)

    # 板 80 x 50 x 12，沿 -Y 看：水平 = X(80)，竖直 = Z(12)
    assert _extent(projection.visible) == pytest.approx((80.0, 12.0), abs=1e-4)


def test_top_view_of_plate_has_correct_projected_extent(plate_step):
    from drawform.geometry.loader import import_step

    model = import_step(plate_step)

    projection = project_view(model.shape, TOP)

    # 沿 +Z 看：水平 = X(80)，竖直 = Y(50)
    assert _extent(projection.visible) == pytest.approx((80.0, 50.0), abs=1e-4)


def test_front_view_exposes_the_hidden_bore_walls(plate_step):
    """Ø20 通孔沿 Z 穿过板。从前方看孔壁被板体遮挡，必须落在 hidden 而不是 visible。"""
    from drawform.geometry.loader import import_step

    model = import_step(plate_step)

    projection = project_view(model.shape, FRONT)

    assert projection.hidden, "通孔在隐藏线里，hidden 不应为空"

    hidden_xs = sorted({round(p[0], 3) for c in projection.hidden for p in c.sample()})
    # 孔心 X=40，半径 10 → 孔壁投影在 X=30 与 X=50
    assert 30.0 in hidden_xs and 50.0 in hidden_xs

    visible_xs = {round(p[0], 3) for c in projection.visible for p in c.sample()}
    assert 30.0 not in visible_xs and 50.0 not in visible_xs, "隐藏边被当成可见边输出（HLR 消隐未生效）"


def test_refit_restores_stepped_shaft_hidden_lines(stepped_shaft_step):
    """阶梯轴的隐藏边里，OCCT 把精确直线存成了 B 样条，离散化后是折线。

    零件只有直线与圆，任何残留折线都说明 refit 没接进 project_view。
    旧断言写的是「Line2D 条数 >= 8」，那个数字是在重复边尚未清理时标定的，
    清理后条数会变，因此改成按「不应有折线残留」断言，更强也不依赖重复计数。
    """
    from drawform.geometry.loader import import_step
    from drawform.geometry.projection import Polyline2D

    model = import_step(stepped_shaft_step)

    projection = project_view(model.shape, FRONT)

    assert projection.hidden, "轴向孔与台阶在正视图中应有隐藏边"
    leftover = [c for c in projection.hidden if isinstance(c, Polyline2D)]
    assert leftover == [], (
        f"隐藏边残留 {len(leftover)} 条折线，refit 未接进 project_view；"
        f"实际类型 = {[type(c).__name__ for c in projection.hidden]}"
    )


def test_a_layer_never_carries_the_same_edge_twice(bearing_seat_step):
    """同一层里重复画同一条线，虚线会叠出深浅不一的脏线，客户在 AutoCAD 里也会看到重复实体。

    实测：轴承座的正视图 hidden 层 31 条里有 9 对完全重合的线段。
    """
    from drawform.geometry.loader import import_step

    model = import_step(bearing_seat_step)

    projection = project_view(model.shape, FRONT)

    for name, group in (("visible", projection.visible), ("hidden", projection.hidden)):
        keys = [_key(c) for c in group]
        duplicated = [k for k in set(keys) if keys.count(k) > 1]
        assert not duplicated, f"{name} 层存在重复边：{duplicated}"


def test_hidden_lines_coincident_with_visible_ones_are_suppressed(bearing_seat_step):
    """平板顶面与底面的轮廓在俯视图里投影完全重合，隐藏线与可见线叠在一起。

    重合的虚线压在实线上既不增加信息又让图面发灰，制图上属于多余线型。
    """
    from drawform.geometry.loader import import_step

    model = import_step(bearing_seat_step)

    projection = project_view(model.shape, TOP)

    assert projection.visible, "俯视图没有可见轮廓"
    assert projection.hidden == (), f"与可见线重合的隐藏线未被抑制：{projection.hidden}"


def test_suppression_does_not_delete_genuinely_hidden_lines(plate_step):
    """抑制只针对重合线：Ø20 通孔的孔壁在正视图里没有任何可见线与之重合，必须保留。"""
    from drawform.geometry.loader import import_step

    model = import_step(plate_step)

    projection = project_view(model.shape, FRONT)

    hidden_xs = {round(p[0], 3) for c in projection.hidden for p in c.sample()}
    assert 30.0 in hidden_xs and 50.0 in hidden_xs


def test_hidden_line_covered_by_visible_geometry_is_suppressed(stepped_shaft_step):
    """整条被可见线遮住的虚线是多余线型，即使它不是和某一条可见线一一对应。

    实测阶梯轴正视图里，台阶内沿（Ø30 段在 z=30 的边）整条落在可见的台阶线
    (-22,30)-(22,30) 之内；剖切后的轴承座更明显：远侧上沿被近侧若干段可见线遮住。
    只比「整条对整条重合」漏掉了这类。
    """
    from drawform.geometry.loader import import_step
    from drawform.geometry.projection import Line2D

    model = import_step(stepped_shaft_step)

    projection = project_view(model.shape, FRONT)

    on_shoulder = [c for c in projection.hidden
                   if isinstance(c, Line2D)
                   and abs(c.start[1] - 30.0) < 1e-6 and abs(c.end[1] - 30.0) < 1e-6]
    assert on_shoulder == [], f"台阶处被可见线整条遮住的虚线未抑制：{on_shoulder}"

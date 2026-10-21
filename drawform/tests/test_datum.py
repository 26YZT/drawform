"""基准代号（GB/T 1182）。

图纸上有一批尺寸写"25""35"，是从包围盒最小角那个面量出去的 —— 方案层早就声明了
`Datum("A","B","C")`，但那三个字母只活在 linter 里：2026-10-07 实测 part_0001 出图后
纸上有 14 条尺寸文字、`基准` 出现 **0 次**，输出层 grep 不到任何 datum 代码。
车间看图纸第一眼要找的就是"从哪量"，找不到这张图就没法开工。

这里补的是**把已经执行了的意图显式化**，不是新增设计判断：基准面仍是包围盒最小角，
定位尺寸仍从它量，只是现在纸上画出来了。
"""

import pytest

from drawform.dimensioning.place import place_datum_marks, place_scheme
from drawform.dimensioning.scheme import derive_scheme
from drawform.geometry.features import extract_features
from drawform.geometry.loader import import_step
from drawform.geometry.projection import FRONT, LEFT, TOP, project_view


def _prepared(model):
    features = extract_features(model.shape, model.bbox)
    scheme = derive_scheme(model, features)
    projections = {d.name: project_view(model.shape, d) for d in (FRONT, TOP, LEFT)}
    return scheme, projections


def test_only_datums_actually_referenced_get_a_symbol(plate_step):
    """80×50×12 板上所有孔都沿 z 钻，定位只用到 x、y 两个基准。

    给 C 也画一个代号等于教图纸说谎：纸上标了基准却没有任何尺寸从它量起。
    """
    model = import_step(plate_step)
    scheme, projections = _prepared(model)

    letters = sorted(m.letter for m in place_datum_marks(scheme, projections))

    assert letters == ["A", "B"], f"只该有被引用的两个基准，实际 {letters}"


def test_a_bore_drilled_along_x_moves_the_referenced_datums(plate_step, cross_bore_step):
    """沿 x 钻的孔，定位尺寸落在 y 与 z 上 → 该出现 B、C，而不是 A。

    基准字母与世界轴的对应是写死的约定（A=x、B=y、C=z），这条测试钉住它：
    哪天有人把字母顺序调了，尺寸和代号会悄悄对不上，分数一点不会掉。
    """
    model = import_step(cross_bore_step)
    scheme, projections = _prepared(model)

    letters = sorted(m.letter for m in place_datum_marks(scheme, projections))

    assert letters == ["B", "C"]


def test_the_mark_anchors_on_real_projected_outline_not_the_bounding_box(plate_step):
    """代号的锚点必须落在真实投影轮廓上。

    拿包围盒角点凑的话，一个左下角被切掉一块的零件会把代号悬空指在空气里 ——
    这与 M3-d「深度端点必须落在真实投影轮廓上」是同一类错误，指标完全看不见。
    """
    model = import_step(plate_step)
    scheme, projections = _prepared(model)

    marks = place_datum_marks(scheme, projections)
    assert marks, "板上定位尺寸存在，就该有基准代号"
    for mark in marks:
        box = [p for curve in projections[mark.view].visible for p in curve.sample()]
        along = 0 if mark.outward == (-1.0, 0.0) else 1
        extreme = min(p[along] for p in box)
        assert mark.anchor[along] == pytest.approx(extreme, abs=0.02), \
            f"{mark.letter} 的锚点 {mark.anchor} 没贴到该方向真实轮廓 {extreme}"


def test_a_mark_sits_on_the_view_that_dimensions_from_it(plate_step):
    """基准代号要和它服务的定位尺寸在同一个视图上。

    尺寸在顶视图上从左边量，代号却画在主视图上 —— 读图的人得自己猜哪个面是 A。
    """
    model = import_step(plate_step)
    scheme, projections = _prepared(model)
    dims = place_scheme(scheme, projections)
    positions = {(d.view, d.axis) for d in dims if d.kind == "position"}

    for mark in place_datum_marks(scheme, projections):
        axis = {"A": "x", "B": "y", "C": "z"}[mark.letter]
        assert (mark.view, axis) in positions, \
            f"{mark.letter} 画在 {mark.view}，但那个视图上没有从它量起的 {axis} 尺寸"


def test_the_drawing_draws_a_box_with_the_letter(plate_step):
    """方框 + 字母 + 引出线，缺一个就不是基准代号。"""
    from drawform.geometry.layout import layout_views
    from drawform.output.drawing import build_drawing

    model = import_step(plate_step)
    scheme, projections = _prepared(model)
    placed = layout_views(tuple(projections.values()))
    dims = place_scheme(scheme, projections)

    drawing = build_drawing(placed, dimensions=dims,
                            datum_marks=place_datum_marks(scheme, projections))

    layers = dict(drawing.lines)
    assert "DATUM" in layers, "基准代号应当自成一层，客户二次编辑时要能整体关掉的可见性"
    letters = sorted(t.content for t in drawing.texts if t.content in ("A", "B", "C"))
    assert letters == ["A", "B"]
    closed = [run for run in layers["DATUM"] if len(run) == 5 and run[0] == run[-1]]
    assert len(closed) == 2, f"两个代号该有两个闭合方框，实际 {len(closed)}"


def test_marks_are_written_into_svg_and_dxf(plate_step, tmp_path):
    """三个出口都得有，否则网页看到的和打印出去的不是一张图。"""
    from drawform.output.dxf import write_dxf
    from drawform.output.svg import write_svg
    from drawform.geometry.layout import layout_views

    model = import_step(plate_step)
    scheme, projections = _prepared(model)
    placed = layout_views(tuple(projections.values()))
    dims = place_scheme(scheme, projections)
    marks = place_datum_marks(scheme, projections)

    svg = write_svg(placed, tmp_path / "a.svg", dimensions=dims, datum_marks=marks)
    dxf = write_dxf(placed, tmp_path / "a.dxf", dimensions=dims, datum_marks=marks)

    text = svg.read_text(encoding="utf8")
    assert ">A<" in text and ">B<" in text
    assert "DATUM" in dxf.read_text(encoding="utf8")


def test_a_crossing_leader_slides_along_its_own_datum_face():
    """引出线穿尺寸线时，代号沿**它自己那条棱**滑开，不是把线剪短或抬离基准面。

    基准标注的是一个平面，平面上任何一点贴代号都同样成立；滑出这条棱就不成立了。
    """
    from drawform.output.drawing import DatumMark, datum_anchor_slide

    mark = DatumMark(view="FRONT", anchor=(40.0, 0.0), outward=(0.0, -1.0), letter="B",
                     standoff=30.0, reach=20.0)
    lanes = [((30.0, -15.0), (80.0, -15.0))]      # 这条尺寸线正好压在引出线上

    delta = datum_anchor_slide(mark, lanes)

    assert delta is not None, "有地方可滑就必须滑开"
    assert 0.0 < abs(delta) <= mark.reach, delta
    assert delta < 0.0, "往 x 负方向才出得去那条线"


def test_a_mark_on_a_point_datum_has_nowhere_to_slide():
    """棱长为零（基准就是这个点）时代号不许飘走 —— 宁可留下一条被报出来的冲突。"""
    from drawform.output.drawing import DatumMark, datum_anchor_slide

    mark = DatumMark(view="FRONT", anchor=(40.0, 0.0), outward=(0.0, -1.0), letter="B",
                     standoff=30.0, reach=0.0)

    assert datum_anchor_slide(mark, [((30.0, -15.0), (80.0, -15.0))]) is None


def test_a_box_sitting_on_a_dimension_line_moves_too():
    """引出线没压线、**方框**压线 —— 一样要挪。

    检测器看的是整个代号（方框四条边 + 引出线），避让若只查那根线，
    方框会照样盖在尺寸行上，而报出来的冲突一条没少。
    """
    from drawform.output.drawing import DatumMark, datum_anchor_slide

    mark = DatumMark(view="FRONT", anchor=(40.0, 0.0), outward=(0.0, -1.0), letter="B",
                     standoff=4.0, reach=20.0)          # 引出线短，方框落在 y -11..-4
    lanes = [((30.0, -8.0), (80.0, -8.0))]              # 正好穿过方框，够不着引出线

    delta = datum_anchor_slide(mark, lanes)

    assert delta is not None and delta != 0.0, "方框压线也必须躲"
    assert abs(delta) <= mark.reach


def test_a_clear_leader_does_not_move():
    """不穿就不动。每次出图都重排一遍位置，回归测试就永远追不上。"""
    from drawform.output.drawing import DatumMark, datum_anchor_slide

    mark = DatumMark(view="FRONT", anchor=(40.0, 0.0), outward=(0.0, -1.0), letter="B",
                     standoff=30.0, reach=20.0)

    assert datum_anchor_slide(mark, [((60.0, -15.0), (80.0, -15.0))]) == 0.0


def test_a_mark_steps_past_the_dimension_lanes_it_would_otherwise_cross(ribbed_plate_step):
    """代号不能压在尺寸带上。

     ribbed 板那三条壁厚的尺寸线就在视图下方，而 y 基准的引出方向也是往下 ——
    方框不往外让，就直接盖在尺寸行上（截图里 B 正是这样糊住的）。
    """
    model = import_step(ribbed_plate_step)
    scheme, projections = _prepared(model)
    dims = place_scheme(scheme, projections)

    marks = place_datum_marks(scheme, projections, dims)
    for mark in marks:
        box = [p for curve in projections[mark.view].visible for p in curve.sample()]
        if mark.outward == (0.0, -1.0):
            lanes = [d.base[1] for d in dims
                     if d.view == mark.view and d.base is not None and d.base[1] < min(p[1] for p in box)]
            deepest = min(lanes) if lanes else min(p[1] for p in box)
            assert mark.anchor[1] - mark.standoff - 3.5 <= deepest, \
                f"{mark.letter} 的方框底边没让过最外一条尺寸带 {deepest}"

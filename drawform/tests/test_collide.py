"""图面互斥检测器本身要可判真假。

这个模块存在的理由：落位指标只问"画了没有"，不问"能不能读"。壁厚瀑布那次
（F2/落位/门三项全绿，截图一看是瀑布）就是缺这么一层。所以它必须先能在
人工构造的两种情形上分别报与不报 —— 否则它又是一个"看起来在工作"的桩。
"""

import pytest

from drawform.output.collide import (LABEL_MAX_SHIFT, avoid_lines, find_collisions,
                           separate_labels, text_box)
from drawform.output.drawing import Drawing, DrawingItem, Text


def _drawing(items=(), texts=()):
    return Drawing(sheet="A3", lines=(), arrows=(), texts=tuple(texts),
                   items=tuple(items))


def _text(content, x, y, owner):
    return Text(content, x, y, "middle", owner=owner)


def test_overlapping_text_from_two_dimensions_is_reported():
    left = _text("22.5", 0.0, 0.0, "dim:a")
    right = _text("35", 1.0, 0.0, "dim:b")

    found = find_collisions(_drawing(texts=[left, right]))

    assert [c.kind for c in found] == ["text/text"]


def test_a_dimension_never_collides_with_its_own_extension_line():
    """同归属必须放过：尺寸界线挨着自己的字是正常画法。

    这条不守住，检测器会把每一条尺寸都报成冲突，然后没人再看它的输出。
    """
    owner = "dim:(gap-x-10.000, gap)"
    line = DrawingItem(owner, "DIM", ((-20.0, 0.0), (20.0, 0.0)))

    found = find_collisions(_drawing(items=[line], texts=[_text("8", 0.0, 0.0, owner)]))

    assert found == ()


def test_a_symbol_leader_crossing_another_dimensions_line_is_reported():
    leader = DrawingItem("datum:B", "DATUM", ((0.0, 20.0), (0.0, -20.0)))
    dim = DrawingItem("dim:span", "DIM", ((-10.0, 0.0), (10.0, 0.0)))

    found = find_collisions(_drawing(items=[leader, dim]))

    assert "leader/line" in [c.kind for c in found]


def test_extension_lines_may_cross_the_part_outline():
    """尺寸界线穿过零件轮廓是画法，不是缺陷 —— 报出来检测器就废了。"""
    outline = DrawingItem("view:front", "VISIBLE", ((-5.0, -5.0), (-5.0, 5.0)))
    dim = DrawingItem("dim:span", "DIM", ((-5.0, 0.0), (-20.0, 0.0)))

    found = find_collisions(_drawing(items=[outline, dim]))

    assert found == ()


def test_text_sitting_on_top_of_the_part_outline_is_reported():
    """文字必须落在空处。压在轮廓上的字在车间里读不出来。"""
    outline = DrawingItem("view:front", "VISIBLE", ((0.0, -5.0), (0.0, 5.0)))

    found = find_collisions(_drawing(
        items=[outline], texts=[_text("45", 0.0, 0.0, "dim:span")]))

    assert [c.kind for c in found] == ["text/line"]


def test_touching_endpoints_are_not_a_crossing():
    """两条线共用端点（箭头接尺寸线）不算相交，否则每处箭头都成缺陷。"""
    a = DrawingItem("x:a", "DIM", ((0.0, 0.0), (10.0, 0.0)))
    b = DrawingItem("x:b", "DIM", ((10.0, 0.0), (10.0, 8.0)))

    assert find_collisions(_drawing(items=[a, b])) == ()


def test_text_box_follows_anchor_and_rotation():
    from drawform.output.drawing import CHAR_WIDTH, TEXT_HEIGHT

    assert text_box(_text("12", 0.0, 0.0, "o")) == pytest.approx(
        (-CHAR_WIDTH * TEXT_HEIGHT, -TEXT_HEIGHT / 2.0,
         CHAR_WIDTH * TEXT_HEIGHT, TEXT_HEIGHT / 2.0))
    # 旋转 90° 后宽高互换：竖排的字占的是窄而高的那条
    vertical = text_box(Text("12", 0.0, 0.0, "middle", -90.0, "o"))
    assert (vertical[3] - vertical[1]) > (vertical[2] - vertical[0])


def test_colliding_labels_are_pushed_apart_without_changing_what_they_say():
    texts = [_text("22.5", 0.0, 0.0, "dim:a"), _text("35", 1.0, 0.0, "dim:b")]

    moved = separate_labels(texts)

    assert [(t.content, t.owner) for t in moved] == [("22.5", "dim:a"), ("35", "dim:b")]
    assert find_collisions(_drawing(texts=moved)) == ()
    # 只沿重叠较小的那个轴分开：横向挤就横向让，不许把字抬离自己的尺寸线
    assert moved[0].y == 0.0 and moved[1].y == 0.0


def test_labels_that_never_collided_are_left_exactly_where_they_were():
    texts = [_text("22.5", 0.0, 0.0, "dim:a"), _text("35", 60.0, 40.0, "dim:b")]

    assert separate_labels(texts) == tuple(texts)


def test_a_dimensions_own_labels_are_not_pushed_away_from_each_other():
    """同归属的字叠在一起不是缺陷（箭头旁的两段文字本来就是一起画的），动了就白动。"""
    texts = [_text("4×", 0.0, 0.0, "dim:a"), _text("⌀9", 1.0, 0.0, "dim:a")]

    assert separate_labels(texts) == tuple(texts)


def test_a_pile_of_labels_spreads_out_instead_of_sliding_as_one():
    """中心完全相等时必须有左右之分，否则整堆字会一起平移，一处都没分开。"""
    texts = [_text("12.5", 0.0, 0.0, f"dim:{i}") for i in range(4)]

    moved = separate_labels(texts)

    assert len({(t.x, t.y) for t in moved}) == len(texts)


def test_separation_never_moves_a_label_further_than_the_bound():
    """挪太远等于把字从自己的尺寸上摘下来 —— 宁可可读性上留一条报出来的冲突。"""
    texts = [_text("12.5", 0.0, 0.0, f"dim:{i}") for i in range(6)]

    moved = separate_labels(texts)

    assert len(moved) == len(texts)
    for before, after in zip(texts, moved):
        assert abs(after.x - before.x) <= LABEL_MAX_SHIFT
        assert abs(after.y - before.y) <= LABEL_MAX_SHIFT


def test_a_rotated_label_is_separated_from_its_neighbour_too():
    """垂直尺寸的字头朝左，包围盒是瘦高的；只按宽排的字分开会漏掉这一类。"""
    texts = [Text("225", 0.0, 0.0, "middle", -90.0, "dim:a"),
             Text("350", 0.0, 1.0, "middle", -90.0, "dim:b")]

    moved = separate_labels(texts)

    assert find_collisions(_drawing(texts=moved)) == ()
    assert moved[0].x == 0.0 and moved[1].x == 0.0, "瘦高的字应当上下让开，不该横着跑"


def test_build_drawing_separates_labels_that_land_on_top_of_each_other(plate_step):
    """分离必须发生在 `build_drawing` 里：函数单测通过不等于交出去的图上真的分开了。"""
    from drawform.dimensioning.place import PlacedDimension
    from drawform.dimensioning.scheme import derive_scheme
    from drawform.geometry.features import extract_features
    from drawform.geometry.layout import layout_views
    from drawform.geometry.loader import import_step
    from drawform.geometry.projection import FRONT, LEFT, TOP, project_view
    from drawform.output.drawing import build_drawing

    model = import_step(plate_step)
    features = extract_features(model.shape, model.bbox)
    scheme = derive_scheme(model, features)
    projections = {d.name: project_view(model.shape, d) for d in (FRONT, TOP, LEFT)}
    placed = layout_views(tuple(projections.values()))
    dims = (PlacedDimension("linear", "front", (0.0, 0.0), (40.0, 0.0), (0.0, 20.0),
                            "22.5", 22.5, axis="x", source=("bore:1", "x")),
            PlacedDimension("linear", "front", (0.0, 1.0), (40.0, 1.0), (0.0, 20.0),
                            "35", 35.0, axis="x", source=("bore:2", "x")))

    drawing = build_drawing(placed, dimensions=dims)

    assert [c for c in find_collisions(drawing) if c.kind == "text/text"] == []


def _item(owner, layer, points):
    return DrawingItem(owner=owner, layer=layer, points=tuple(points))


def test_a_label_sitting_on_someone_elses_line_is_slid_off_it():
    """真图上的样子：字横在一条轮廓竖边上面，左右滑就能滑出去。"""
    texts = [_text("40", 20.0, 0.0, "dim:a")]
    lines = [_item("view:front", "VISIBLE", [(20.0, -10.0), (20.0, 10.0)])]

    moved = avoid_lines(texts, lines)

    assert moved != tuple(texts)
    assert find_collisions(_drawing(items=lines, texts=moved)) == ()


def test_a_labels_own_line_does_not_push_it_anywhere():
    """同归属豁免对"字压线"同样成立：尺寸线自己的字本来就在尺寸线上。"""
    # 线要画得短：换成一条横贯全图的长线，字本来就躲不开，"没动"就成了空断言。
    texts = [_text("40", 20.0, 0.0, "dim:a")]
    lines = [_item("dim:a", "DIM", [(18.0, -1.0), (22.0, 1.0)])]

    assert avoid_lines(texts, lines) == tuple(texts)


def test_frame_layer_lines_are_not_what_we_are_avoiding():
    """躲线与判线必须是同一份筛选，否则字会去躲一条检测器根本不看的线。"""
    texts = [_text("40", 20.0, 0.0, "dim:a")]
    lines = [_item("frame", "FRAME", [(18.0, -1.0), (22.0, 1.0)])]

    assert avoid_lines(texts, lines) == tuple(texts)


def test_avoiding_moves_the_letter_along_its_own_axis_only():
    texts = [_text("40", 20.0, 0.0, "dim:a")]
    lines = [_item("view:front", "VISIBLE", [(18.0, -1.0), (22.0, 1.0)])]

    moved = avoid_lines(texts, lines)

    assert moved != tuple(texts)
    assert moved[0].y == 0.0, "横排的字只能沿尺寸线左右滑，抬起来就离开这条尺寸了"


def test_a_label_with_nowhere_to_go_stays_and_is_still_reported():
    """躲不开时不许悄悄留在原地冒充干净：位置不动，冲突照旧报出来。"""
    texts = [_text("40", 0.0, 0.0, "dim:a")]
    lines = [_item(f"view:{x}", "VISIBLE", [(float(x), -10.0), (float(x), 10.0)])
             for x in range(-20, 21)]

    moved = avoid_lines(texts, lines)

    assert moved == tuple(texts)
    kinds = {c.kind for c in find_collisions(_drawing(items=lines, texts=moved))}
    assert kinds == {"text/line"}


def test_avoiding_respects_the_same_shift_bound_as_separating():
    texts = [_text("40", 0.0, 0.0, "dim:a")]
    lines = [_item("view:front", "VISIBLE", [(-1.0, -5.0), (-1.0, 5.0)])]

    moved = avoid_lines(texts, lines)

    assert abs(moved[0].x) <= LABEL_MAX_SHIFT


def test_a_vertical_letter_slides_up_and_down_not_sideways():
    """字头朝左（垂直尺寸）的字只能上下滑。横竖都用同一根轴，是上一轮刚栽过的坑。"""
    texts = [Text("225", 0.0, 20.0, "middle", -90.0, "dim:a")]
    lines = [_item("view:front", "VISIBLE", [(-10.0, 20.0), (10.0, 20.0)])]

    moved = avoid_lines(texts, lines)

    assert moved != tuple(texts)
    assert moved[0].x == 0.0, "瘦高的字上下让开就行，横着挪会离开这条尺寸线"

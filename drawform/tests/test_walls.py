"""壁厚与台阶尺寸。

基线方案只发外形总尺寸 + 孔位 + 直径 + 深度，**平面之间的间距一条都没发过**。
真实图纸上这一类是主力：65 件里线性池漏标的 95/136 就是两个平行平面之间的间距。

要发它，先得能把「槽」和「肋」分开 —— 两者都是两个平行平面、间隙同为 4 mm，
区别只在中间有没有材料。
"""

import pytest

from drawform.dimensioning.constraints import (
    CLOSED_CHAIN, UNDER_CONSTRAINED, Chain, Sized, check_plan,
)
from drawform.dimensioning.scheme import derive_scheme
from drawform.geometry.features import extract_features, find_gaps, planar_faces
from drawform.geometry.loader import import_step


def _positions(model, axis):
    """平面沿该轴的位置，换算成「离包围盒最小角多远」。

    `planar_faces` 和 `extract_features` 一样返回全局坐标，减原点这件事留给方案层做；
    测试在这里减一次，是为了不跟着 STEP 文件摆放位置的变化改断言。
    """
    index = "xyz".index(axis)
    return sorted(round(f.position - model.origin[index], 3)
                  for f in planar_faces(model.shape) if f.axis == axis)


def test_planar_faces_lists_the_faces_perpendicular_to_each_axis(ribbed_plate_step):
    """40×30×8 板开两条 4 宽槽 → x 方向应有 6 个平面。

    0/40 是外廓，10/14 与 18/22 是槽壁，14 到 18 之间才是那道肋。
    """
    model = import_step(ribbed_plate_step)

    assert _positions(model, "x") == [0.0, 10.0, 14.0, 18.0, 22.0, 40.0]


def test_planar_faces_reports_the_face_centroid_not_an_arbitrary_point_on_the_plane(
        ribbed_plate_step):
    """质点是「中点探在哪儿」的依据，只给平面坐标定不出中点。

    两个平行面的 `position` 相同不代表它们对得上 —— 侧视方向可以错开整个板宽。
    平面上任取一点（OCCT 的 Plane().Location() 就是这么给的）会把中点探到空气里。
    """
    model = import_step(ribbed_plate_step)
    faces = [f for f in planar_faces(model.shape) if f.axis == "x"]
    left = min(faces, key=lambda f: f.position)

    relative = tuple(round(left.centroid[k] - model.origin[k], 3) for k in range(3))

    assert relative == (0.0, model.bbox[1] / 2.0, model.bbox[2] / 2.0)


def test_planar_faces_skips_faces_that_are_not_axis_parallel(angled_plate_step):
    """斜面不在这一步范围内：它的线性尺寸要先定投影方向。

    这块板切了个 45° 斜面，法向 (0.707, 0, 0.707)。守卫要是松了，它会被按主轴分量
    归到 x 或 z 上，于是 x 方向凭空多出一个"平面"，两个面之间的间隙就成了假壁厚。
    所以断言写在 x 面上：斜面只可能被算进 x 或 z，而 x 方向本来只剩左端面一个面。
    """
    model = import_step(angled_plate_step)
    faces = planar_faces(model.shape)

    assert len(faces) == 5, f"6 个平面里只有 5 个是轴平行的，收到 {len(faces)} 个"
    assert len([f for f in faces if f.axis == "x"]) == 1


def _gaps(model, axis):
    return sorted(round(g.gap, 3) for g in find_gaps(model.shape, model.bbox)
                  if g.axis == axis)


def test_find_gaps_reports_every_pair_of_adjacent_faces(ribbed_plate_step):
    """x 方向相邻面 0|10|14|18|22|40 → 5 对相邻间隙。

    槽宽和壁厚在图纸上都是正当的尺寸，所以「相邻」是发不发的判据，
    「中间是不是材料」不是。实测把空腔剔掉会发 159 条、F2 76.8%，
    全发是 274 条、F2 77.8% —— 剔掉反而亏，因为槽宽本来就是真图纸上会标的东西。
    """
    model = import_step(ribbed_plate_step)

    assert _gaps(model, "x") == [4.0, 4.0, 4.0, 10.0, 18.0]


def test_find_gaps_tags_which_pairs_are_material_and_which_are_air(ribbed_plate_step):
    """间隙三对都是 4 mm，只有材料探针能分出 14→18 是肋、另两对是槽。

    这个标记不用于删尺寸，用于排优先级和落位 —— 所以它必须是**可验的**：
    删掉探针，`solid` 会全变 True 或全变 False，这条就红。
    """
    model = import_step(ribbed_plate_step)
    rib = [g for g in find_gaps(model.shape, model.bbox)
           if g.axis == "x" and abs(g.low - 14.0) < 1e-6][0]
    slot = [g for g in find_gaps(model.shape, model.bbox)
            if g.axis == "x" and abs(g.low - 10.0) < 1e-6][0]

    assert rib.solid is True
    assert slot.solid is False


def test_find_gaps_does_not_report_the_overall_extent_as_a_gap(ribbed_plate_step):
    """0 到 40 那一对就是外廓，总尺寸已经发过了，再发一条是同自由度钉两次。

    y、z 两个方向上只有外廓那一对平面，所以它们应当一条间隙都不产出。
    """
    model = import_step(ribbed_plate_step)

    assert _gaps(model, "y") == []
    assert _gaps(model, "z") == []


def _scheme(model):
    return derive_scheme(model, extract_features(model.shape, model.bbox))


def _gap_values(plan):
    return sorted(round(c.value, 3) for c in plan.constraints
                  if isinstance(c, Sized) and c.dof == "gap")


def test_derive_scheme_dimensions_the_adjacent_gaps(ribbed_plate_step):
    """三条 4 mm 的间隙（两槽一肋）都要发出去。

    0→10 与 22→40 那两段超过外廓最大边的 20%（=8 mm），不算壁厚/台阶，不发。
    """
    model = import_step(ribbed_plate_step)

    assert _gap_values(_scheme(model).plan) == [4.0, 4.0, 4.0]


def test_a_complete_partition_of_the_outline_becomes_a_chain_with_one_reference(
        stepped_plate_step):
    """台阶板 z 方向 0|4|8 正好铺满外廓 8 —— 两条都发实尺就是封闭尺寸链。

    链必须**存在于方案里**，不是指望 linter 自己猜出来：`derive_scheme` 过去从不
    构造 `Chain`，65 件真实零件里 Chain 总数 = 0，`CLOSED_CHAIN` 一次都没触发过。
    """
    model = import_step(stepped_plate_step)
    plan = _scheme(model).plan

    chain = next(c for c in plan.constraints if isinstance(c, Chain) and c.name == "chain:z")
    assert chain.total == pytest.approx(model.bbox[2])
    assert len(chain.members) == 2
    assert len(chain.reference) == 1, "铺满外廓的链必须留一环降级成参考尺寸"


def test_keeping_the_largest_link_as_reference_leaves_no_closed_chain(stepped_plate_step):
    """留一环到底生效没有，由 CLOSED_CHAIN 自己说话，不由我说。

    这是那个检查第一次在生产产出上被真正跑到 —— 以前它只在手工构造 `Chain` 的单测里过过。
    """
    model = import_step(stepped_plate_step)
    scheme = _scheme(model)

    kinds = [v.kind for v in check_plan(scheme.plan, scheme.unknowns)]

    assert CLOSED_CHAIN not in kinds, f"留一环没生效：{kinds}"
    assert _gap_values(scheme.plan) == [4.0], "两条 4 只该发一条实尺，另一条留空"


def test_the_wall_dimension_lands_on_the_sheet(stepped_plate_step):
    """发了却没画到图上的尺寸不算数 —— 交付门正是按这条拦的。

    方案层能算出「这两个面之间是 4 mm」不等于图纸上有一条能改能量的 DIMENSION。
    """
    from drawform.dimensioning.place import place_scheme
    from drawform.geometry.projection import FRONT, LEFT, TOP, project_view

    model = import_step(stepped_plate_step)
    scheme = _scheme(model)
    projections = {d.name: project_view(model.shape, d) for d in (FRONT, TOP, LEFT)}

    dims = [d for d in place_scheme(scheme, projections) if d.source and d.source[1] == "gap"]

    assert len(dims) == 1, f"台阶那条 4 该落图，实际落了 {len(dims)} 条"
    assert dims[0].value == pytest.approx(4.0)
    assert dims[0].axis == "z"
    index = 0 if dims[0].p1[0] != dims[0].p2[0] else 1
    span = abs(dims[0].p2[index] - dims[0].p1[index])
    assert span == pytest.approx(4.0), "两端点必须真的隔着 4 mm，不是文字写着 4"


def test_the_undimensioned_link_is_still_an_unknown_pinned_by_the_chain(
        stepped_plate_step):
    """留空那一环仍然要登记成未知量 —— 它由「总尺寸 + 另一环」推出来，不是不重要。

    少了 Chain 这条链它就变成欠定，这正是链存在的意义。
    """
    model = import_step(stepped_plate_step)
    scheme = _scheme(model)

    gaps = [key for key in scheme.unknowns if key[1] == "gap"]
    pinned_directly = {(c.feature, c.dof) for c in scheme.plan.constraints
                       if isinstance(c, Sized)}

    assert len(gaps) == 2, "两环都该是未知量"
    assert len([k for k in gaps if k not in pinned_directly]) == 1
    assert [v for v in check_plan(scheme.plan, scheme.unknowns)
            if v.kind == UNDER_CONSTRAINED] == []


def test_adjacent_wall_dimensions_share_one_lane(ribbed_plate_step):
    """相邻且不重叠的壁厚尺寸必须排在同一条带上。

    早先每条尺寸各开一条带（`band` 每条 +1），15 条壁厚就往外排 120 mm ——
    浏览器里看到的是从零件底下甩出去的一条瀑布。落位指标 99.8% 全绿，
    因为它只问"画了没有"，不问"能不能读"。这张图是被截图抓住的。
    """
    from drawform.dimensioning.place import place_scheme
    from drawform.geometry.projection import FRONT, LEFT, TOP, project_view

    model = import_step(ribbed_plate_step)
    scheme = _scheme(model)
    projections = {d.name: project_view(model.shape, d) for d in (FRONT, TOP, LEFT)}

    lanes = {round(d.base[1], 3) for d in place_scheme(scheme, projections)
             if d.source and d.source[1] == "gap" and d.base is not None}

    assert len(lanes) == 1, f"三条相邻间隙该共用一条带，实际占了 {len(lanes)} 条：{lanes}"


def test_no_dimension_line_falls_inside_the_view_outline(ribbed_plate_step):
    """尺寸线一律排在视图轮廓之外。

    压在零件上的尺寸线会和轮廓线糊在一起，读图的人分不清哪条是边、哪条是尺寸。
    """
    from drawform.dimensioning.place import place_scheme
    from drawform.geometry.projection import FRONT, LEFT, TOP, project_view

    model = import_step(ribbed_plate_step)
    scheme = _scheme(model)
    projections = {d.name: project_view(model.shape, d) for d in (FRONT, TOP, LEFT)}
    placed = place_scheme(scheme, projections)

    for dim in placed:
        if dim.base is None:
            continue
        box = [p for curve in projections[dim.view].visible for p in curve.sample()]
        x0, y0 = min(p[0] for p in box), min(p[1] for p in box)
        x1, y1 = max(p[0] for p in box), max(p[1] for p in box)
        inside = x0 + 1e-6 < dim.base[0] < x1 - 1e-6 and y0 + 1e-6 < dim.base[1] < y1 - 1e-6
        assert not inside, f"{dim.text} 的尺寸线落在视图轮廓内部 {dim.base}"

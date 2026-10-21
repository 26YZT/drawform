"""把尺寸方案落到图纸几何上：原生 DIMENSION 实体，不是自绘的线条加文字。

理由和「孔必须是原生 CIRCLE」一样：客户拿到图要在 AutoCAD 里改，
自绘的尺寸线在 CAD 眼里只是一堆线段，改不了也量不了。
"""

import math

import ezdxf
import pytest

from drawform.dimensioning.place import place_scheme
from drawform.dimensioning.scheme import derive_scheme
from drawform.geometry.features import Bore, extract_features
from drawform.geometry.loader import import_step
from drawform.domain.curves import Circle2D, view_basis
from drawform.geometry.projection import FRONT, TOP, LEFT, project_view
from drawform.output.dxf import write_dxf
from drawform.geometry.layout import layout_views
from dataclasses import replace
from drawform.geometry.centerlines import centerlines_for


def _placed_views(model):
    features = extract_features(model.shape, model.bbox)
    projections = {d.name: project_view(model.shape, d) for d in (FRONT, TOP, LEFT)}
    placed = [replace(v, centerlines=centerlines_for(v.projection, features))
              for v in layout_views(tuple(projections.values()))]
    return projections, features, placed


def test_overall_dimensions_are_placed(bearing_seat_step):
    model = import_step(bearing_seat_step)
    projections, features, _ = _placed_views(model)
    scheme = derive_scheme(model, features)

    dims = place_scheme(scheme, projections)

    kinds = {(d.kind, d.view) for d in dims}
    assert ("linear", "front") in kinds, "总长/总高应落在主视图上"
    assert ("diameter", "top") in kinds, "孔径应落在它投影成圆的那个视图上"


def test_linear_dimension_spans_real_geometry_not_the_bounding_box_guess(bearing_seat_step):
    """尺寸界线必须钉在投影出来的真实轮廓上，不能拿包围盒数字凑。"""
    model = import_step(bearing_seat_step)
    projections, features, _ = _placed_views(model)
    scheme = derive_scheme(model, features)

    length = [d for d in place_scheme(scheme, projections)
              if d.kind == "linear" and d.text == "80"]
    assert length, "总长 80 的尺寸没落位"
    dim = length[0]
    assert abs(dim.p1[0] - dim.p2[0]) == pytest.approx(80.0, abs=1e-6)
    assert dim.p1[1] == pytest.approx(dim.p2[1], abs=1e-6), "水平尺寸的界线必须等高"


def test_diameter_dimension_lands_on_the_actual_circle(bearing_seat_step):
    model = import_step(bearing_seat_step)
    projections, features, _ = _placed_views(model)
    scheme = derive_scheme(model, features)

    diameters = [d for d in place_scheme(scheme, projections) if d.kind == "diameter"]

    assert diameters, "孔径尺寸一条都没落位"
    # ⌀40 是凸台外圆：外圆特征上线后它必须出现在图上，凸台直径不标就等于没画。
    # Ø32 改从 counterbore 自由度发来，符号必须是沉孔 ⌴ —— 方案层改对了但图上少了
    # 这一条，就是"指标变好、交付变差"。
    assert {d.text for d in diameters} == {"2×⌀9", "⌀20", "⌴⌀32", "⌀40"}
    for dim in diameters:
        span = (dim.p2[0] - dim.p1[0]) ** 2 + (dim.p2[1] - dim.p1[1]) ** 2
        assert span == pytest.approx(dim.value ** 2 / 4, abs=1e-6), \
            f"{dim.text} 的尺寸端点不在圆上"


def test_quantity_prefix_is_kept_on_the_drawing(bearing_seat_step):
    """并组出来的数量必须写在图上：4 个孔只标「⌀9」在生产里就是漏标。"""
    model = import_step(bearing_seat_step)
    projections, features, _ = _placed_views(model)
    scheme = derive_scheme(model, features)

    dims = place_scheme(scheme, projections)

    assert any(d.text.startswith("2×") for d in dims if d.kind == "diameter")


def test_dimensions_survive_into_the_dxf_as_native_entities(bearing_seat_step, tmp_path):
    """端到端：方案 → 落位 → DXF 里真的有 DIMENSION 实体，且 audit 干净。

    此前 DXF 里尺寸实体恒为 0，指标量的是一个从没画出来的数据结构。
    """
    model = import_step(bearing_seat_step)
    projections, features, placed = _placed_views(model)
    scheme = derive_scheme(model, features)
    dims = place_scheme(scheme, projections)
    out = tmp_path / "dim.dxf"

    write_dxf(placed, out, dimensions=dims)

    doc = ezdxf.readfile(str(out))
    entities = list(doc.modelspace().query("DIMENSION"))
    assert entities, "DXF 里没有原生 DIMENSION 实体"
    assert len(entities) == len(dims)
    assert len(doc.audit().errors) == 0


def test_dimension_text_matches_the_plan_values(bearing_seat_step, tmp_path):
    model = import_step(bearing_seat_step)
    projections, features, placed = _placed_views(model)
    scheme = derive_scheme(model, features)
    dims = place_scheme(scheme, projections)
    out = tmp_path / "text.dxf"

    write_dxf(placed, out, dimensions=dims)

    doc = ezdxf.readfile(str(out))
    texts = {e.dxf.text for e in doc.modelspace().query("DIMENSION")}
    assert "80" in texts
    assert any(t.startswith("⌀") for t in texts)


def test_dimension_text_is_legible_on_a_millimetre_drawing(bearing_seat_step, tmp_path):
    """ezdxf 自带 dimstyle 的字高是 0.25（英寸习惯），落到毫米图上就是 0.25 mm 的字。

    GB/T 14691 规定的字高系列是 3.5 / 5 / 7 mm。字高 0.25 的尺寸在纸上看不到，
    等于没有标注 —— 而且 DXF 审计、实体数量、文字内容全都正常，只有看纸面才发现。
    """
    model = import_step(bearing_seat_step)
    projections, features, placed = _placed_views(model)
    dims = place_scheme(derive_scheme(model, features), projections)
    out = tmp_path / "legible.dxf"

    write_dxf(placed, out, dimensions=dims)

    doc = ezdxf.readfile(str(out))
    heights = [e.dxf.char_height for block in doc.blocks
               if block.dxf.name.startswith("*D")
               for e in block if e.dxftype() == "MTEXT"]

    assert heights, "尺寸块里没有文字"
    assert min(heights) >= 3.5, f"尺寸字高 {min(heights)} mm，低于 GB/T 14691 最小字高 3.5"


def test_two_views_sharing_a_projection_direction_stay_distinct(bearing_seat_step, tmp_path):
    """剖视图 A-A 和主视图投射方向相同，但它们是两张不同的视图。

    早先 write_dxf 用方向名做键，后写的剖视图把主视图覆盖，总尺寸 80 画到了剖视图上。
    """
    from drawform.dimensioning.place import PlacedDimension
    from drawform.geometry.layout import PlacedView

    model = import_step(bearing_seat_step)
    front = project_view(model.shape, FRONT)
    views = (PlacedView(projection=front, origin=(0.0, 0.0), label="front"),
             PlacedView(projection=front, origin=(200.0, 0.0), label="A-A"))
    dims = (PlacedDimension("linear", "front", (0.0, 0.0), (80.0, 0.0),
                            (0.0, -8.0), "80", 80.0),)
    out = tmp_path / "labels.dxf"

    write_dxf(views, out, dimensions=dims)

    doc = ezdxf.readfile(str(out))
    dim = next(iter(doc.modelspace().query("DIMENSION")))

    assert dim.dxf.defpoint.x == pytest.approx(0.0, abs=1e-6), \
        "尺寸落到了 A-A 上：同名方向的视图没被区分开"


def test_concentric_diameter_dimensions_use_different_angles(bearing_seat_step):
    """同心孔的孔径尺寸若都放 45°，文字会沿同一条射线叠在一起。"""
    import math

    model = import_step(bearing_seat_step)
    projections, features, _ = _placed_views(model)
    scheme = derive_scheme(model, features)

    diams = [d for d in place_scheme(scheme, projections) if d.kind == "diameter"]

    angles = {round(math.degrees(math.atan2(d.p2[1] - d.p1[1], d.p2[0] - d.p1[0])), 1)
              for d in diams}
    assert len(angles) == len(diams), f"同心孔径尺寸角度重复：{sorted(angles)}"


def test_general_tolerance_is_stated_on_the_sheet(bearing_seat_step, tmp_path):
    """基线方案一律不指定差，全靠「标题栏一般公差覆盖」这个前提。

    那句话说不出图上，整个公差策略就是空头承诺：linter 认了，审查员不认。
    """
    model = import_step(bearing_seat_step)
    projections, features, placed = _placed_views(model)
    scheme = derive_scheme(model, features)
    dims = place_scheme(scheme, projections)
    out = tmp_path / "notes.dxf"

    write_dxf(placed, out, dimensions=dims, notes=scheme.notes)

    doc = ezdxf.readfile(str(out))
    texts = " ".join(e.dxf.text for e in doc.modelspace().query("TEXT"))

    assert "ISO 2768" in texts, f"图上没有一般公差注记，只有：{texts!r}"
    assert len(doc.audit().errors) == 0


def test_projection_method_is_stated_on_the_sheet(bearing_seat_step, tmp_path):
    """第一角/第三角不写出来，两张视图布局相同的图会被读成完全不同的零件。"""
    model = import_step(bearing_seat_step)
    projections, features, placed = _placed_views(model)
    scheme = derive_scheme(model, features)
    out = tmp_path / "proj.dxf"

    write_dxf(placed, out, dimensions=place_scheme(scheme, projections),
              notes=scheme.notes)

    doc = ezdxf.readfile(str(out))
    texts = " ".join(e.dxf.text for e in doc.modelspace().query("TEXT"))

    assert "第一角" in texts or "Projection" in texts


def _placed_depths(model, projections, features):
    scheme = derive_scheme(model, features)
    return [d for d in place_scheme(scheme, projections) if d.kind == "depth"]


def test_blind_hole_gets_a_depth_dimension_on_the_sheet(blind_hole_step):
    """`↧12` 现在只存在于方案里，图上一条都没有 —— 这是"分数在涨、纸面没变"的那条缝。"""
    model = import_step(blind_hole_step)
    projections, features, _ = _placed_views(model)

    depths = _placed_depths(model, projections, features)

    assert [d.text for d in depths] == ["↧12"]


def test_depth_is_measured_from_the_mouth_to_the_floor(blind_hole_step):
    """画出来的长度必须等于深度本身：入口端与孔底端搞反就会画成别的数。"""
    model = import_step(blind_hole_step)
    projections, features, _ = _placed_views(model)

    dim = _placed_depths(model, projections, features)[0]

    span = math.hypot(dim.p2[0] - dim.p1[0], dim.p2[1] - dim.p1[1])
    assert span == pytest.approx(12.0, abs=1e-3)


def test_depth_lands_on_the_view_where_the_axis_is_visible(blind_hole_step):
    """深度要落在"看得到孔有多深"的那个视图上，不是落在孔变成一个圆的那个视图上。"""
    model = import_step(blind_hole_step)
    projections, features, _ = _placed_views(model)

    dim = _placed_depths(model, projections, features)[0]

    direction = projections[dim.view].direction
    bore = next(f for f in features if isinstance(f, Bore))
    assert abs(sum(a * b for a, b in zip(direction.direction, bore.axis_direction))) < 1e-6, \
        f"{dim.view} 上孔的轴不平行于视图面，深度画不出来"


def test_through_hole_gets_no_depth_on_the_sheet(plate_step):
    """贯通孔既不在方案里发深度，也不该出现在图上。"""
    model = import_step(plate_step)
    projections, features, _ = _placed_views(model)

    assert _placed_depths(model, projections, features) == []


def test_depth_endpoints_sit_on_real_projected_geometry(blind_hole_step):
    """尺寸两端必须钉在投影出来的真实轮廓上，不能拿模型坐标算完了事。

    这是本模块从一开始立的规矩：包围盒与真实轮廓在圆角、斜面上不重合。
    """
    model = import_step(blind_hole_step)
    projections, features, _ = _placed_views(model)

    dim = _placed_depths(model, projections, features)[0]
    projection = projections[dim.view]
    levels = [p[1] for c in tuple(projection.visible) + tuple(projection.hidden)
              for p in c.sample()]

    for point in (dim.p1, dim.p2):
        assert min(abs(point[1] - level) for level in levels) < 1e-6, \
            f"端点 {point} 不在任何投影轮廓的高度上"


def test_counterbore_depth_is_placed_too(bearing_seat_step):
    """沉孔的 `↧6` 也要上图 —— 它现在在方案里，图上没有。"""
    model = import_step(bearing_seat_step)
    projections, features, _ = _placed_views(model)

    depths = _placed_depths(model, projections, features)

    assert [d.text for d in depths] == ["↧6"]


def _positions(model, projections):
    scheme = derive_scheme(model, extract_features(model.shape, model.bbox))
    dims = place_scheme(scheme, projections)
    return [d for d in dims if d.kind == "position"], scheme


def test_hole_positions_are_drawn_as_linear_dimensions(bearing_seat_step):
    """孔的 x/y 定位尺寸必须上图。

    实测落位层只覆盖方案的 51%：外形、直径、深度都画了，322 条定位尺寸一条没画。
    一张孔没有坐标的零件图在车间里没法开工 —— 这和之前"↧ 在方案里有、图上没有"
    是同一类缝，只是量大得多。
    """
    model = import_step(bearing_seat_step)
    projections, features, _ = _placed_views(model)

    positions, scheme = _positions(model, projections)

    planned = [c for c in scheme.plan.constraints if c.dof in ("x", "y")]
    # 共线孔位可共用一条标注，但不能少掉任何约束来源、数值或实际测量端点。
    from drawform.dimensioning.place import position_anchors
    from drawform.domain.curves import to_view

    anchors = position_anchors(scheme)
    covered = {}
    for dim in positions:
        assert len(dim.shared_sources) == len(dim.shared_witnesses) == len(dim.shared_values)
        for source, (p1, p2), value in zip((dim.source, *dim.shared_sources),
                                          ((dim.p1, dim.p2), *dim.shared_witnesses),
                                          (dim.value, *dim.shared_values)):
            assert source not in covered, f"重复覆盖定位来源 {source}"
            covered[source] = value
            expected = to_view(anchors[source[0]], *view_basis(projections[dim.view].direction))
            assert p2 == pytest.approx(expected)
            along = 0 if _view_direction_of(dim, projections) == "x" else 1
            assert p1[along] == pytest.approx(min(p[along] for p in _samples(projections[dim.view])))
            assert p1[1-along] == pytest.approx(p2[1-along])
    assert set(covered) == {(c.feature, c.dof) for c in planned}
    for constraint in planned:
        assert covered[(constraint.feature, constraint.dof)] == pytest.approx(constraint.value)


UNIT = {"x": (1.0, 0.0, 0.0), "y": (0.0, 1.0, 0.0), "z": (0.0, 0.0, 1.0)}


def _samples(projection):
    return [p for c in tuple(projection.visible) + tuple(projection.hidden)
            for p in c.sample()]


def _view_direction_of(dim, projections):
    """世界坐标 dim.axis 落在该视图的哪个方向上（"x" 水平 / "y" 竖直）。"""
    x_basis, y_basis = view_basis(projections[dim.view].direction)
    for name, basis in (("x", x_basis), ("y", y_basis)):
        if sum(a * b for a, b in zip(UNIT[dim.axis], basis)) > 0.99:
            return name
    raise AssertionError(f"{dim.view} 上量不出 {dim.axis}")


def _span(dim, projections):
    index = 0 if _view_direction_of(dim, projections) == "x" else 1
    return abs(dim.p2[index] - dim.p1[index])


def _start(dim, projections):
    index = 0 if _view_direction_of(dim, projections) == "x" else 1
    return min(dim.p1[index], dim.p2[index])


def test_position_is_measured_from_the_real_outline_not_from_the_origin(bearing_seat_step):
    """定位尺寸的起点必须是投影出来的真实轮廓那一侧（基准面），不是坐标 0。

    夹具的零件不在原点上时，"从 0 量"和"从基准面量"才会显出差别 —— 这里用的
    轴承座底板从 x=0 起、凸台孔在 (40, 25)，两条都能对上。
    """
    model = import_step(bearing_seat_step)
    projections, features, _ = _placed_views(model)

    for dim in _positions(model, projections)[0]:
        projection = projections[dim.view]
        index = 0 if _view_direction_of(dim, projections) == "x" else 1
        outline_lo = min(p[index] for p in _samples(projection))

        assert _start(dim, projections) == pytest.approx(outline_lo, abs=1e-6), \
            f"{dim.text} 没从基准面量起：起点 {_start(dim, projections)}，轮廓在 {outline_lo}"


def test_position_value_matches_what_the_plan_declared(bearing_seat_step):
    """画出来的长度必须等于**方案声明的那个值**。

    只比 `dim.value` 是空转的：那个值本身就是从同一对端点算出来的。
    要防的是端点锚错（锚到原点、锚到另一个孔）之后数值悄悄变了，
    那只有拿方案里那条原始数值来对才看得见。
    """
    model = import_step(bearing_seat_step)
    projections, features, _ = _placed_views(model)

    positions, scheme = _positions(model, projections)
    declared = {(c.feature, c.dof): c.value for c in scheme.plan.constraints}

    assert positions, "一条定位尺寸都没落位"
    for dim in positions:
        assert _span(dim, projections) == pytest.approx(declared[dim.source], abs=1e-6), \
            f"{dim.source} 方案要 {declared[dim.source]}，画成 {_span(dim, projections)}"


def test_radial_hole_positions_land_on_the_view_where_the_axis_is_visible(cross_bore_step):
    """沿 X 的径向孔：它的定位在 Y/Z 上，应落在能看见那两个坐标的视图上。"""
    model = import_step(cross_bore_step)
    projections, features, _ = _placed_views(model)

    positions, _ = _positions(model, projections)

    assert positions, "径向孔的定位尺寸一条都没落位"
    for dim in positions:
        unit = {"x": (1.0, 0.0, 0.0), "y": (0.0, 1.0, 0.0), "z": (0.0, 0.0, 1.0)}[dim.axis]
        x_basis, y_basis = view_basis(projections[dim.view].direction)
        assert any(abs(sum(a * b for a, b in zip(unit, basis))) > 0.99
                   for basis in (x_basis, y_basis)), \
            f"{dim.view} 上量不出 {dim.axis} 这个坐标"


def _bolt_circle_dims(model):
    projections = {d.name: project_view(model.shape, d) for d in (FRONT, TOP, LEFT)}
    scheme = derive_scheme(model, extract_features(model.shape, model.bbox))
    return place_scheme(scheme, projections), scheme, projections


def test_bolt_circle_center_is_placed_in_both_coordinates(bolt_circle_step):
    """圆心定位尺寸必须上图。方案发了 2 条（x/y），图上就得有 2 条。"""
    model = import_step(bolt_circle_step)
    placed, scheme, projections = _bolt_circle_dims(model)

    planned = [c for c in scheme.plan.constraints
               if c.feature.endswith(":center") and c.dof in ("x", "y")]
    drawn = [d for d in placed if d.kind == "position" and d.source[0].endswith(":center")]

    assert len(planned) == 2
    assert len(drawn) == len(planned), f"圆心定位 {len(planned)} 条，图上只有 {len(drawn)}"
    # 只数条数是空转的：拿第一个孔的位置冒充圆心，条数一样、数值全错
    declared = {(c.feature, c.dof): c.value for c in scheme.plan.constraints}
    for dim in drawn:
        assert _span(dim, projections) == pytest.approx(declared[dim.source], abs=1e-6), \
            f"圆心 {dim.source} 方案要 {declared[dim.source]}，画成 {_span(dim, projections)}"


def test_pcd_is_dimensioned_between_two_real_opposite_hole_centers(bolt_circle_step):
    """分布圆是假想圆，投影里根本没有那个半径的圆可钉 —— 只能钉在两个对角孔的实圆心上。"""
    model = import_step(bolt_circle_step)
    placed, scheme, _ = _bolt_circle_dims(model)

    pcd = [d for d in placed if d.kind == "pcd"]

    assert len(pcd) == 1, "方案发了 PCD，图上一条都没有"
    assert pcd[0].text == "⌀50 PCD"
    span = math.hypot(pcd[0].p2[0] - pcd[0].p1[0], pcd[0].p2[1] - pcd[0].p1[1])
    assert span == pytest.approx(50.0, abs=1e-3), f"PCD 画成 {span}"


def test_pcd_endpoints_are_two_projected_hole_centers(bolt_circle_step):
    """端点必须真的是两个孔的投影圆心，而不是拿 50 这个数凑出来的一条线。"""
    model = import_step(bolt_circle_step)
    placed, _scheme, projections = _bolt_circle_dims(model)

    pcd = [d for d in placed if d.kind == "pcd"][0]
    centers = [c.center for c in projections[pcd.view].visible if isinstance(c, Circle2D)]

    for point in (pcd.p1, pcd.p2):
        assert min(math.hypot(point[0] - cx, point[1] - cy) for cx, cy in centers) < 1e-6, \
            f"PCD 端点 {point} 不是任何孔的投影圆心"


def test_an_odd_bolt_circle_still_gets_a_pcd_whose_line_measures_the_pcd(odd_bolt_circle_step):
    """5 个孔没有直径相对的一对，但 ⌀50 PCD 照样要标。

    原先直接跳过 → 方案发了、图上没画 → `unplaced_dimensions` 把整张图拒交
    （实测语料 65 件里 2 件因此完全出不了图）。现在两端落在穿过第一个孔的
    径向上、与分布圆的两个交点：量出来仍然是 50，不是最远那一对的 40.45。
    """
    model = import_step(odd_bolt_circle_step)
    placed, scheme, projections = _bolt_circle_dims(model)

    planned = [c for c in scheme.plan.constraints if c.dof == "pcd"]
    drawn = [d for d in placed if d.kind == "pcd"]

    assert len(planned) == 1, "方案该认出这个螺栓圆"
    assert len(drawn) == 1, f"PCD 方案发了却没落位：{[c.feature for c in planned]}"
    assert drawn[0].text == "⌀50 PCD"
    assert math.dist(drawn[0].p1, drawn[0].p2) == pytest.approx(50.0, abs=1e-6)

    # 两端必须是分布圆上的点：圆心是各孔圆心的平均，半径 25。
    holes = [c.center for c in projections[drawn[0].view].visible if isinstance(c, Circle2D)]
    center = (sum(p[0] for p in holes) / len(holes), sum(p[1] for p in holes) / len(holes))
    for point in (drawn[0].p1, drawn[0].p2):
        assert math.dist(point, center) == pytest.approx(25.0, abs=1e-6), \
            f"PCD 端点 {point} 不在分布圆上"


def test_a_diameter_whose_projection_is_only_an_arc_still_gets_placed(edge_notch_step):
    """投影里只有圆弧（孔被边界切掉一半）时，直径照样要标出来。

    `_find_circle` 原先只认整圆，于是这类尺寸在方案里有、图上没有 —— 实测语料
    35 条没落位的直径里 33 条是这个原因（不是斜孔，斜轴只有 2 条）。
    """
    model = import_step(edge_notch_step)
    projections = {d.name: project_view(model.shape, d) for d in (FRONT, TOP, LEFT)}
    scheme = derive_scheme(model, extract_features(model.shape, model.bbox))

    dims = place_scheme(scheme, projections)
    planned = [c for c in scheme.plan.constraints if c.dof == "diameter"]
    drawn = [d for d in dims if d.kind == "diameter"]

    assert planned, "这个缺口该被认成一个 Ø10 的特征"
    assert len(drawn) == len(planned), f"方案 {len(planned)} 条直径，图上只有 {len(drawn)}"
    assert drawn[0].text == "⌀10"


def test_outline_and_position_dimensions_do_not_share_an_offset_lane(bearing_seat_step):
    """外形总尺寸与孔位尺寸不能挤在同一条偏移带上。

    目视渲染发现的缺陷：俯视图左侧 `50`（宽度）与 `25`（孔位）文字互相压字。
    此前看不到，是因为根本没有定位尺寸 —— 落位层补上孔位之后，这条缝才显形。
    """
    model = import_step(bearing_seat_step)
    projections, features, _ = _placed_views(model)
    dims = place_scheme(derive_scheme(model, features), projections)

    top = [d for d in dims if d.view == "top" and d.base is not None]
    # 水平尺寸（尺寸线是横线）按 base 的 y 分带；竖直尺寸按 base 的 x 分带。
    horizontal = [round(d.base[1], 3) for d in top
                  if d.p1[1] == pytest.approx(d.p2[1], abs=1e-6)]
    vertical = [round(d.base[0], 3) for d in top
                if d.p1[0] == pytest.approx(d.p2[0], abs=1e-6)]

    assert len(set(horizontal)) == len(horizontal), f"水平尺寸挤在同一条带上：{sorted(horizontal)}"
    assert len(set(vertical)) == len(vertical), f"竖直尺寸挤在同一条带上：{sorted(vertical)}"

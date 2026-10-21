"""重复特征合并成带数量的组：4 个一样的孔是一条 `4×⌀9`，不是 4 条直径。"""

import pytest

from drawform.dimensioning.scheme import derive_scheme
from drawform.geometry.features import Bore, extract_features, group_bores
from drawform.geometry.loader import import_step


def _bore(fid, diameter, depth, axis=(0.0, 0.0, 1.0), point=(0.0, 0.0, 0.0),
          through=False, mouth=0.0):
    # mouth 默认给一个值：这些合成用例测的是"盲孔要发深度"，入口面是前提条件。
    return Bore(feature_id=fid, diameter=diameter, axis_point=point,
                axis_direction=axis, depth=depth, through=through, mouth=mouth)


def test_identical_bores_collapse_into_one_group_with_a_count():
    bores = [_bore(f"b{i}", 9.0, 12.0, point=(10.0 * i, 25.0, 0.0)) for i in range(4)]

    groups = group_bores(bores)

    assert len(groups) == 1
    assert groups[0].count == 4
    assert groups[0].diameter == pytest.approx(9.0)
    assert groups[0].depth == pytest.approx(12.0)


def test_different_diameter_is_a_different_group():
    groups = group_bores([_bore("a", 9.0, 12.0), _bore("b", 11.0, 12.0)])
    assert len(groups) == 2


def test_same_diameter_different_depth_is_a_different_group():
    """Ø9 通孔和 Ø9 盲孔是两条不同的标注，不能并成 `2×⌀9`。"""
    groups = group_bores([_bore("a", 9.0, 12.0), _bore("b", 9.0, 20.0)])
    assert len(groups) == 2


def test_same_size_different_through_is_a_different_group():
    """同直径同深度、一个通一个不通：通孔不给深度、盲孔要给，两套标注不一样，不能并组。"""
    groups = group_bores([
        _bore("a", 9.0, 12.0, point=(0.0, 0.0, 0.0), through=True),
        _bore("b", 9.0, 12.0, point=(20.0, 0.0, 0.0), through=False),
    ])

    assert len(groups) == 2


def test_identical_counterbores_collapse_into_one_count():
    """两个一样的沉孔写成 `2×⌀4,5 ⌴⌀6 ↧2`，不是两套重复标注（语料里就这么写）。"""
    from drawform.geometry.features import Counterbore, group_counterbores

    pairs = [Counterbore(cbore_id=f"c{i}", pilot_id=f"p{i}", diameter=6.0, depth=2.0)
             for i in range(2)]

    groups = group_counterbores(pairs)

    assert len(groups) == 1
    assert groups[0].count == 2
    assert groups[0].diameter == pytest.approx(6.0)


def test_counterbores_of_different_depth_are_different_groups():
    from drawform.geometry.features import Counterbore, group_counterbores

    groups = group_counterbores([
        Counterbore(cbore_id="a", pilot_id="p1", diameter=6.0, depth=2.0),
        Counterbore(cbore_id="b", pilot_id="p2", diameter=6.0, depth=3.0),
    ])

    assert len(groups) == 2


def test_through_group_is_annotated_without_depth():
    """真实图纸对贯通孔只给直径。65 个真实零件实测：照深度全发是 118 条，
    真图纸只有 37 条 ↧ —— 通孔不发深度是把这一项拉回量级的关键。
    """
    bores = [_bore(f"b{i}", 9.0, 12.0, point=(10.0 * i, 25.0, 0.0), through=True)
             for i in range(4)]
    model = _Plate(bbox=(60.0, 50.0, 12.0), origin=(0.0, 0.0, 0.0))

    scheme = derive_scheme(model, bores)

    assert [c for c in scheme.plan.constraints if c.dof == "depth"] == []
    assert [c for c in scheme.plan.constraints if c.dof == "diameter"][0].quantity == 4


def test_blind_group_still_gets_its_depth():
    bores = [_bore("b0", 9.0, 12.0, point=(10.0, 25.0, 0.0), through=False)]
    model = _Plate(bbox=(60.0, 50.0, 12.0), origin=(0.0, 0.0, 0.0))

    scheme = derive_scheme(model, bores)

    depths = [c for c in scheme.plan.constraints if c.dof == "depth"]
    assert len(depths) == 1
    assert depths[0].value == pytest.approx(12.0)


def test_counterbore_above_a_through_pilot_keeps_the_step_depth():
    """Ø32 沉孔站在 Ø20 通孔上：通孔不发深度，沉孔那一级必须发 ↧6。"""
    bores = [_bore("pilot", 20.0, 46.0, point=(40.0, 25.0, 0.0), through=True),
             _bore("cbore", 32.0, 6.0, point=(40.0, 25.0, 46.0))]
    model = _Plate(bbox=(80.0, 50.0, 52.0), origin=(0.0, 0.0, 0.0))

    scheme = derive_scheme(model, bores)

    depths = [c for c in scheme.plan.constraints if c.dof == "depth"]
    assert len(depths) == 1
    assert depths[0].feature == "group-cbore"
    assert depths[0].value == pytest.approx(6.0)


def test_same_size_different_axis_is_a_different_group():
    """轴向不同的同尺寸孔，定位自由度都不一样，不能并组。"""
    groups = group_bores([
        _bore("a", 9.0, 12.0, axis=(0.0, 0.0, 1.0)),
        _bore("b", 9.0, 12.0, axis=(1.0, 0.0, 0.0)),
    ])
    assert len(groups) == 2



def test_group_emits_one_diameter_and_one_depth_for_all_members():
    bores = [_bore(f"b{i}", 9.0, 12.0, point=(10.0 * i, 25.0, 0.0)) for i in range(4)]
    model = _Plate(bbox=(60.0, 50.0, 12.0), origin=(0.0, 0.0, 0.0))

    scheme = derive_scheme(model, bores)

    diameters = [c for c in scheme.plan.constraints if c.dof == "diameter"]
    depths = [c for c in scheme.plan.constraints if c.dof == "depth"]
    assert len(diameters) == 1
    assert diameters[0].quantity == 4
    assert len(depths) == 1


def test_positions_are_still_given_per_member():
    """并组只合并「同样的量」；每个孔的位置仍然各不相同，必须逐个钉住。"""
    bores = [_bore(f"b{i}", 9.0, 12.0, point=(10.0 * i, 25.0, 0.0)) for i in range(4)]
    model = _Plate(bbox=(60.0, 50.0, 12.0), origin=(0.0, 0.0, 0.0))

    scheme = derive_scheme(model, bores)

    xs = [c for c in scheme.plan.constraints if c.dof == "x"]
    assert len(xs) == 4
    assert sorted(c.value for c in xs) == pytest.approx([0.0, 10.0, 20.0, 30.0])


def test_grouped_scheme_is_still_legal():
    from drawform.dimensioning.constraints import check_plan

    bores = [_bore(f"b{i}", 9.0, 12.0, point=(10.0 * i, 25.0, 0.0)) for i in range(4)]
    model = _Plate(bbox=(60.0, 50.0, 12.0), origin=(0.0, 0.0, 0.0))

    scheme = derive_scheme(model, bores)

    assert check_plan(scheme.plan, scheme.unknowns) == ()


def test_bearing_seat_bolt_holes_group(bearing_seat_step):
    """轴承座两个 Ø9 螺栓孔同尺寸同深同轴，应并成一条 2×⌀9。"""
    model = import_step(bearing_seat_step)
    features = extract_features(model.shape, model.bbox)

    groups = group_bores([f for f in features if isinstance(f, Bore)])

    nine = [g for g in groups if abs(g.diameter - 9.0) < 1e-6]
    assert len(nine) == 1 and nine[0].count == 2


from dataclasses import dataclass  # noqa: E402  （测试用的假模型，只为喂 derive_scheme）


@dataclass(frozen=True)
class _Plate:
    bbox: tuple
    origin: tuple


def _ring(count, radius=25.0, center=(40.0, 30.0), diameter=9.0):
    import math
    return [_bore(f"p{i}", diameter, 12.0,
                  point=(center[0] + radius * math.cos(2 * math.pi * i / count),
                         center[1] + radius * math.sin(2 * math.pi * i / count), 0.0))
            for i in range(count)]


def test_equally_spaced_holes_become_one_bolt_circle():
    """4 个等分孔：真实图纸是一条 PCD 标注，不是 8 个坐标。"""
    bores = _ring(4)
    model = _Plate(bbox=(80.0, 60.0, 12.0), origin=(0.0, 0.0, 0.0))

    scheme = derive_scheme(model, bores)

    pcd = [c for c in scheme.plan.constraints if c.dof == "pcd"]
    assert len(pcd) == 1
    assert pcd[0].value == pytest.approx(50.0)
    assert pcd[0].quantity == 4
    assert [c for c in scheme.plan.constraints if c.dof in ("x", "y")
            and c.feature.startswith("p")] == []


def test_bolt_circle_center_is_still_located():
    """螺栓圆整体相对基准的位置仍要标，否则孔系在板上放哪没定。"""
    bores = _ring(4)
    model = _Plate(bbox=(80.0, 60.0, 12.0), origin=(0.0, 0.0, 0.0))

    scheme = derive_scheme(model, bores)
    center = [c for c in scheme.plan.constraints
              if c.feature.endswith(":center") and c.dof in ("x", "y")]

    assert len(center) == 2
    assert sorted(c.value for c in center) == pytest.approx([30.0, 40.0])


def test_two_holes_are_not_a_bolt_circle():
    """两孔定不出「等分」，真实图纸也是逐个给坐标，不能凑成 PCD。"""
    bores = _ring(2)
    model = _Plate(bbox=(80.0, 60.0, 12.0), origin=(0.0, 0.0, 0.0))

    scheme = derive_scheme(model, bores)

    assert [c for c in scheme.plan.constraints if c.dof == "pcd"] == []
    assert len([c for c in scheme.plan.constraints if c.dof in ("x", "y")]) == 4


def test_unequal_angular_spacing_is_not_a_bolt_circle():
    import math
    angles = (0.0, math.pi / 2, math.pi, 3 * math.pi / 4)
    bores = [_bore(f"u{i}", 9.0, 12.0, point=(40.0 + 25.0 * math.cos(a),
                                              30.0 + 25.0 * math.sin(a), 0.0))
             for i, a in enumerate(angles)]
    model = _Plate(bbox=(80.0, 60.0, 12.0), origin=(0.0, 0.0, 0.0))

    scheme = derive_scheme(model, bores)

    assert [c for c in scheme.plan.constraints if c.dof == "pcd"] == []


def test_rectangular_pattern_is_not_a_bolt_circle():
    bores = [_bore(f"r{i}", 9.0, 12.0, point=p) for i, p in
             enumerate([(20.0, 20.0, 0.0), (60.0, 20.0, 0.0),
                        (60.0, 40.0, 0.0), (20.0, 40.0, 0.0)])]
    model = _Plate(bbox=(80.0, 60.0, 12.0), origin=(0.0, 0.0, 0.0))

    scheme = derive_scheme(model, bores)

    assert [c for c in scheme.plan.constraints if c.dof == "pcd"] == []


def test_bolt_circle_scheme_is_still_legal():
    from drawform.dimensioning.constraints import check_plan

    bores = _ring(6)
    model = _Plate(bbox=(80.0, 60.0, 12.0), origin=(0.0, 0.0, 0.0))

    scheme = derive_scheme(model, bores)

    assert check_plan(scheme.plan, scheme.unknowns) == ()


def test_equiangular_but_unequal_radius_is_not_a_bolt_circle():
    """菱形四角：从重心看角度严格等分（0/90/180/270），但半径是 30/20/30/20。

    只有共圆判据能挡下它 —— 三点版本挡不住，因为半径不等会让重心偏移，
    角度跟着不再等分，最后否掉它的是角度判据，共圆判据等于没被测到。
    """
    bores = [_bore(f"d{i}", 9.0, 12.0, point=p) for i, p in enumerate(
        [(70.0, 30.0, 0.0), (40.0, 50.0, 0.0), (10.0, 30.0, 0.0), (40.0, 10.0, 0.0)])]
    model = _Plate(bbox=(80.0, 60.0, 12.0), origin=(0.0, 0.0, 0.0))

    scheme = derive_scheme(model, bores)

    assert [c for c in scheme.plan.constraints if c.dof == "pcd"] == []

import pytest

from drawform.geometry.features import (Bore, Shaft, extract_features, find_counterbores,
                                        group_shafts)
from drawform.geometry.loader import import_step


def test_single_through_hole_is_detected_as_one_bore(plate_step):
    model = import_step(plate_step)

    features = extract_features(model.shape, model.bbox)

    bores = [f for f in features if isinstance(f, Bore)]
    assert len(bores) == 1
    assert bores[0].diameter == pytest.approx(20.0, abs=1e-6)
    assert bores[0].axis_point[0:2] == pytest.approx((40.0, 25.0), abs=1e-6)


def test_bore_carries_a_stable_id_usable_by_dimensioning(plate_step):
    """特征 ID 是标注的唯一事实源引用，必须稳定且可重复。"""
    model = import_step(plate_step)

    first = extract_features(model.shape, model.bbox)
    second = extract_features(model.shape, model.bbox)

    assert [f.feature_id for f in first] == [f.feature_id for f in second]
    assert len({f.feature_id for f in first}) == len(first)


def test_outer_cylinders_are_not_mistaken_for_bores(stepped_shaft_step):
    """Ø44 与 Ø30 外圆 + Ø16 通孔：外圆柱面不是孔，不能被当成 Bore。"""
    model = import_step(stepped_shaft_step)

    features = extract_features(model.shape, model.bbox)

    bores = [f for f in features if isinstance(f, Bore)]
    assert len(bores) == 1
    assert bores[0].diameter == pytest.approx(16.0, abs=1e-6)


def test_bore_depth_is_reported(stepped_shaft_step):
    model = import_step(stepped_shaft_step)

    bore = next(f for f in extract_features(model.shape, model.bbox) if isinstance(f, Bore))

    assert bore.depth == pytest.approx(90.0, abs=1e-3)


def test_bore_depth_is_material_thickness_not_cutter_length(plate_step):
    """通孔深度必须是实体厚度 12，而不是布尔运算时那根切除圆柱的长度 14。

    切除体总是两端出头，直接拿圆柱参数域长度当深度会把图画错。
    贯通孔虽然不标深度，这个值仍然参与判据（eps 由它推出），不能错。
    """
    model = import_step(plate_step)

    bore = _bores_by_diameter(model.shape, model.bbox)[20.0]

    assert bore.depth == pytest.approx(12.0, abs=1e-3), "深度取成了切除刀具长度"


def test_radial_bore_keeps_the_coordinates_perpendicular_to_its_axis(cross_bore_step):
    """沿 X 的径向孔：必须给出 Y/Z 这两个垂直于轴线的坐标。

    只存 (X, Y) 的话，X 在轴线上本来就是任取的（等于没给），Z 则彻底丢了，
    定位尺寸无从算起。
    """
    from drawform.geometry.features import extract_features
    from drawform.geometry.loader import import_step

    model = import_step(cross_bore_step)

    bore = extract_features(model.shape, model.bbox)[0]

    assert bore.axis_direction == pytest.approx((1.0, 0.0, 0.0), abs=1e-6)
    assert bore.axis_point[1:] == pytest.approx((10.0, 10.0), abs=1e-6)


def _bores_by_diameter(shape, bbox):
    return {round(b.diameter, 3): b for b in extract_features(shape, bbox) if isinstance(b, Bore)}


def test_blind_bore_records_which_end_the_tool_enters_from(blind_hole_step):
    """盲孔要知道"开口在哪一端"，否则深度画到图上会不知道从哪条线量起。

    夹具是从顶面 (z=20) 往下钻 12，所以口在 20、底在 8。区间 (8,20) 本身
    分不出上下 —— 只有开口端能定方向。
    """
    model = import_step(blind_hole_step)

    bore = _bores_by_diameter(model.shape, model.bbox)[10.0]

    assert bore.axis_span == pytest.approx((8.0, 20.0), abs=1e-3)
    assert bore.mouth == pytest.approx(20.0, abs=1e-3)


def test_through_bore_has_no_single_mouth(plate_step):
    """贯通孔两端都开口，没有"入口"可言，返回 None 而不是随便挑一端。"""
    model = import_step(plate_step)

    assert _bores_by_diameter(model.shape, model.bbox)[20.0].mouth is None


def test_counterbore_mouth_is_at_the_boss_top(bearing_seat_step):
    """Ø32 沉孔的口在凸台顶面 z=52，底在 z=46。"""
    model = import_step(bearing_seat_step)

    bore = _bores_by_diameter(model.shape, model.bbox)[32.0]

    assert bore.mouth == pytest.approx(52.0, abs=1e-3)


def test_through_bore_is_flagged_through(plate_step):
    """打通的孔在真实图纸上只标直径，不标深度 —— 深不深由剖视/侧视自己说明。"""
    model = import_step(plate_step)

    bore = _bores_by_diameter(model.shape, model.bbox)[20.0]

    assert bore.through is True


def test_blind_bore_is_not_flagged_through(blind_hole_step):
    model = import_step(blind_hole_step)

    bore = _bores_by_diameter(model.shape, model.bbox)[10.0]

    assert bore.through is False
    assert bore.depth == pytest.approx(12.0, abs=1e-3)


def test_radial_through_bore_is_flagged_through(cross_bore_step):
    """径向贯穿：判据不能只认 Z 轴方向的孔。"""
    model = import_step(cross_bore_step)

    assert _bores_by_diameter(model.shape, model.bbox)[8.0].through is True


def test_counterbore_keeps_its_depth_while_the_pilot_hole_is_through(bearing_seat_step):
    """Ø20 贯通、Ø32 沉孔只沉到一半 —— 沉孔那一级的深度必须照标。

    真实图纸写 `⌀20 ⌴⌀32 ↧6`：⌀20 不给深度，⌀32 给。判据若是「轴向延长线上
    没有材料就算通」，⌀32 会因为脚下站着 ⌀20 的空腔而被误判成通孔，把这条
    ↧6 弄丢。所以穿透检查要打在孔自己的环带上，不是打在轴线上。
    """
    model = import_step(bearing_seat_step)

    bores = _bores_by_diameter(model.shape, model.bbox)

    assert bores[20.0].through is True
    assert bores[32.0].through is False
    assert bores[9.0].through is True


def _shafts_by_diameter(shape, bbox):
    return {round(s.diameter, 3): s for s in extract_features(shape, bbox) if isinstance(s, Shaft)}


def test_outer_cylinders_are_extracted_as_shafts(stepped_shaft_step):
    """Ø44/Ø30 阶梯轴的两个外圆面必须成为可标注特征。

    实测 65 个真实零件：只认内圆柱面时直径池 79/149 命中，把外圆补上能到 131 ——
    回转体零件的大部分 ⌀ 标的是外圆，不是孔。
    """
    model = import_step(stepped_shaft_step)

    shafts = _shafts_by_diameter(model.shape, model.bbox)

    assert sorted(shafts) == [30.0, 44.0]
    assert shafts[44.0].length == pytest.approx(30.0, abs=1e-3)


def test_shaft_and_bore_do_not_get_mixed_up(stepped_shaft_step):
    """同一根轴上 Ø16 是孔、Ø30/Ø44 是外圆，两侧不能互串。"""
    model = import_step(stepped_shaft_step)

    features = extract_features(model.shape, model.bbox)

    assert {round(b.diameter, 3) for b in features if isinstance(b, Bore)} == {16.0}
    assert {round(s.diameter, 3) for s in features if isinstance(s, Shaft)} == {30.0, 44.0}


def test_plate_with_only_a_hole_has_no_shafts(plate_step):
    model = import_step(plate_step)

    assert [s for s in extract_features(model.shape, model.bbox) if isinstance(s, Shaft)] == []


def test_split_faces_of_one_cylinder_do_not_become_two_dimensions(double_journal_step):
    """Ø30 被中间台阶隔成两段：图上 ⌀30 只写一次，不写 2×⌀30。"""
    model = import_step(double_journal_step)

    groups = group_shafts([s for s in extract_features(model.shape, model.bbox) if isinstance(s, Shaft)])

    assert sorted(round(g.diameter, 3) for g in groups) == [30.0, 50.0]
    thirty = next(g for g in groups if abs(g.diameter - 30.0) < 1e-6)
    assert thirty.quantity == 1


def _bores_of(model):
    return [f for f in extract_features(model.shape, model.bbox) if isinstance(f, Bore)]


def test_a_larger_bore_sitting_on_a_smaller_one_is_a_counterbore(bearing_seat_step):
    """Ø32 只沉 6 深、底下站着 Ø20 通孔 —— 这就是真实图纸的 `⌀20 ⌴⌀32 ↧6`。

    沉孔的 ⌀32 必须被认成**沉孔直径**而不是普通直径：真实标注把 ⌴ 的值放在沉孔
    这一池，我们却发进直径池，结果沉孔池 0 产出、直径池凭空多出一堆超发。
    """
    model = import_step(bearing_seat_step)

    cbore = find_counterbores(_bores_of(model))

    assert len(cbore) == 1
    assert round(cbore[0].diameter, 3) == 32.0
    assert round(cbore[0].depth, 3) == pytest.approx(6.0, abs=1e-2)
    pilot = next(b for b in _bores_of(model) if abs(b.diameter - 20.0) < 1e-6)
    assert cbore[0].pilot_id == pilot.feature_id


def test_counterbore_pairs_only_on_the_same_axis(bearing_seat_step):
    """两个 Ø9 螺栓孔和 Ø32 沉孔直径不同也不共轴，不能被配成沉孔。"""
    model = import_step(bearing_seat_step)

    bores = _bores_of(model)
    cbore = find_counterbores(bores)

    nine_ids = {b.feature_id for b in bores if abs(b.diameter - 9.0) < 1e-6}
    assert nine_ids.isdisjoint({c.pilot_id for c in cbore})
    assert nine_ids.isdisjoint({c.cbore_id for c in cbore})


def test_a_pilot_that_is_blind_too_still_counts_as_a_counterbore(meeting_cbore_step):
    """`⌀4 ↧6 ⌴⌀6.5 ↧4`：两面钻进来的孔刚好碰头，引导孔自己也是盲孔。

    配对判据不能要求引导孔必须打通 —— 真实语料里这种"刚好碰面"的写法很常见。
    """
    model = import_step(meeting_cbore_step)

    cbore = find_counterbores(_bores_of(model))

    assert [round(c.diameter, 3) for c in cbore] == [6.5]
    assert round(cbore[0].depth, 3) == pytest.approx(4.0, abs=1e-2)


def test_a_stepped_blind_hole_is_not_a_counterbore(blind_hole_step):
    """只有一个孔，没有可配的大孔，不能凭空产生沉孔。"""
    model = import_step(blind_hole_step)

    assert find_counterbores(_bores_of(model)) == ()


def test_same_span_but_different_axis_is_not_a_counterbore(cbore_traps_step):
    """两个盲孔轴向区间完全一样，但轴线不同 —— 那是两个孔，不是沉孔。

    挡住它的只能是同轴判据：相接、大小关系这两条在这里全都满足。
    """
    model = import_step(cbore_traps_step)

    bores = _bores_of(model)
    pairs = find_counterbores(bores)

    a_big = next(b for b in bores if abs(b.diameter - 20.0) < 1e-6
                 and abs(b.axis_point[1] - 5.0) < 1e-6)
    assert a_big.feature_id not in {c.cbore_id for c in pairs}


def test_coaxial_holes_drilled_from_opposite_faces_are_not_a_counterbore(cbore_traps_step):
    """共轴但中间隔 4 mm 实腹的两端对钻孔：大孔不是沉孔，得各自给深度。

    挡住它的只能是端面相接判据 —— 同轴、大小关系在这里都成立。
    """
    model = import_step(cbore_traps_step)

    bores = _bores_of(model)
    pairs = find_counterbores(bores)

    b_big = next(b for b in bores if abs(b.diameter - 20.0) < 1e-6
                 and abs(b.axis_point[0] - 5.0) < 1e-6)
    assert b_big.feature_id not in {c.cbore_id for c in pairs}



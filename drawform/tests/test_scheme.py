"""基线尺寸方案生成：把模型里客观可量的量全部钉住，并要求它必然过 linter。

可推与不可推的界线：
- 尺寸「数值」全部可从模型量出来，是客观事实；
- 「标哪些、从哪个基准量、给什么公差」是设计意图，推不出来。
所以本模块只做一件站得住的事：按一条写死的策略产出一套完整且合法的基线方案，
用它去和真实图纸的数值对分（见 eval/score.py），而不是假装自己懂意图。
"""

import pytest

from drawform.dimensioning.constraints import (
    NO_DATUM, NO_TOLERANCE_HOME, UNDER_CONSTRAINED, check_plan,
)
from drawform.dimensioning.scheme import derive_scheme
from drawform.geometry.features import Bore, extract_features
from drawform.geometry.loader import import_step


def _scheme(plate_step):
    model = import_step(plate_step)
    return model, derive_scheme(model, extract_features(model.shape, model.bbox))


def test_outline_dimensions_equal_the_measured_bbox(plate_step):
    model, scheme = _scheme(plate_step)

    values = {(c.feature, c.dof): c.value
              for c in scheme.plan.constraints if c.feature == "outline"}

    assert values[("outline", "length")] == pytest.approx(model.bbox[0], abs=1e-6)
    assert values[("outline", "width")] == pytest.approx(model.bbox[1], abs=1e-6)
    assert values[("outline", "height")] == pytest.approx(model.bbox[2], abs=1e-6)


def test_a_small_but_real_position_is_still_dimensioned(plate_step):
    """阈值只切浮点噪声，不是切小尺寸。

    0.5 mm 的偏心是正当尺寸（真实语料里 0.5 mm 的壁厚有 4 件）。把阈值放大到 1 mm
    会把它静默吞掉 —— 变异测试就是这么试出来的，所以这里钉住上界。
    """
    model = import_step(plate_step)
    bore = Bore("bore-near", 20.0, (40.0, 0.5, 0.0), (0.0, 0.0, 1.0), 12.0,
                through=True)

    scheme = derive_scheme(model, [bore])

    offsets = [c.value for c in scheme.plan.constraints if c.dof == "y"]
    assert offsets == [pytest.approx(0.5)], "该发的尺寸一条都不能悄悄少"


def test_bore_diameter_comes_from_the_detected_feature(plate_step):
    model, scheme = _scheme(plate_step)

    diameters = [c.value for c in scheme.plan.constraints
                 if c.dof == "diameter"]

    assert diameters == [pytest.approx(20.0)]


def test_a_noisy_position_is_snapped_to_zero_but_stays_on_the_sheet(plate_step):
    """OCCT 把"孔轴正好落在基准面上"算成 1e-07，图纸上就出现一个谁也不该读的数字。

    修法是**归零并保留这条尺寸**。第一版我把它删了，被 `test_grouping.py` 里那条
    "并组不吞位置"打回来 —— 删掉之后这个孔在该方向上再没有任何定位依据，
    而"0"表达的正是与基准面共面这件事。（真实语料：part_0011 的 1e-07、
    part_0056 的 5.8e-12。）
    """
    model = import_step(plate_step)
    bore = Bore("bore-edge", 20.0, (40.0, 1e-07, 0.0), (0.0, 0.0, 1.0), 12.0,
                through=True)

    scheme = derive_scheme(model, [bore])

    positions = {(c.feature, c.dof): c.value for c in scheme.plan.constraints
                 if c.dof in ("x", "y", "z")}
    assert positions[(bore.feature_id, "y")] == 0.0, "要归零，不是留着 1e-07"
    assert (bore.feature_id, "y") in scheme.unknowns, "尺寸与未知量必须成对存在"
    assert check_plan(scheme.plan, scheme.unknowns) == ()


def test_through_bore_gets_no_depth_dimension(plate_step):
    """Ø20 打通 12 厚的板：真实图纸只写 ⌀20，不写深度。"""
    model, scheme = _scheme(plate_step)

    depths = [c.value for c in scheme.plan.constraints if c.dof == "depth"]

    assert depths == []


def test_outer_diameters_are_annotated(stepped_shaft_step):
    """阶梯轴的两个外圆 ⌀44、⌀30 必须进方案 —— 真实图纸上回转体的多数 ⌀ 标的是外圆。"""
    model = import_step(stepped_shaft_step)
    scheme = derive_scheme(model, extract_features(model.shape, model.bbox))

    diameters = sorted(c.value for c in scheme.plan.constraints if c.dof == "diameter")

    assert diameters == pytest.approx([16.0, 30.0, 44.0])


def test_scheme_with_shafts_is_still_legal(stepped_shaft_step):
    """加了外圆特征后仍然要过 linter：每个钉住的量都在 unknowns 里，且不欠定。"""
    model = import_step(stepped_shaft_step)
    scheme = derive_scheme(model, extract_features(model.shape, model.bbox))

    assert check_plan(scheme.plan, scheme.unknowns) == ()


def _values(scheme, dof):
    return sorted(c.value for c in scheme.plan.constraints if c.dof == dof)


def test_counterbore_diameter_is_not_emitted_as_a_plain_diameter(bearing_seat_step):
    """Ø32 是沉孔直径，真实图纸写作 `⌴⌀32` 并放在沉孔一池。

    发进直径池的后果（实测）：沉孔池 19 条我们 0 产出，直径池反倒超发 ——
    同一个数值放错池，两边都是错。Ø40 是凸台**外圆**，它本来就该留在直径池。
    """
    model = import_step(bearing_seat_step)
    scheme = derive_scheme(model, extract_features(model.shape, model.bbox))

    assert _values(scheme, "diameter") == pytest.approx([9.0, 20.0, 40.0])
    assert _values(scheme, "counterbore") == pytest.approx([32.0])


def test_counterbore_still_carries_its_own_depth(bearing_seat_step):
    model = import_step(bearing_seat_step)
    scheme = derive_scheme(model, extract_features(model.shape, model.bbox))

    assert _values(scheme, "depth") == pytest.approx([6.0], abs=1e-2)


def test_counterbore_needs_no_extra_position_dimensions(bearing_seat_step):
    """沉孔与引导孔同轴，引导孔的定位已经把它定住了；再发一遍就是超定。"""
    model = import_step(bearing_seat_step)
    scheme = derive_scheme(model, extract_features(model.shape, model.bbox))

    cbore_features = {c.feature for c in scheme.plan.constraints if c.dof == "counterbore"}
    located = [c for c in scheme.plan.constraints if c.dof in ("x", "y", "z")
               and c.feature in cbore_features]

    assert located == []


def test_scheme_with_counterbores_is_still_legal(bearing_seat_step):
    model = import_step(bearing_seat_step)
    scheme = derive_scheme(model, extract_features(model.shape, model.bbox))

    assert check_plan(scheme.plan, scheme.unknowns) == ()


def test_bore_position_is_measured_from_the_datum_face(plate_step):
    """板 80×50，孔在 (40, 25)：定位尺寸必须从基准面量起，而不是从原点。"""
    model, scheme = _scheme(plate_step)

    positions = {c.dof: c.value for c in scheme.plan.constraints if c.dof in ("x", "y")}

    assert positions["x"] == pytest.approx(40.0)
    assert positions["y"] == pytest.approx(25.0)


def test_a_derived_scheme_is_legal_by_construction(plate_step, stepped_shaft_step,
                                                   bearing_seat_step):
    """基线方案必须自己过得了 linter —— 过不了就不配当生成器的下限。"""
    for step in (plate_step, stepped_shaft_step, bearing_seat_step):
        model = import_step(step)
        scheme = derive_scheme(model, extract_features(model.shape, model.bbox))
        violations = check_plan(scheme.plan, scheme.unknowns)
        kinds = {v.kind for v in violations}
        assert not kinds & {UNDER_CONSTRAINED, NO_DATUM, NO_TOLERANCE_HOME}, \
            f"{step.name} 的基线方案不合法：{violations}"


def test_every_unknown_is_pinned_exactly_once(plate_step):
    model, scheme = _scheme(plate_step)

    pinned = [(c.feature, c.dof) for c in scheme.plan.constraints]

    assert len(pinned) == len(set(pinned)), "同一个未知量被钉了两次"
    assert set(pinned) == set(scheme.unknowns)


def test_an_enclosed_void_gets_no_depth_dimension(enclosed_void_step):
    """没有入口面的封闭内腔不发深度 —— 它不是钻出来的，`↧` 无量可量。

    此前这类孔照样发 `Sized(depth)`，落位层画不出来（mouth is None），
    于是方案与图纸互相矛盾、交付门把整张图拦死。实测语料里有 8 条。
    """
    model = import_step(enclosed_void_step)
    features = extract_features(model.shape, model.bbox)
    bore = next(f for f in features if isinstance(f, Bore))
    scheme = derive_scheme(model, features)

    assert bore.mouth is None, "封闭内腔两端都不通，不该有入口"
    assert [c.value for c in scheme.plan.constraints if c.dof == "depth"] == []
    # 直径仍然要发：内腔的形状尺寸在生产里照样要验收
    assert [c.value for c in scheme.plan.constraints if c.dof == "diameter"]

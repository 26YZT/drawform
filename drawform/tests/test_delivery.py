"""交付门：不过关就不出图。

门只消费已经量出来的事实，自己不做任何几何判断 —— 判断在特征层与方案层已经完成，
这里的职责是"把这些事实变成一次拒绝"。
"""

from pathlib import Path

import pytest

from drawform.delivery import (ALLOW, BLOCK, ILLEGAL_PLAN, INVALID_SOLID,
                               UNCLASSIFIED_FACES, UNPLACED_DIMENSIONS, check_delivery)
from drawform.dimensioning.constraints import (
    CLOSED_CHAIN, Chain, Datum, Plan, Sized,
)
from drawform.dimensioning.scheme import derive_scheme
from drawform.geometry.loader import import_step
from drawform.geometry.projection import FRONT, LEFT, TOP, project_view


def _clean():
    """一套没有问题的输入：实体有效、方案合法、所有面都判得出内外。"""
    plan = Plan(constraints=(Sized("outline", "length", 80.0),),
                datum=Datum("A"), general_tolerance="ISO 2768-m")
    return dict(is_valid=True, unclassified_faces=0, plan=plan,
                unknowns=(("outline", "length"),))


def test_a_clean_drawing_is_allowed():
    verdict = check_delivery(**_clean())

    assert verdict.decision == ALLOW
    assert verdict.reasons == ()


def test_gate_blocks_a_model_whose_solid_is_invalid():
    """实体本身通不过 OCCT 有效性检查时，识别结果不可信，不得出图。"""
    facts = _clean()
    facts["is_valid"] = False

    verdict = check_delivery(**facts)

    assert verdict.decision == BLOCK
    assert verdict.failed == (INVALID_SOLID,)


def test_gate_blocks_an_illegal_dimension_plan():
    """尺寸链无意闭合 = 多标了一个封闭环，照这套尺寸加工会互相矛盾。"""
    plan = Plan(constraints=(
        Chain("总长", (("a", "x"), ("b", "x")), total=80.0),
        Sized("a", "x", 30.0), Sized("b", "x", 50.0),
    ), datum=Datum("A"), general_tolerance="ISO 2768-m")
    facts = _clean()
    facts["plan"] = plan
    facts["unknowns"] = (("a", "x"), ("b", "x"))

    verdict = check_delivery(**facts)

    assert verdict.decision == BLOCK
    assert verdict.failed == (ILLEGAL_PLAN,)
    # 同一套尺寸既是封闭链、又被重复钉住，两类都该报出来
    assert CLOSED_CHAIN in [v.kind for v in verdict.violations]


def test_gate_blocks_when_some_faces_cannot_be_told_inside_from_outside():
    """判不出内外的圆柱面意味着有特征被悄悄丢掉，图上会缺尺寸。"""
    facts = _clean()
    facts["unclassified_faces"] = 4

    verdict = check_delivery(**facts)

    assert verdict.decision == BLOCK
    assert verdict.failed == (UNCLASSIFIED_FACES,)


def test_a_checker_that_crashes_counts_as_not_passed():
    """检查器自己抛异常 ≠ 通过。

    抄 Varen 那条纪律：门一旦写成"出错就放行"，第一版就会在最该拦的地方放行。
    """
    def broken(**_kwargs):
        raise ZeroDivisionError("boom")

    facts = _clean()
    verdict = check_delivery(**facts, extra_checks=(("新加的孔数核对", broken),))

    assert verdict.decision == BLOCK
    assert any("新加的孔数核对" in r and "异常" in r for r in verdict.reasons)


def test_the_gate_is_fed_by_the_facts_the_kernel_already_produces(bearing_seat_step):
    """端到端：装载层与特征层产出的事实要能直接喂给门，中间不需要再造轮子。"""
    from drawform.geometry.features import extract_features, unclassified_face_count
    from drawform.geometry.loader import import_step

    model = import_step(bearing_seat_step)
    features = extract_features(model.shape, model.bbox)
    scheme = derive_scheme(model, features)

    verdict = check_delivery(is_valid=model.is_valid,
                             unclassified_faces=unclassified_face_count(model.shape),
                             plan=scheme.plan, unknowns=scheme.unknowns)

    assert verdict.allowed, verdict.reasons


def test_gate_can_be_run_on_the_model_alone_before_any_projecting(bearing_seat_step):
    """装载层就能知道的事实必须在装载后就查 —— 无效实体会先把投影层跑崩。

    实测：把门放在排版之后，part_0002 直接以 `min() on empty sequence` 崩在视图层，
    拿到的是裸异常而不是一次拒绝。能提前拦的检查不能排在后面。
    """
    from drawform.delivery import gate_for

    model = import_step(bearing_seat_step)

    verdict = gate_for(model)

    assert verdict.allowed
    assert INVALID_SOLID not in verdict.failed
    # 没有方案时不去跑尺寸合法性检查
    assert ILLEGAL_PLAN not in verdict.failed


def test_a_check_that_returns_a_reason_blocks():
    """检查器返回一句理由 = 不通过。返回 None 才是通过。"""
    facts = _clean()
    verdict = check_delivery(**facts, extra_checks=(
        ("孔数核对", lambda **_kw: "识别到 4 个孔，图纸上只标了 3 个"),))

    assert verdict.decision == BLOCK
    assert "孔数核对" in verdict.failed


def test_the_gate_reports_every_failure_instead_of_stopping_at_the_first(bearing_seat_step):
    """一次把问题报齐。短路只报第一条，会把"到底还差什么"变成要反复跑才好几次才能问完的事。"""
    from drawform.delivery import gate_for

    path = Path(__file__).resolve().parents[2] / "corpus" / "cadsy" / "samples" / "part_0002"
    if not (path / "model.step").is_file():
        pytest.skip("未下载 cadsy 语料")

    model = import_step(path / "model.step")
    verdict = gate_for(model)

    assert verdict.failed == (INVALID_SOLID, UNCLASSIFIED_FACES)
    assert len(verdict.reasons) == 2


def test_gate_blocks_dimensions_the_plan_declared_but_the_sheet_never_drew():
    """方案说"这条要标"、图上却没有 —— 这是交付层与方案层之间最坏的一条缝。

    实测落位层一度只覆盖方案的 51%：322 条孔的定位尺寸一条没画，而分数完全看不出来
    （定位值在分池配对里命中了，纸面上却没有）。所以门必须直接数"发了没画"。
    """
    facts = _clean()
    verdict = check_delivery(**facts,
                             unplaced=(("b1", "x"), ("b2", "x"), ("b3", "y")))

    assert verdict.decision == BLOCK
    assert UNPLACED_DIMENSIONS in verdict.failed
    assert any("3" in r for r in verdict.reasons)


def test_unplaced_count_is_measured_against_what_actually_landed(bearing_seat_step):
    """每一条落到图上的尺寸都带着它来自方案里的哪一条，数得清才拦得准。"""
    from drawform.delivery import unplaced_dimensions
    from drawform.dimensioning.place import place_scheme
    from drawform.geometry.features import extract_features
    from drawform.geometry.loader import import_step

    model = import_step(bearing_seat_step)
    scheme = derive_scheme(model, extract_features(model.shape, model.bbox))
    projections = {direction.name: project_view(model.shape, direction)
                   for direction in (FRONT, TOP, LEFT)}
    placed = place_scheme(scheme, projections)

    assert unplaced_dimensions(scheme, placed) == []


def test_blocked_pipeline_raises_instead_of_quietly_returning(bearing_seat_step):
    """调用方拿到门的结果必须是"要么出图要么抛"，不能是一个可以忘掉的返回值。"""
    from drawform.delivery import DeliveryBlocked, gate_for, require_allowed
    from drawform.geometry.features import extract_features
    from drawform.geometry.loader import import_step

    model = import_step(bearing_seat_step)
    scheme = derive_scheme(model, extract_features(model.shape, model.bbox))
    require_allowed(gate_for(model, scheme))

    verdict = check_delivery(is_valid=False, unclassified_faces=0,
                             plan=scheme.plan, unknowns=scheme.unknowns)
    with pytest.raises(DeliveryBlocked) as raised:
        require_allowed(verdict)
    assert INVALID_SOLID in raised.value.failed


def test_gate_blocks_the_real_model_whose_solid_is_invalid():
    """语料里 part_0002 的实体 BRepCheck 判无效 —— 门必须把它拦下来。"""
    from drawform.delivery import gate_for
    from drawform.geometry.features import extract_features
    from drawform.geometry.loader import import_step

    path = Path(__file__).resolve().parents[2] / "corpus" / "cadsy" / "samples" / "part_0002"
    if not (path / "model.step").is_file():
        pytest.skip("未下载 cadsy 语料")

    model = import_step(path / "model.step")
    verdict = gate_for(model, derive_scheme(model, extract_features(model.shape, model.bbox)))

    assert verdict.decision == BLOCK
    assert INVALID_SOLID in verdict.failed

"""尺寸方案合法性检查（linter）。

判据不依赖任何参考答案：把「一套尺寸」看成「一组关于未知量的方程」，
- 有未知量没被任何方程钉住 → 欠定（照着这套尺寸造不出唯一零件）；
- 一条尺寸链里所有组成环和封闭环都给了尺寸且没标参考 → 超定（无意闭合尺寸链）；
- 定位尺寸存在却没声明基准 → 尺寸没有出发依据；
- 钉住的量既无显式公差也不在标题栏一般公差覆盖内 → 验收时无据可依。

前两条是关于模型的几何事实，可机械判定；后两条是制图规范。
"""

import pytest

from drawform.dimensioning.constraints import (
    CLOSED_CHAIN, NO_DATUM, NO_TOLERANCE_HOME, OVER_CONSTRAINED,
    UNDER_CONSTRAINED,
    Chain, Datum, Plan, Sized, check_plan, unknowns_for,
)


def _seat_unknowns():
    """轴承座：底板长宽高 + 三个孔位（Ø20 通孔、Ø32 沉孔、Ø9 螺栓孔）各自 4 个自由度。"""
    return unknowns_for(
        outline=("length", "width", "height"),
        features={"bore20": ("x", "y", "diameter", "depth"),
                  "cbore32": ("x", "y", "diameter", "depth"),
                  "bolt9": ("x", "y", "diameter", "depth")},
    )


def _complete_plan():
    unknowns = _seat_unknowns()
    constraints = [Sized(feature, dof, 1.0) for feature, dof in unknowns]
    return Plan(constraints=tuple(constraints), datum=Datum("A", "B", "C"))


def test_a_complete_plan_has_no_violations():
    assert check_plan(_complete_plan(), _seat_unknowns()) == ()


def test_missing_one_dimension_is_under_constrained():
    """少钉住任意一个未知量，这套尺寸就造不出唯一零件 —— 必须报出来并指名是谁。"""
    unknowns = _seat_unknowns()
    plan = Plan(constraints=tuple(
        Sized(f, d, 1.0) for f, d in unknowns if (f, d) != ("bore20", "depth")),
        datum=Datum("A", "B", "C"))

    violations = check_plan(plan, unknowns)

    assert [v.kind for v in violations] == [UNDER_CONSTRAINED]
    assert violations[0].target == ("bore20", "depth")


def test_unknowns_cover_every_feature_degree_of_freedom():
    unknowns = _seat_unknowns()
    assert ("bore20", "diameter") in unknowns
    assert ("bolt9", "x") in unknowns
    assert ("outline", "length") in unknowns
    assert len(unknowns) == 3 + 3 * 4


def test_closed_chain_without_a_reference_link_is_over_constrained():
    """总长已经等于三个分段之和，还给总长标实尺 = 无意封闭尺寸链。"""
    unknowns = _seat_unknowns()
    plan = Plan(
        constraints=tuple(Sized(f, d, 1.0) for f, d in unknowns) + (
            Chain("总长", (("seg1", "length"), ("seg2", "length"), ("seg3", "length")),
                  total=80.0),
        ),
        datum=Datum("A", "B", "C"))

    violations = check_plan(plan, unknowns)

    assert [v.kind for v in violations] == [CLOSED_CHAIN]
    assert violations[0].target == "总长"


def test_chain_with_a_reference_link_is_fine():
    """把其中一个分段标成参考尺寸（括号尺寸）后，链不再超定 —— 这正是制图规范的做法。"""
    unknowns = _seat_unknowns()
    plan = Plan(
        constraints=tuple(Sized(f, d, 1.0) for f, d in unknowns) + (
            Chain("总长", (("seg1", "length"), ("seg2", "length"), ("seg3", "length")),
                  total=80.0, reference=("seg3", "length")),
        ),
        datum=Datum("A", "B", "C"))

    assert [v for v in check_plan(plan, unknowns) if v.kind == CLOSED_CHAIN] == []


def test_chain_with_a_total_pins_every_link():
    """封闭环给了实尺，链上每一环就都被决定了 —— 包括那环标成参考尺寸的。

    参考尺寸不是「没标」，而是「由其余环算出来」，所以它同样把未知量钉住了。
    漏掉这点会把一张合法的链标注误报成欠定。
    """
    unknowns = unknowns_for(outline=("length",), features={"slot": ("x",)})
    plan = Plan(constraints=(
        Sized("slot", "x", 10.0),
        Chain("全长", (("outline", "length"), ("slot", "x")), total=80.0,
              reference=(("outline", "length"),)),
    ), datum=None)

    under = [v for v in check_plan(plan, unknowns) if v.kind == UNDER_CONSTRAINED]
    assert not under, f"链标注被误报成欠定：{under}"


def test_location_dimensions_without_a_datum_are_unanchored():
    unknowns = _seat_unknowns()
    plan = Plan(constraints=tuple(Sized(f, d, 1.0) for f, d in unknowns), datum=None)

    violations = check_plan(plan, unknowns)

    assert NO_DATUM in [v.kind for v in violations]


def test_pinned_quantity_needs_a_tolerance_home():
    """既没给显式公差、标题栏也没有一般公差 → 验收时无据可依。"""
    unknowns = _seat_unknowns()
    plan = Plan(constraints=tuple(Sized(f, d, 1.0) for f, d in unknowns),
                datum=Datum("A", "B", "C"), general_tolerance=None)

    violations = check_plan(plan, unknowns)

    homeless = [v for v in violations if v.kind == NO_TOLERANCE_HOME]
    assert len(homeless) == len(unknowns)


def test_general_tolerance_note_covers_untoleranced_dimensions():
    unknowns = _seat_unknowns()
    plan = Plan(constraints=tuple(Sized(f, d, 1.0) for f, d in unknowns),
                datum=Datum("A", "B", "C"), general_tolerance="ISO 2768-m")

    assert [v for v in check_plan(plan, unknowns)
            if v.kind == NO_TOLERANCE_HOME] == []


def test_explicit_tolerance_also_counts():
    unknowns = _seat_unknowns()
    plan = Plan(constraints=tuple(Sized(f, d, 1.0, tolerance="±0.1") for f, d in unknowns),
                datum=Datum("A", "B", "C"), general_tolerance=None)

    assert [v for v in check_plan(plan, unknowns)
            if v.kind == NO_TOLERANCE_HOME] == []


def test_same_dof_pinned_twice_with_the_same_value_is_a_duplicate_dimension():
    """同一个自由度被两条数值相同的尺寸钉住 = 重复标注。

    方程组里这是一个未知数配了两个相同方程：不矛盾，但 GB/T 不允许重复标，
    而且它会把"封闭尺寸链"伪装成"每环都标了一份"。
    """
    plan = Plan(constraints=(Sized("boss", "x", 40.0), Sized("boss", "x", 40.0)),
                datum=Datum("A"))

    violations = check_plan(plan, (("boss", "x"),))

    assert [v.kind for v in violations] == [OVER_CONSTRAINED]
    assert "重复" in violations[0].message


def test_same_dof_pinned_twice_with_different_values_is_a_contradiction():
    """同一自由度被两个不同数值钉住 = 矛盾，照这套尺寸造不出任何零件。"""
    plan = Plan(constraints=(Sized("boss", "x", 40.0), Sized("boss", "x", 42.0)),
                datum=Datum("A"))

    violations = check_plan(plan, (("boss", "x"),))

    assert [v.kind for v in violations] == [OVER_CONSTRAINED]
    assert "矛盾" in violations[0].message


def test_different_dofs_on_the_same_feature_are_not_over_constrained():
    """一个孔标了直径又标了深度，是两个未知数两条方程，不超定。"""
    plan = Plan(constraints=(Sized("bore", "diameter", 10.0), Sized("bore", "depth", 6.0)),
                datum=Datum("A"))

    assert [v.kind for v in check_plan(plan, ())] == []



def test_a_chain_without_a_total_does_not_pin_its_links():
    """封闭环不给实尺时，链只是"这几环相加等于那个没标出来的数"的关系，它一个数值都不提供。

    旧实现把非参考环也算成"已被链钉住"，于是**一条都没标实尺的链能冒充完整尺寸**
    通过欠定检查 —— 图纸上看着有链，实际没有任何一个数被定住。
    """
    unknowns = (("a", "x"), ("b", "x"))
    plan = Plan(constraints=(
        Chain("总长", unknowns, reference=(("b", "x"),)),
    ), datum=Datum("A"))

    under = [v.target for v in check_plan(plan, unknowns) if v.kind == UNDER_CONSTRAINED]

    assert ("a", "x") in under, f"链没给任何数值却没报欠定：{under}"


def test_the_derived_link_of_a_chain_needs_no_direct_dimension():
    """被指定为"由链算出来"的那一环不该要求它自己被标实尺，否则每条链都必被判欠定。

    原来这条用例没给 `total`，于是它的期望与"链不给数值就什么都推不出来"互相矛盾 ——
    一个方程要解出一个未知量，前提是其余都已知**且封闭环的数值已知**。
    测试的意图（参考环不必标实尺）保留，setup 补上封闭环。
    """
    unknowns = (("a", "x"), ("b", "x"))
    plan = Plan(constraints=(
        Chain("总长", unknowns, total=80.0, reference=(("b", "x"),)),
        Sized("a", "x", 30.0),
    ), datum=Datum("A"))

    under = [v.target for v in check_plan(plan, unknowns) if v.kind == UNDER_CONSTRAINED]

    assert under == [], f"参考环被误判成欠定：{under}"


def test_a_solvable_chain_without_total_is_clean():
    unknowns = (("a", "x"), ("b", "x"))
    plan = Plan(constraints=(
        Chain("总长", unknowns, total=80.0, reference=(("b", "x"),)),
        Sized("a", "x", 30.0),
    ), datum=Datum("A"), general_tolerance="ISO 2768-m")

    assert [v.kind for v in check_plan(plan, unknowns)] == []


def test_a_chain_with_two_undimensioned_links_pins_neither():
    """封闭环给了实尺，但链上有两环没标：一个方程解不出两个未知量，两环都该报欠定。"""
    unknowns = (("a", "x"), ("b", "x"), ("c", "x"))
    plan = Plan(constraints=(
        Chain("总长", unknowns, total=80.0),
        Sized("a", "x", 30.0),
    ), datum=Datum("A"))

    under = sorted(v.target for v in check_plan(plan, unknowns)
                   if v.kind == UNDER_CONSTRAINED)

    assert under == [("b", "x"), ("c", "x")]


def test_chains_that_unlock_each_other_are_both_clean():
    """链可以互相解锁：A 链推出的那一环，可能是 B 链解锁的前提。"""
    plan = Plan(constraints=(
        Chain("横向", (("a", "x"), ("b", "x")), total=50.0, reference=(("b", "x"),)),
        Chain("纵向", (("b", "x"), ("c", "x")), total=30.0, reference=(("c", "x"),)),
        Sized("a", "x", 20.0),
    ), datum=Datum("A"))

    assert [v.kind for v in check_plan(plan, (("a", "x"), ("b", "x"), ("c", "x")))] == []


def test_a_chain_without_a_total_cannot_derive_its_closing_link():
    """封闭环没给数值时，哪怕其余环全标了实尺，参考环仍然推不出来 —— 和是未知的。

    这条与上一条分开测：上一条里没有任何实尺，链在"其余环未定"那一步就被挡住，
    所以它测不到 `total is None` 这个判据本身（变异"去掉 total 判断"当场存活）。
    """
    unknowns = (("a", "x"), ("b", "x"), ("c", "x"))
    plan = Plan(constraints=(
        Chain("总长", unknowns, reference=(("c", "x"),)),
        Sized("a", "x", 30.0),
        Sized("b", "x", 20.0),
    ), datum=Datum("A"))

    under = [v.target for v in check_plan(plan, unknowns) if v.kind == UNDER_CONSTRAINED]

    assert ("c", "x") in under, f"封闭环未知却把参考环算成已定：{under}"


def test_a_derived_link_needs_every_other_link_dimensioned_first():
    """一个方程只能解一个未知量：链上还有一环没标实尺时，参考环也不能算成已定。

    少了这条，"链 + 只标一环"就能蒙过欠定检查（变异"不看其余环是否已知"当场存活）。
    """
    unknowns = (("a", "x"), ("b", "x"), ("c", "x"))
    plan = Plan(constraints=(
        Chain("总长", unknowns, total=80.0, reference=(("c", "x"),)),
        Sized("a", "x", 30.0),
    ), datum=Datum("A"))

    under = sorted(v.target for v in check_plan(plan, unknowns)
                   if v.kind == UNDER_CONSTRAINED)

    assert under == [("b", "x"), ("c", "x")]

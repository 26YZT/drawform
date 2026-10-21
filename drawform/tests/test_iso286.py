"""ISO 286 / GB/T 1800.1 标准公差与基本偏差数表。

数表的正确性不靠「抄对了」，靠三类可执行断言：
1. 单调性：尺寸段变大或等级变低，公差只能变大；
2. 公式带：IT5–IT11 必须落在 a·i 的舍入带内（i = 0.45·∛D + 0.001·D）；
3. 独立锚点：Ø40H7、Ø40g6 这类业内可背的值。
只录入两份独立 MIT 转录逐格一致的格子；单源或有分歧的格子一律不 ship，并有测试守着。
"""

import math

import pytest

from drawform.rules.iso286 import (
    IT_GRADES, SHAFT_LETTERS, SIZE_RANGES, UnsupportedDeviation, fit_for, it_microns,
)


def _unit_i(lo, hi):
    d = math.sqrt(lo * hi)
    return 0.45 * d ** (1.0 / 3.0) + 0.001 * d


MULTIPLIERS = {6: 10, 7: 16, 8: 25, 9: 40, 10: 64, 11: 100}


def test_size_ranges_cover_1_to_500_and_are_contiguous():
    assert SIZE_RANGES[0][0] == 1
    assert SIZE_RANGES[-1][1] == 500
    for (_, upper), (lower, _) in zip(SIZE_RANGES, SIZE_RANGES[1:]):
        assert upper == lower, "尺寸段必须首尾相接，否则中间尺寸会查不到"


def test_tolerance_grows_with_size_and_with_looser_grade():
    for grade in IT_GRADES:
        values = [it_microns(grade, (lo + hi) / 2) for lo, hi in SIZE_RANGES]
        assert values == sorted(values), f"IT{grade} 随尺寸非单调：{values}"

    for lo, hi in SIZE_RANGES:
        mid = (lo + hi) / 2
        values = [it_microns(g, mid) for g in IT_GRADES]
        assert values == sorted(values), f"{lo}–{hi} 段随等级非单调：{values}"


def test_every_cell_sits_inside_the_standard_tolerance_unit_band():
    """每个格子都要能被 i 公式解释，容差按 ISO 取整放宽到最小两档。"""
    for index, (lo, hi) in enumerate(SIZE_RANGES):
        band = 0.15 if index < 2 else 0.06
        for grade, multiplier in MULTIPLIERS.items():
            if grade not in IT_GRADES:
                continue
            published = it_microns(grade, (lo + hi) / 2)
            predicted = _unit_i(lo, hi) * multiplier
            assert abs(published - predicted) / published <= band, (
                f">{lo}-{hi} IT{grade}：表值 {published} 与 {multiplier}i={predicted:.2f}"
                f" 偏差超出 {band:.0%}，疑似转录错格"
            )


def test_it4_and_it5_are_not_shipped_because_only_one_source_has_them():
    """两份独立转录只共同覆盖 IT6–IT11；IT4/IT5 是单源，未经第二方核对不得进表。"""
    assert 5 not in IT_GRADES and 4 not in IT_GRADES


def test_shaft_k_is_excluded_because_the_two_sources_disagree():
    """>1–3 mm 的 k 一份给 0、一份给 1：小尺寸段 j/k/m/n 的基本偏差本就随等级变，
    单行表表达不了，宁可不给也不能猜。"""
    assert "k" not in SHAFT_LETTERS


def test_bore_40_h7_is_the_famous_plus_25_zero():
    assert fit_for("H7", 40.0) == pytest.approx((+0.025, 0.0), abs=1e-9)


def test_shaft_40_g6_is_minus_9_to_minus_25():
    assert fit_for("g6", 40.0) == pytest.approx((-0.009, -0.025), abs=1e-9)


def test_bore_25_h8_is_plus_33_zero():
    assert fit_for("H8", 25.0) == pytest.approx((+0.033, 0.0), abs=1e-9)


def test_shaft_f7_gives_a_running_fit_on_40():
    es, ei = fit_for("f7", 40.0)
    assert (es, ei) == pytest.approx((-0.025, -0.050), abs=1e-9)


def test_hole_h_lower_deviation_is_always_zero():
    for lo, hi in SIZE_RANGES:
        assert fit_for("H7", (lo + hi) / 2)[1] == 0.0


def test_asking_for_an_unsupported_letter_raises():
    with pytest.raises(UnsupportedDeviation):
        fit_for("ka9", 40.0)

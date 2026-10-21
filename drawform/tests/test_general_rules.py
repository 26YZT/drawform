"""ISO 2768-1 未注线性尺寸一般公差 与 GB/T 1031 Ra 优先数系列。

两张表的可执行判据不同：
* ISO 2768-1 是纯查表数据，靠两份独立来源逐格一致 + 单调性守；
* Ra 是优先数系，可以独立用「ISO 3 R10 系列 × 10 的整数幂」判定，
  因此本表只收录能通过该判据的值（0.012 μm 只有单一来源，且偏离 R10 的 1.25 约 4%，
  无法自证，不收录）。
"""

import math

import pytest

from drawform.rules.roughness import RA_VALUES, nearest_not_coarser_than
from drawform.rules.iso2768 import (
    CLASSES, SIZE_RANGES, UnsupportedGeneralTolerance, general_tolerance,
)


def test_iso2768_ranges_are_contiguous():
    assert SIZE_RANGES[0][0] == 0.5
    assert SIZE_RANGES[-1][1] == 4000
    for (_, upper), (lower, _) in zip(SIZE_RANGES, SIZE_RANGES[1:]):
        assert upper == lower


def test_looser_class_never_tighter():
    for lo, hi in SIZE_RANGES:
        mid = (lo + hi) / 2
        values = []
        for cls in CLASSES:
            try:
                values.append(general_tolerance(cls, mid))
            except UnsupportedGeneralTolerance:
                values.append(None)
        present = [v for v in values if v is not None]
        assert present == sorted(present), f"{lo}–{hi} 段 f/m/c/v 非单调：{values}"


def test_tolerance_grows_with_size():
    for cls in CLASSES:
        values = []
        for lo, hi in SIZE_RANGES:
            try:
                values.append(general_tolerance(cls, (lo + hi) / 2))
            except UnsupportedGeneralTolerance:
                continue
        assert values == sorted(values), f"{cls} 级随尺寸非单调：{values}"


def test_anchors_from_the_standard():
    assert general_tolerance("m", 40.0) == pytest.approx(0.3)
    assert general_tolerance("f", 10.0) == pytest.approx(0.1)
    assert general_tolerance("c", 150.0) == pytest.approx(1.2)
    assert general_tolerance("v", 5.0) == pytest.approx(0.5)


def test_classes_that_the_standard_leaves_blank_are_refused():
    """f 级不管 >2000–4000，v 级不管 0.5–3，标准里是空格，不能编一个数出来。"""
    with pytest.raises(UnsupportedGeneralTolerance):
        general_tolerance("f", 3000.0)
    with pytest.raises(UnsupportedGeneralTolerance):
        general_tolerance("v", 2.0)


def test_out_of_range_size_raises():
    with pytest.raises(UnsupportedGeneralTolerance):
        general_tolerance("m", 9000.0)


def test_ra_values_sit_on_the_r10_lattice():
    """每个 Ra 都必须落在 10^(k/10) 的优先数格子上（2% 以内），这是独立于任何转录的判据。

    0.012 μm 落在格子上偏 −4.7%，加上只有单一来源，因此不收录；
    同理任何一位数字转录错（如 12.5 写成 12.0）都会被这条抓住。
    """
    for value in RA_VALUES:
        k = round(10 * math.log10(value))
        lattice = 10 ** (k / 10.0)
        assert abs(value - lattice) / lattice <= 0.02, (
            f"{value} 偏离 R10 格子 {lattice:.4g} 达 "
            f"{abs(value - lattice) / lattice:.1%}，不是优先数")


def test_ra_lattice_check_actually_rejects_the_excluded_value():
    k = round(10 * math.log10(0.012))
    lattice = 10 ** (k / 10.0)
    assert abs(0.012 - lattice) / lattice > 0.02
    assert 0.012 not in RA_VALUES


def test_ra_series_is_ascending_and_covers_practical_range():
    assert list(RA_VALUES) == sorted(RA_VALUES)
    assert RA_VALUES[0] == 0.025
    assert RA_VALUES[-1] == 100.0


def test_nearest_not_coarser_never_picks_a_coarser_surface():
    """要求 Ra≤0.7 时只能选 0.4：选 0.8 会比要求粗，零件直接不合格。"""
    assert nearest_not_coarser_than(0.7) == 0.4
    assert nearest_not_coarser_than(1.6) == 1.6
    assert nearest_not_coarser_than(2.0) == 1.6
    assert nearest_not_coarser_than(50.0) == 50.0


def test_requirement_finer_than_the_series_is_refused():
    with pytest.raises(ValueError):
        nearest_not_coarser_than(0.01)

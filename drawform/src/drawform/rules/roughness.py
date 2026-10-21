"""GB/T 1031 / ISO 4287 表面粗糙度 Ra 优先数值系列。

Ra 不是查出来的测量值，而是优先数系：每个值都必须落在 10^(k/10) 的 R10 格子上
（2% 以内）。这条判据独立于任何转录，所以本模块只收录能通过它的值。

0.012 μm（14 级）没有收录：它偏离格子 −4.7%，无法用优先数系自证，
且本次只找到单一来源。需要它时必须先补一个独立来源。
"""

from __future__ import annotations

#: 单位 μm。R10 优先数系，公比 10^(3/10) ≈ 2。
RA_VALUES: tuple[float, ...] = (
    0.025, 0.05, 0.1, 0.2, 0.4, 0.8, 1.6, 3.2, 6.3, 12.5, 25.0, 50.0, 100.0,
)


def nearest_not_coarser_than(requirement_um: float) -> float:
    """把功能要求的 Ra 上限吸附到标准值，只允许往更细（更小）的一档靠。

    要求 Ra≤0.7 时选 0.8 会得到比要求更粗的表面，零件直接不合格，
    所以只能取「不超过要求的最大标准值」。
    """
    candidates = [value for value in RA_VALUES if value <= requirement_um + 1e-12]
    if not candidates:
        raise ValueError(
            f"要求 Ra≤{requirement_um} μm 比本表最小值 {RA_VALUES[0]} μm 更细，"
            "该粗糙度已不属于常规机械加工范围，需人工判定")
    return max(candidates)

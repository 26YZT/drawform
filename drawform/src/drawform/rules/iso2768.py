"""ISO 2768-1 未注公差的线性尺寸一般公差。

数值来自两份互相独立的公开转录（csl-imt.ch 与 rivcut.com 的 ISO 2768 表），
两者逐格一致；测试另外用「f≤m≤c≤v」与「随尺寸不减」两条单调性守住错格。
标准里留空的格子（f 级不管 >2000–4000、v 级不管 0.5–3）一律抛异常，不补数。

注意：GB/T 1804-2000 与本标准数值一致，但本模块只按 ISO 2768-1 口径核对过，
出图注记要写 GB/T 1804 还是 ISO 2768 需按客户体系决定，不能在这里替用户混用。
"""

from __future__ import annotations

SIZE_RANGES: tuple[tuple[float, float], ...] = (
    (0.5, 3), (3, 6), (6, 30), (30, 120),
    (120, 400), (400, 1000), (1000, 2000), (2000, 4000),
)

#: 01 级四个公差等级：f 精密 / m 中等 / c 粗糙 / v 最粗
CLASSES = ("f", "m", "c", "v")

#: ±值，单位 mm，按 SIZE_RANGES 顺序；None 表示标准中该格为空。
TABLE = {
    "f": (0.05, 0.05, 0.1, 0.15, 0.2, 0.3, 0.5, None),
    "m": (0.1, 0.1, 0.2, 0.3, 0.5, 0.8, 1.2, 2.0),
    "c": (0.2, 0.3, 0.5, 0.8, 1.2, 2.0, 3.0, 4.0),
    "v": (None, 0.5, 1.0, 1.5, 2.5, 4.0, 6.0, 8.0),
}


class UnsupportedGeneralTolerance(Exception):
    """该等级在该尺寸段没有规定值，或尺寸超出 >0.5–4000 mm。"""


def _index(nominal_mm: float) -> int:
    for index, (lower, upper) in enumerate(SIZE_RANGES):
        if lower < nominal_mm <= upper:
            return index
    raise UnsupportedGeneralTolerance(
        f"公称尺寸 {nominal_mm} mm 超出一般公差表的 >0.5–4000 mm")


def covers(nominal_mm: float) -> bool:
    """该公称尺寸是否落在表覆盖的 >0.5–4000 mm 之内。

    与 `general_tolerance` 抛异常的区别：这一条是**问**，那条是**取值**。
    交付门要区分"尺寸根本不在表的范围内"（图纸上的承诺不成立，该拦）与
    "在范围内但该等级留空"（同样不成立），而不要把两者都塞进异常里靠文案分辨。
    """
    try:
        _index(nominal_mm)
    except UnsupportedGeneralTolerance:
        return False
    return True


def general_tolerance(tolerance_class: str, nominal_mm: float) -> float:
    """返回 ± 对称公差值（mm）。"""
    if tolerance_class not in TABLE:
        raise UnsupportedGeneralTolerance(f"未知的一般公差等级：{tolerance_class!r}")
    value = TABLE[tolerance_class][_index(nominal_mm)]
    if value is None:
        raise UnsupportedGeneralTolerance(
            f"ISO 2768-1 中 {tolerance_class} 级在该尺寸段为空，不得自行补值")
    return value

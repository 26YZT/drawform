"""ISO 286-1 / GB/T 1800.1 标准公差数值与基本偏差。

数表不能靠公式生成：i = 0.45·∛D + 0.001·D 配倍率只能复现约八成格子，错的那些
一律差 1 μm —— 因为标准值是按 ISO 1116 规则取整到优先数，不是就近取整。
所以本表是转录来的，并且只录入两份独立 MIT 转录
（dooosp_freecad-automation/scripts/_tolerance_db.py 与
U-C4N_Autocad-MCP/engineering/fits.py）逐格一致的部分：

* 尺寸段 >1–500 mm 共 13 段，两源完全相同；
* IT6–IT11 共 78 格，两源逐格一致；IT4/IT5 只有单源，不收录；
* 轴偏差 d/e/f/g（上偏差）与 m/n/p（下偏差）两源一致；h 与孔 H 按定义恒为 0；
* 轴 k 在 >1–3 mm 两源给出 0 与 1 两个值 —— 小尺寸段 j/k/m/n 的基本偏差本就随
  等级变化，单行表表达不了，因此整个 k 不收录；
* 孔的 F/G/K/N/P 只有单源，不收录（基孔制 H 已覆盖绝大多数标注场景）。

需要更大范围或更多代号时，必须再找独立来源核对，不能凭记忆补格。
"""

from __future__ import annotations

import re

SIZE_RANGES: tuple[tuple[int, int], ...] = (
    (1, 3), (3, 6), (6, 10), (10, 18), (18, 30), (30, 50), (50, 80),
    (80, 120), (120, 180), (180, 250), (250, 315), (315, 400), (400, 500),
)

#: 标准公差因子倍率，用于公式带校验；不是生成数表的手段。
IT_MULTIPLIERS = {6: 10, 7: 16, 8: 25, 9: 40, 10: 64, 11: 100}

#: 单位 μm，按 SIZE_RANGES 顺序。
IT_TABLE = {
    6: (6, 8, 9, 11, 13, 16, 19, 22, 25, 29, 32, 36, 40),
    7: (10, 12, 15, 18, 21, 25, 30, 35, 40, 46, 52, 57, 63),
    8: (14, 18, 22, 27, 33, 39, 46, 54, 63, 72, 81, 89, 97),
    9: (25, 30, 36, 43, 52, 62, 74, 87, 100, 115, 130, 140, 155),
    10: (40, 48, 58, 70, 84, 100, 120, 140, 160, 185, 210, 230, 250),
    11: (60, 75, 90, 110, 130, 160, 190, 220, 250, 290, 320, 360, 400),
}

#: 轴的基本偏差（μm）：这些代号的偏差值是上偏差 es。
SHAFT_ES = {
    "d": (-20, -30, -40, -50, -65, -80, -100, -120, -145, -170, -190, -210, -230),
    "e": (-14, -20, -25, -32, -40, -50, -60, -72, -85, -100, -110, -125, -135),
    "f": (-6, -10, -13, -16, -20, -25, -30, -36, -43, -50, -56, -62, -68),
    "g": (-2, -4, -5, -6, -7, -9, -10, -12, -14, -15, -17, -18, -20),
}

#: 轴的基本偏差（μm）：这些代号的偏差值是下偏差 ei。
SHAFT_EI = {
    "m": (2, 4, 6, 7, 8, 9, 11, 13, 15, 17, 20, 21, 23),
    "n": (4, 8, 10, 12, 15, 17, 20, 23, 27, 31, 34, 37, 40),
    "p": (6, 12, 15, 18, 22, 26, 32, 37, 43, 50, 56, 62, 68),
}

IT_GRADES = tuple(sorted(IT_TABLE))
SHAFT_LETTERS = frozenset(SHAFT_ES) | frozenset(SHAFT_EI) | {"h"}
HOLE_LETTERS = frozenset({"H"})


class UnsupportedDeviation(Exception):
    """表里没有这个代号或尺寸 —— 宁可不给，也不给一个没核对过的值。"""


def _size_index(nominal_mm: float) -> int:
    for index, (lower, upper) in enumerate(SIZE_RANGES):
        if lower < nominal_mm <= upper:
            return index
    raise UnsupportedDeviation(
        f"公称尺寸 {nominal_mm} mm 超出已核对的 >1–500 mm 范围")


def it_microns(grade: int, nominal_mm: float) -> int:
    try:
        row = IT_TABLE[grade]
    except KeyError:
        raise UnsupportedDeviation(
            f"IT{grade} 未收录：本表只有两源一致的 IT{IT_GRADES[0]}–IT{IT_GRADES[-1]}"
        ) from None
    return row[_size_index(nominal_mm)]


def _parse(code: str) -> tuple[str, int]:
    match = re.fullmatch(r"\s*([A-Za-z])(\d{1,2})\s*", code or "")
    if not match:
        raise UnsupportedDeviation(f"无法解析公差代号：{code!r}（两字母代号如 JS6 暂不支持）")
    return match.group(1), int(match.group(2))


def fit_for(code: str, nominal_mm: float) -> tuple[float, float]:
    """查代号对应的上下偏差，单位 mm，返回 (上偏差, 下偏差)。"""
    letter, grade = _parse(code)
    index = _size_index(nominal_mm)
    it = it_microns(grade, nominal_mm)

    if letter.isupper():
        if letter not in HOLE_LETTERS:
            raise UnsupportedDeviation(f"孔的偏差代号 {letter} 只有单源，未核对，不收录")
        lower = 0.0
        return (lower + it) / 1000.0, lower

    if letter == "h":
        return 0.0, -it / 1000.0
    if letter in SHAFT_ES:
        upper = SHAFT_ES[letter][index]
        return upper / 1000.0, (upper - it) / 1000.0
    if letter in SHAFT_EI:
        lower = SHAFT_EI[letter][index]
        return (lower + it) / 1000.0, lower / 1000.0

    raise UnsupportedDeviation(f"轴的偏差代号 {letter} 未收录（k 两源冲突，见模块说明）")

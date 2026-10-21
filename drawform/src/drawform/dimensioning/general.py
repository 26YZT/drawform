"""把标题栏那句「一般公差」逐条落到尺寸上。

图上**不画**逐条公差框格 —— 未注公差按画法就是由标题栏统一声明的，画上去反而不合规范。
这一层买的是另外两件事：

1. 校对人问"这条 ⌀20 到底允许差多少"时答得出，而且答案是从核对过的数表推出来的；
2. 交付门能发现"这句承诺盖不住图上的某条尺寸"（超出 >0.5–4000、或该等级在该段是空格）。
   那等于图纸上写了一句不成立的话，比不写更糟。

⚠️ 数表只按 ISO 2768-1 的**线性**那张核对过（两份独立转录逐格一致）。角度未注公差在同一
标准里是另一张表（按边长分档），GB/T 1804 数值虽然一致但没核对过它 —— 这两种情况一律
标未覆盖，**不许拿线性值凑**。
"""

from __future__ import annotations

import re

from drawform.dimensioning.constraints import UNDECLARED, Sized
from drawform.rules.iso2768 import (SIZE_RANGES, UnsupportedGeneralTolerance,
                                    covers, general_tolerance)

#: 角度未注公差是另一张表，我们手上没有核对过的版本。
ANGULAR_DOFS = frozenset({"angle"})

#: 只认 ISO 2768-1 这一种写法。认不出来不等于"大概是他"，见 `_rows` 里的 unverified。
_DECLARATION = re.compile(r"^\s*ISO\s*2768-?1?\s*[-–]\s*([fmcv])\s*$", re.IGNORECASE)

#: 这两类是"图纸上的承诺在该尺寸上不成立"，与"我们还没做那张表"性质不同。
PROMISE_BROKEN = ("out_of_range", "blank_cell")


def _band(nominal_mm: float) -> str:
    for lower, upper in SIZE_RANGES:
        if lower < nominal_mm <= upper:
            return f">{lower:g}–{upper:g}"
    return ""


def general_tolerances(plan) -> tuple[dict, ...]:
    """方案里每条**没有专门公差**的尺寸，标题栏那句话实际允许差多少。

    专门公差压过一般公差：给 H7 的孔再套一个 ±0.2 会把图纸改成自相矛盾，
    所以带 `tolerance` 的那几条根本不出现在这里。
    """
    declared = getattr(plan, "general_tolerance", UNDECLARED)
    if declared is UNDECLARED or not declared:
        return ()
    note = str(declared).strip()
    match = _DECLARATION.match(note)
    grade = match.group(1).lower() if match else ""

    rows = []
    for item in plan.constraints:
        if not isinstance(item, Sized) or item.tolerance is not None:
            continue
        row = {"feature": item.feature, "dof": item.dof, "value": item.value,
               "declared": note}
        if not match:
            rows.append({**row, "covered": False, "kind": "standard_unverified",
                         "why": f"「{note}」的数表未核对过，不套用 ISO 2768-1 的值"})
            continue
        if item.dof in ANGULAR_DOFS:
            rows.append({**row, "covered": False, "kind": "angular_table_missing",
                         "why": "角度未注公差是另一张表（按边长分档），未核对，"
                                "不套线性值"})
            continue
        if item.value == 0.0:
            # 落位层已把浮点噪声归零（`scheme.ZERO_POSITION`）。"0" 说的是与基准面共面，
            # 不是一段长度 —— 未注公差对它不适用，但这条尺寸必须留在图上，
            # 否则那个孔在这个方向就没有任何定位依据了。
            rows.append({**row, "covered": False, "kind": "coincidence",
                         "why": "0 是与基准面共面，不是一段长度，未注公差不适用"})
            continue
        if not covers(item.value):
            rows.append({**row, "covered": False, "kind": "out_of_range",
                         "why": f"公称尺寸 {item.value:g} mm 超出一般公差表的 "
                                f">{SIZE_RANGES[0][0]:g}–{SIZE_RANGES[-1][1]:g} mm"})
            continue
        try:
            plus_minus = general_tolerance(grade, item.value)
        except UnsupportedGeneralTolerance as blank:
            rows.append({**row, "covered": False, "kind": "blank_cell",
                         "why": str(blank)})
            continue
        rows.append({**row, "covered": True, "kind": "covered",
                     "standard": "ISO 2768-1", "grade": grade,
                     "plus_minus": plus_minus, "band": _band(item.value)})
    return tuple(rows)


def broken_promises(rows) -> tuple[dict, ...]:
    """那句一般公差**盖不住**的那几条。这才是图纸说谎，不是我们少做了一张表。"""
    return tuple(row for row in rows if row["kind"] in PROMISE_BROKEN)


def general_tolerance_note(plan) -> str:
    """首次出图和答案回写共用同一条覆盖声明。"""
    declared = plan.general_tolerance
    if declared is UNDECLARED or not declared:
        return ""
    note = f"一般公差 {declared}"
    gaps = broken_promises(general_tolerances(plan))
    if gaps:
        note += f"；另有 {len(gaps)} 条尺寸不在该表覆盖段内，需专门公差"
    return note

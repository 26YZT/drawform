"""交叉核对两份独立 MIT 转录的 ISO 286 标准公差表，并用 i 公式复核倍率序列。

结论只认「两源一致 + 公式可解释」的格子；不一致的格子必须留给人工判定，不能猜。
"""
import importlib.util
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclass 需要能在 sys.modules 里找到宿主模块
    spec.loader.exec_module(module)
    return module


a = load("tdb", ROOT / "repos-cad/dooosp_freecad-automation/scripts/_tolerance_db.py")
b = load("fits", ROOT / "repos-cad/U-C4N_Autocad-MCP/engineering/fits.py")

ranges_a = [(lo, hi) for lo, hi in a.DIAMETER_RANGES]
ranges_b = [(lo, hi) for lo, hi in b._SIZE_STEPS]
print("尺寸分段一致:", ranges_a == ranges_b, f"({len(ranges_a)} 段)")

# 归一化成 {(段序号, 等级): μm}
table_a = {(i, g): v for g, row in a.IT_GRADES.items() for i, v in enumerate(row)}
table_b = {(i, g): v for i, row in enumerate(b._IT_TABLE) for g, v in zip(b._IT_GRADES, row)}

common = sorted(set(table_a) & set(table_b))
print(f"重叠格子 {len(common)} 个（A 有 {len(table_a)}，B 有 {len(table_b)}）")

diff = [(i, g, table_a[(i, g)], table_b[(i, g)]) for (i, g) in common
        if table_a[(i, g)] != table_b[(i, g)]]
print("两源不一致:", diff if diff else "无")

MULTIPLIERS = {4: 4, 5: 7, 6: 10, 7: 16, 8: 25, 9: 40, 10: 64, 11: 100}


def unit_i(lo, hi):
    d = math.sqrt(lo * hi)
    return 0.45 * d ** (1 / 3) + 0.001 * d


print("\n用 i = 0.45·∛D + 0.001·D 复核倍率（D 取分段几何均值）：")
worst = []
for (i, g) in common:
    published = table_b[(i, g)]
    lo, hi = ranges_b[i]
    predicted = unit_i(lo, hi) * MULTIPLIERS[g]
    rel = abs(published - predicted) / published
    if rel > 0.06:
        worst.append((f">{lo}-{hi}", f"IT{g}", published, round(predicted, 2), f"{rel:.1%}"))

print(f"  偏差 > 6% 的格子：{len(worst)} / {len(common)}")
for row in worst[:12]:
    print("   ", row)

for g in sorted(MULTIPLIERS):
    ratios = [table_b[(i, g)] / unit_i(*ranges_b[i]) for i in range(len(ranges_b))
              if (i, g) in table_b]
    if ratios:
        print(f"  IT{g}: 实测 value/i 落在 {min(ratios):.1f}–{max(ratios):.1f}"
              f"（名义倍率 {MULTIPLIERS[g]}）")

print("\n=== 基本偏差表交叉核对 ===")
source_a = {}
for letter, row in a.SHAFT_UPPER_DEV.items():
    source_a[("shaft", "es", letter)] = row
for letter, row in a.SHAFT_LOWER_DEV.items():
    source_a[("shaft", "ei", letter)] = row
for letter, row in a.HOLE_LOWER_DEV.items():
    source_a[("hole", "ei", letter)] = row
for letter, row in a.HOLE_UPPER_DEV.items():
    source_a[("hole", "es", letter)] = row

source_b = {}
for name, table in (("shaft_es", b._SHAFT_ES), ("shaft_ei", getattr(b, "_SHAFT_EI", {})),
                    ("hole_ei", getattr(b, "_HOLE_EI", {})), ("hole_es", getattr(b, "_HOLE_ES", {}))):
    role, kind = name.split("_")
    for letter, row in table.items():
        source_b[(role, kind, letter.lower())] = row

shared = sorted(set(source_a) & set(source_b), key=str)
print(f"A 有 {len(source_a)} 条，B 有 {len(source_b)} 条，可对比 {len(shared)} 条")
bad = []
for key in shared:
    ra, rb = source_a[key], tuple(source_b[key])
    if len(ra) != len(rb):
        bad.append((key, "长度不同"))
    elif any(x != y for x, y in zip(ra, rb)):
        bad.append((key, [(i, x, y) for i, (x, y) in enumerate(zip(ra, rb)) if x != y]))
print("不一致:", bad if bad else "无 —— 两源逐格一致")
print("A 独有:", sorted(set(source_a) - set(source_b), key=str))
print("B 独有:", sorted(set(source_b) - set(source_a), key=str))

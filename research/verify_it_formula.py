"""验证：ISO 286 标准公差 IT 值能否用公式 i = 0.45·∛D + 0.001·D 推导？

若能 → 数表可程序化生成，M2 大幅省力。
若不能 → 数表必须从标准原文转录 + 逐格断言，不能靠推导。

对照值为 GB/T 1800.1 / ISO 286-1 的公开标准值（H6/H7/H8 孔的基本偏差为 0，
故上偏差数值即 IT 等级公差值）。
"""
import math

# ISO 286 尺寸分段（mm），D 取几何均值
RANGES = [
    (0, 3), (3, 6), (6, 10), (10, 18), (18, 30), (30, 50), (50, 80),
    (80, 120), (120, 180), (180, 250), (250, 315), (315, 400), (400, 500),
]

MULT = {5: 7, 6: 10, 7: 16, 8: 25, 9: 40, 10: 64, 11: 100, 12: 160,
        13: 250, 14: 400, 15: 640, 16: 1000, 17: 1600, 18: 2500}

# 标准表实测值（μm）：{尺寸段: {IT等级: 值}}
REFERENCE = {
    (3, 6):     {6: 8,  7: 12, 8: 19},
    (6, 10):    {6: 8,  7: 15, 8: 22},
    (10, 18):   {6: 11, 7: 18, 8: 27},
    (18, 30):   {6: 13, 7: 21, 8: 33},
    (30, 50):   {6: 16, 7: 25, 8: 39},
    (50, 80):   {6: 19, 7: 30, 8: 46},
    (80, 120):  {6: 22, 7: 35, 8: 54},
    (120, 180): {6: 25, 7: 40, 8: 63},
    (180, 250): {6: 29, 7: 46, 8: 72},
    (250, 315): {6: 33, 7: 52, 8: 81},
    (315, 400): {6: 36, 7: 57, 8: 89},
    (400, 500): {6: 39, 7: 63, 8: 97},
}

# ISO 优先数系（R5 派生），标准规定 IT 值须圆整到该系列
PREFERRED = [1, 1.25, 1.6, 2, 2.5, 3.15, 4, 5, 6.3, 8]


def unit_i(d):
    """IT5–IT18 公差单位 i（μm），d 为尺寸段几何均值（mm）"""
    return 0.45 * d ** (1 / 3) + 0.001 * d


def round_preferred(x):
    """圆整到优先数系（取最接近者）"""
    if x <= 0:
        return 0
    exp = math.floor(math.log10(x))
    best, bestd = None, None
    for k in (exp - 1, exp, exp + 1):
        for m in PREFERRED:
            v = m * (10 ** k)
            dd = abs(v - x)
            if bestd is None or dd < bestd - 1e-12:
                best, bestd = v, dd
    return round(best, 3)


def main():
    print("=" * 84)
    print(f"{'尺寸段':<12}{'IT':<4}{'D几何均值':>10}{'i(μm)':>9}{'n·i':>9}{'圆整':>8}{'标准值':>8}  判定")
    print("-" * 84)
    ok = bad = 0
    misses = []
    for lo, hi in RANGES:
        if (lo, hi) not in REFERENCE:
            continue
        d = math.sqrt(lo * hi)
        i = unit_i(d)
        for grade, ref in sorted(REFERENCE[(lo, hi)].items()):
            raw = i * MULT[grade]
            rounded = round_preferred(raw)
            hit = abs(rounded - ref) < 1e-6
            ok += hit
            bad += (not hit)
            flag = "✓" if hit else f"✗ 差 {rounded - ref:+.2f}"
            if not hit:
                misses.append((f"{lo}-{hi}", grade, ref, raw, rounded))
            print(f"{f'>{lo}–{hi}':<12}{f'IT{grade}':<4}{d:>10.2f}{i:>9.3f}"
                  f"{raw:>9.2f}{rounded:>8.2f}{ref:>8}  {flag}")
    print("=" * 84)
    print(f"命中 {ok} / {ok + bad}")
    if misses:
        print("\n未命中明细（尺寸段, IT, 标准值, 公式原值, 圆整值）:")
        for m in misses:
            print("   ", m)
    print("\n结论候选：")
    print("  · 全命中 → IT 数表可用公式生成，仅需把 ISO 2768/粗糙度等经验值转录")
    print("  · 有未命中 → 圆整规则或分段规则本身是标准的一部分，数表必须转录 + 逐格断言")


if __name__ == "__main__":
    main()

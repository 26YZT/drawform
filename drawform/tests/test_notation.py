"""工程图标注文本语法（ISO 风格，小数用逗号）。

解析结果是有序的 (kind, value) 序列而不是一个多字段结构：真实标注是位置敏感的
（'⌀4 ↧6 ⌴⌀6,5 ↧4' 里两个 ↧ 分属主孔与沉孔），拍平成字段会丢掉这个信息。
认不出的片段一律以 unknown 原样留着，绝不猜含义 —— 'H 12' 至今没有可靠解释。
"""

import pytest

from drawform.rules.notation import parse_fcf, parse_notation


def _kinds(tokens):
    return [t.kind for t in tokens]


def _values(tokens, kind):
    return [t.value for t in tokens if t.kind == kind]


def test_plain_size_dimension():
    tokens = parse_notation("35")
    assert _kinds(tokens) == ["size"]
    assert tokens[0].value == pytest.approx(35.0)


def test_decimal_comma_is_normalized():
    assert parse_notation("22,5")[0].value == pytest.approx(22.5)
    assert parse_notation("25,63")[0].value == pytest.approx(25.63)


def test_plus_minus_tolerance():
    tokens = parse_notation("10 ±0,05")
    assert _kinds(tokens) == ["size", "tolerance"]
    assert _values(tokens, "tolerance") == [pytest.approx(0.05)]


def test_diameter_and_depth_and_counterbore():
    tokens = parse_notation("⌀30 ⌴⌀40 SF ↧5")

    assert _kinds(tokens) == ["diameter", "counterbore", "spotface", "depth"]
    assert _values(tokens, "diameter") == [pytest.approx(30.0)]
    assert _values(tokens, "counterbore") == [pytest.approx(40.0)]
    assert _values(tokens, "depth") == [pytest.approx(5.0)]


def test_depth_order_is_preserved_for_nested_features():
    """'⌀4 ↧6 ⌴⌀6,5 ↧4'：第一个 ↧ 属于主孔，第二个属于沉孔，顺序不能丢。"""
    tokens = parse_notation("⌀4 ↧6 ⌴⌀6,5 ↧4")

    assert _kinds(tokens) == ["diameter", "depth", "counterbore", "depth"]
    assert _values(tokens, "depth") == [pytest.approx(6.0), pytest.approx(4.0)]


def test_counterbore_can_carry_its_own_tolerance():
    tokens = parse_notation("⌀30 ⌴⌀40 ±0,2 SF ↧5")
    assert _values(tokens, "counterbore") == [pytest.approx(40.0)]
    assert _values(tokens, "tolerance") == [pytest.approx(0.2)]


def test_quantity_prefix():
    tokens = parse_notation("2×⌀4,5 ⌴⌀7 ↧3,5")
    assert tokens[0].kind == "count"
    assert tokens[0].value == 2
    assert _kinds(tokens)[1:] == ["diameter", "counterbore", "depth"]


def test_chamfer_and_radius():
    assert _kinds(parse_notation("10×C0,5")) == ["count", "chamfer"]
    assert _values(parse_notation("10×C0,5"), "chamfer") == [pytest.approx(0.5)]
    assert _kinds(parse_notation("3×R1,5")) == ["count", "radius"]
    assert _values(parse_notation("3×R1,5"), "radius") == [pytest.approx(1.5)]


def test_bolt_circle():
    tokens = parse_notation("3×⌀12 EQUALLY SPACED ON ⌀50 PCD")

    assert _values(tokens, "count") == [3]
    assert _values(tokens, "diameter") == [pytest.approx(12.0)]
    assert _values(tokens, "pcd") == [pytest.approx(50.0)]


def test_rectangular_pocket_keeps_two_sizes_in_order():
    tokens = parse_notation("4×7 × 29 ↧2,5")
    assert _values(tokens, "count") == [4]
    assert _values(tokens, "size") == [pytest.approx(7.0), pytest.approx(29.0)]
    assert _values(tokens, "depth") == [pytest.approx(2.5)]


def test_unrecognized_fragment_is_kept_not_guessed():
    """'H 12' 的含义没有可靠来源，只能原样留着并标成 unknown。"""
    tokens = parse_notation("H 12 ±0,05")

    unknown = [t for t in tokens if t.kind == "unknown"]
    assert unknown, "未识别片段被静默丢弃了"
    assert "H" in unknown[0].value
    assert _values(tokens, "tolerance") == [pytest.approx(0.05)]


def test_fcf_without_datum():
    frames = parse_fcf("cylindricity|0,05")
    assert len(frames) == 1
    assert frames[0].symbol == "cylindricity"
    assert frames[0].tolerance == pytest.approx(0.05)
    assert frames[0].datums == ()


def test_fcf_with_diametral_zone_and_datum():
    frames = parse_fcf("perpendicularity|⌀0,05|B\ncylindricity|0,05")

    assert len(frames) == 2
    assert frames[0].symbol == "perpendicularity"
    assert frames[0].zone_is_diametral is True
    assert frames[0].tolerance == pytest.approx(0.05)
    assert [str(d) for d in frames[0].datums] == ["B"]
    assert frames[1].symbol == "cylindricity"
    assert frames[1].zone_is_diametral is False


def test_countersink_carries_diameter_and_included_angle():
    """语料实测：'⌀6 ↧6,55 ⌵⌀12 × 82°' —— ⌵ 是锥形沉孔，× 后是计入角。"""
    tokens = parse_notation("⌀6 ↧6,55 ⌵⌀12 × 82°")

    assert _kinds(tokens) == ["diameter", "depth", "countersink", "angle"]
    assert _values(tokens, "countersink") == [pytest.approx(12.0)]
    assert _values(tokens, "angle") == [pytest.approx(82.0)]


def test_across_flats_marker():
    """A/F = 对边距离（扳手位），是量值后缀而不是独立数值。"""
    tokens = parse_notation("10 ±0,05 A/F")
    assert _kinds(tokens) == ["size", "tolerance", "across_flats"]


def test_bolt_circle_can_carry_its_own_tolerance():
    tokens = parse_notation("4×⌀5 EQUALLY SPACED ON ⌀60 ±0,2 PCD")

    assert _values(tokens, "pcd") == [pytest.approx(60.0)]
    assert _values(tokens, "tolerance") == [pytest.approx(0.2)]
    assert "unknown" not in _kinds(tokens), [t for t in tokens if t.kind == "unknown"]


# --- 真实框格语法。以下每一条都逐字取自 research/nist_gdt_labels.csv，
#     不是照想象编的：旧 parse_fcf 在这批数据上 125 条全抛 ValueError。 ---


def test_a_real_frame_keeps_the_characteric_and_the_zone_apart():
    """真实图纸把特征名与公差带写在同一格（`Position ⌀.015|C|A|B`），
    而旧实现假设第 1 格是数值 —— 于是拿基准字母去 float()。
    """
    frames = parse_fcf("Position ⌀.015|C|A|B")

    assert len(frames) == 1
    assert frames[0].symbol == "Position"
    assert frames[0].tolerance == pytest.approx(0.015)
    assert frames[0].zone_is_diametral is True


def test_the_datum_chain_is_ordered_and_keeps_every_reference():
    """三级基准链 |A|B|C 是有顺序的，单数 datum 字段表达不了 —— 顺序错了图就废了。"""
    frames = parse_fcf("Profile .015|D|HⓂ-JⓂ\nAll Around")

    assert len(frames) == 1
    frame = frames[0]
    assert [str(d) for d in frame.datums] == ["D", "HⓂ-JⓂ"]
    assert frame.attach == ("All Around",)


def test_modifiers_on_the_zone_are_kept_not_guessed():
    """Ⓜ（最大实体）、Ⓟ（投影带）这些圈号修饰符我们**没有可靠释义**，
    只能原样留着：猜错一个修饰符比不解释更危险。
    """
    frames = parse_fcf("Position ⌀.025Ⓜ|A|B|C")

    assert frames[0].tolerance == pytest.approx(0.025)
    assert frames[0].zone_modifiers == ("Ⓜ",)


def test_spherical_tolerance_zone_is_not_a_plain_diameter():
    """`S⌀` 是球面直径公差带。当成普通 ⌀ 会丢掉"球"这个信息。"""
    frames = parse_fcf("Position S⌀.025|D|B|C")

    assert frames[0].zone_is_spherical is True
    assert frames[0].tolerance == pytest.approx(0.025)


def test_two_frames_on_one_cell_split_by_newline():
    frames = parse_fcf("Profile Surface .05|D|B|C\nProfile Surface .01|D")

    assert [f.tolerance for f in frames] == [pytest.approx(0.05), pytest.approx(0.01)]
    assert [len(f.datums) for f in frames] == [3, 1]


def test_a_frame_we_cannot_understand_is_kept_raw_instead_of_raising():
    """认不出的框格必须原样留着并标出解析失败，绝不能抛 ——
    它在生产里由 eval/corpus.py 调用，一条读不懂的注记不该让整批语料跑挂。
    """
    frames = parse_fcf("Straightness 0.2 / 15")

    assert len(frames) == 1
    assert frames[0].tolerance is None
    assert frames[0].raw == "Straightness 0.2 / 15"


def test_the_parser_survives_every_real_row_it_meets():
    """整份真实标签表跑一遍：不许抛异常，且绝大多数要解出公差值。
    这条是防"修好六个样例、第七个又崩"的兜底。
    """
    import csv
    from pathlib import Path

    table = Path(__file__).resolve().parents[2] / "research" / "nist_gdt_labels.csv"
    if not table.is_dir():
        rows = list(csv.DictReader(open(table, encoding="utf-8")))
    texts = [(r["target_text"] or "").strip() for r in rows]
    with_pipes = [t for t in texts if "|" in t]

    assert with_pipes, "标签表里没有带竖线的标注，这条测试测不到东西"
    frames = []
    for text in with_pipes:
        frames.extend(parse_fcf(text))          # 不许抛

    parsed = sum(1 for f in frames if f.tolerance is not None)
    assert parsed >= 0.9 * len(frames), \
        f"{len(frames)} 个框格里只解出 {parsed} 个公差值，模型仍不够用"

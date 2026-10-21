"""读取 cadsy/cad-technical-drawings 语料：STEP ↔ 带标注图纸的配对样本。

语料由 Cadsy 从 Zero-to-CAD-100k 的 STEP 自动生成，Apache-2.0。
它是「合成参照」而不是专家金标准：对齐它等于对齐 Cadsy 的画法策略，
能判视图选择、尺寸选与放、标注语法、几何量测，不能判设计意图。
"""

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from drawform.eval.corpus import load_corpus, load_sample


def _write_sample(root: Path, name: str, annotations, *, with_step=True) -> Path:
    d = root / "samples" / name
    d.mkdir(parents=True)
    (d / "iso_labels.json").write_text(
        json.dumps({"annotations": annotations}, ensure_ascii=False), encoding="utf-8")
    if with_step:
        (d / "model.step").write_text("ISO-10303-21;", encoding="utf-8")
    return root


def test_reads_annotation_records_with_normalized_fields(tmp_path):
    _write_sample(tmp_path, "part_0001", [
        {"type": "dimension", "view": "front", "text": "35",
         "box": [0.1, 0.2, 0.3, 0.4]},
        {"type": "callout", "view": "plan", "text": "⌀30 ⌀40 SF 5",
         "box": [0.5, 0.6, 0.7, 0.8]},
    ])

    sample = load_sample(tmp_path / "samples" / "part_0001")

    assert sample.sample_id == "part_0001"
    assert [a.type for a in sample.annotations] == ["dimension", "callout"]
    assert sample.annotations[0].view == "front"
    assert sample.annotations[0].box == pytest.approx((0.1, 0.2, 0.3, 0.4))


def test_annotation_text_is_parsed_through_the_notation_grammar(tmp_path):
    _write_sample(tmp_path, "part_0002", [
        {"type": "callout", "view": "plan", "text": "2×⌀4,5 ⌴⌀7 ↧3,5",
         "box": [0, 0, 1, 1]},
    ])

    sample = load_sample(tmp_path / "samples" / "part_0002")

    kinds = [t.kind for t in sample.annotations[0].tokens]
    assert kinds == ["count", "diameter", "counterbore", "depth"]


def test_feature_control_frames_are_parsed_into_structures(tmp_path):
    _write_sample(tmp_path, "part_0003", [
        {"type": "feature_control_frame", "view": "front",
         "text": "perpendicularity|⌀0,05|B\ncylindricity|0,05",
         "box": [0, 0, 1, 1]},
    ])

    frame = load_sample(tmp_path / "samples" / "part_0003").annotations[0].frames

    assert len(frame) == 2
    assert frame[0].symbol == "perpendicularity"
    assert [str(d) for d in frame[0].datums] == ["B"]
    assert frame[1].symbol == "cylindricity"


def test_corpus_skips_samples_without_a_step_model(tmp_path):
    """下载中断或仓库本身缺件时，没有 model.step 的目录不能当有效样本混进来。"""
    _write_sample(tmp_path, "part_0001", [{"type": "dimension", "view": "front",
                                           "text": "10", "box": [0, 0, 1, 1]}])
    _write_sample(tmp_path, "part_0002", [], with_step=False)

    corpus = load_corpus(tmp_path)

    assert [s.sample_id for s in corpus] == ["part_0001"]


def test_corpus_sorts_samples_deterministically(tmp_path):
    for name in ("part_0010", "part_0002", "part_0001"):
        _write_sample(tmp_path, name, [])

    assert [s.sample_id for s in load_corpus(tmp_path)] == [
        "part_0001", "part_0002", "part_0010"]


def test_notation_is_parsed_only_where_the_grammar_applies(tmp_path):
    """标题栏文字与基准字母不属于标注语法，套上去只会产出成百上千条假 unknown。"""
    _write_sample(tmp_path, "part_0004", [
        {"type": "title_block_value", "view": "sheet", "text": "ISO 2768-f",
         "box": [0, 0, 1, 1]},
        {"type": "datum_definition", "view": "front", "text": "B", "box": [0, 0, 1, 1]},
        {"type": "label", "view": "sheet", "text": "A", "box": [0, 0, 1, 1]},
        {"type": "dimension", "view": "front", "text": "35", "box": [0, 0, 1, 1]},
        {"type": "callout", "view": "plan", "text": "⌀30", "box": [0, 0, 1, 1]},
    ])

    sample = load_sample(tmp_path / "samples" / "part_0004")

    assert sample.annotations[0].tokens == ()
    assert sample.annotations[1].tokens == ()
    assert sample.annotations[2].tokens == ()
    assert [t.kind for t in sample.annotations[3].tokens] == ["size"]
    assert [t.kind for t in sample.annotations[4].tokens] == ["diameter"]


def test_generated_counterbore_dof_lands_in_the_counterbore_pool():
    """方案里的沉孔直径必须进沉孔池 —— 真实标注的 `⌴⌀6,5` 就在那一池。

    放错池的话两侧各错一次：沉孔池永远 0 命中，直径池白白多一条超发。
    """
    from drawform.dimensioning.constraints import Plan, Sized
    from drawform.eval.score import generated_value_groups

    plan = Plan(constraints=(Sized("cbore-1", "counterbore", 6.5),
                             Sized("pilot-1", "diameter", 4.0)))

    groups = generated_value_groups(plan)

    assert groups["counterbore"] == [6.5]
    assert groups["diameter"] == [4.0]
    assert groups["linear"] == []


def test_match_values_counts_duplicates_once_each():
    """重复值不能被一条生成值虚增成两条命中。"""
    from drawform.eval.score import match_values

    assert match_values([35.0, 35.0], [35.0]) == 1
    assert match_values([35.0, 35.0], [35.0, 35.0]) == 2


def test_match_values_uses_the_display_tolerance():
    from drawform.eval.score import match_values

    assert match_values([22.50], [22.504]) == 1
    assert match_values([22.50], [22.60]) == 0


def test_score_groups_judges_distinct_values_not_how_often_they_are_written():
    """真实图纸把同一个 ⌀70 写两次，是两个表面上的同一个名义尺寸，不是两个要求。

    65 个真实零件实测：多重集口径下这类重复书写占全部真实标注值的 30.8%（284/923，
    其中 linear 池占 249），而记为「漏标」的 387 条里有 170 条（44%）其实值已经覆盖。
    继续用多重集等于罚我们不照抄对方的版式，还会奖励多标。
    """
    from drawform.eval.score import score_groups

    score = score_groups({"linear": [15.0, 15.0, 15.0]}, {"linear": [15.0]})

    assert score.real_total == 1
    assert score.generated_total == 1
    assert score.matched == 1


def test_repeated_value_we_emit_too_few_times_is_reported_not_scored():
    """值覆盖但次数不足（8 个孔都定位在 15，我们只标了一个）必须**看得见**，
    但不能混进 recall —— 混进去就变成"多标才得高分"的错误激励。
    """
    from drawform.eval.score import score_groups

    score = score_groups({"linear": [15.0] * 8}, {"linear": [15.0]})

    assert score.recall == 1.0
    assert score.shortfall == 1


def test_emitting_a_value_more_times_than_the_drawing_does_is_surplus_only():
    """我们比图纸多写同一个值，不额外扣 recall，也只按一个值算进 precision。"""
    from drawform.eval.score import score_groups

    score = score_groups({"linear": [15.0]}, {"linear": [15.0, 15.0]})

    assert score.matched == 1
    assert score.generated_total == 1
    assert score.recall == 1.0


def test_value_bucketing_uses_the_display_tolerance():
    """同一侧的 22,50 与 22.504 是同一个名义尺寸的不同书写，算一个值。

    没有这条，去重会退化成按浮点精确去重，图纸上的逗号/尾零差异会被当成两个要求。
    """
    from drawform.eval.score import score_groups

    score = score_groups({"linear": [22.50, 22.504, 22.60]}, {"linear": [22.50]})

    assert score.real_total == 2
    assert score.matched == 1


def test_match_values_does_not_pair_far_apart_values():
    from drawform.eval.score import match_values

    assert match_values([10.0, 90.0], [50.0]) == 0


def test_score_reports_recall_and_precision_separately():
    from drawform.eval.score import Score

    score = Score("p", matched=4, real_total=8, generated_total=16)

    assert score.recall == 0.5
    assert score.precision == 0.25


def test_matching_is_scoped_by_kind_not_bare_number():
    """直径 9 不能因为图里另有一个「深度 9」就算命中。

    只按裸数值配对会让巧合数字白送分，实测能把 precision 虚高十几个点。
    """
    from drawform.eval.score import score_groups

    real = {"diameter": [], "linear": [9.0], "pcd": []}
    generated = {"diameter": [9.0], "linear": [], "pcd": []}

    assert score_groups(real, generated).matched == 0


def test_matching_still_counts_a_real_diameter_hit():
    from drawform.eval.score import score_groups

    real = {"diameter": [9.0], "linear": [], "pcd": []}
    generated = {"diameter": [9.0], "linear": [], "pcd": []}

    assert score_groups(real, generated).matched == 1


def test_real_values_are_split_by_what_they_measure():
    """⌀ 开头的量的是直径，其余线性尺寸走 linear，两者不可互撞。"""
    from drawform.eval.corpus import Annotation
    from drawform.eval.score import real_value_groups
    from drawform.rules.notation import parse_notation

    annotations = [
        Annotation(type="callout", view="plan", text="⌀30", box=(0, 0, 1, 1),
                   tokens=parse_notation("⌀30")),
        Annotation(type="dimension", view="front", text="35", box=(0, 0, 1, 1),
                   tokens=parse_notation("35")),
    ]

    groups = real_value_groups(annotations)

    assert groups["diameter"] == [30.0]
    assert groups["linear"] == [35.0]


def test_pcd_values_are_not_thrown_away_or_pooled_with_linears():
    """真实图纸里的 PCD 值必须自成一池。

    早先 real_value_groups 只收 size/diameter，把 PCD 直接丢掉，而我生成的 pcd
    被塞进 linear 池去碰运气 —— 结果是 16/16 全对的螺栓圆被判成 6% 命中。
    """
    from drawform.eval.corpus import Annotation
    from drawform.eval.score import real_value_groups
    from drawform.rules.notation import parse_notation

    annotations = [Annotation(type="callout", view="plan",
                              text="4×⌀5 EQUALLY SPACED ON ⌀60 PCD",
                              box=(0, 0, 1, 1),
                              tokens=parse_notation("4×⌀5 EQUALLY SPACED ON ⌀60 PCD"))]

    groups = real_value_groups(annotations)

    assert groups["pcd"] == [60.0]
    assert groups["diameter"] == [5.0]
    assert groups["linear"] == []


def test_generated_pcd_matches_against_real_pcd():
    from drawform.eval.score import score_groups

    real = {"diameter": [], "linear": [], "pcd": [60.0]}
    generated = {"diameter": [], "linear": [], "pcd": [60.0]}

    assert score_groups(real, generated).matched == 1


def test_generated_pcd_lands_in_its_own_group():
    """生成侧的 pcd 自由度必须落进 pcd 池，落进 linear 就永远配不上。"""
    from drawform.dimensioning.constraints import Plan, Sized
    from drawform.eval.score import generated_value_groups

    plan = Plan(constraints=(Sized("g1", "diameter", 5.0, quantity=4),
                             Sized("g1", "pcd", 60.0, quantity=4),
                             Sized("outline", "length", 80.0)))

    groups = generated_value_groups(plan)

    assert groups["pcd"] == [60.0]
    assert groups["diameter"] == [5.0]
    assert groups["linear"] == [80.0]


def test_real_dimension_pools_cover_every_quantity_kind():
    """深度、倒角、圆角、沉孔、角度都是「被标出来的量」，不能因为基线还不产就算不进分母。

    早先只收 diameter/size/pcd，把 42 条深度、36 条倒角、20 条沉孔、17 条圆角全扔了，
    等于只在自己可能命中的子集上打分，recall 被系统性高估。
    """
    from drawform.eval.corpus import Annotation
    from drawform.eval.score import real_value_groups
    from drawform.rules.notation import parse_notation

    texts = ["⌀6 ↧6,55 ⌵⌀12 × 82°", "10×C0,5", "2×R5", "⌀30 ⌴⌀40 SF ↧5", "35"]
    annotations = [Annotation(type="callout", view="plan", text=t, box=(0, 0, 1, 1),
                              tokens=parse_notation(t)) for t in texts]

    groups = real_value_groups(annotations)

    assert groups["depth"] == [6.55, 5.0]
    assert groups["chamfer"] == [0.5]
    assert groups["radius"] == [5.0]
    assert groups["counterbore"] == [40.0]
    assert groups["countersink"] == [12.0]
    assert groups["angle"] == [82.0]
    assert groups["diameter"] == [6.0, 30.0]
    assert groups["linear"] == [35.0]


def test_modifiers_are_not_counted_as_dimensions():
    """数量前缀、公差、SF、A/F 是修饰语，不是被标出来的量，不进分母。"""
    from drawform.eval.corpus import Annotation
    from drawform.eval.score import real_value_groups
    from drawform.rules.notation import parse_notation

    texts = ["4×⌀5 ±0,05", "10 ±0,05 A/F", "⌀30 ⌴⌀40 SF ↧5"]
    annotations = [Annotation(type="callout", view="plan", text=t, box=(0, 0, 1, 1),
                              tokens=parse_notation(t)) for t in texts]

    groups = real_value_groups(annotations)
    collected = [v for pool in groups.values() for v in pool]

    assert 0.05 not in collected      # 公差是修饰语
    assert 4 not in collected         # `4×` 是数量前缀，不是被标的量
    assert groups["linear"] == [10.0]  # `10 ±0,05 A/F` 里的 10 才是真尺寸
    assert set(groups["diameter"]) == {5.0, 30.0}
    assert groups["counterbore"] == [40.0]   # ⌴⌀40 是沉孔直径，另一类量
    assert groups["depth"] == [5.0]


def test_generated_depth_matches_real_depth_not_a_random_linear():
    from drawform.dimensioning.constraints import Plan, Sized
    from drawform.eval.score import generated_value_groups

    plan = Plan(constraints=(Sized("g1", "diameter", 5.0), Sized("g1", "depth", 12.0),
                             Sized("outline", "length", 80.0)))

    groups = generated_value_groups(plan)

    assert groups["depth"] == [12.0]
    assert groups["linear"] == [80.0]


def test_every_notation_token_kind_is_classified_for_scoring():
    """解析器能产出的每一种 token 都必须被显式归类，否则会被静默扔出评分。

    pcd、depth、chamfer 连着三次都是这么丢的 —— 分母悄悄变小，recall 悄悄变高。
    这条守卫让「新增一种 token 却忘了归类」当场失败。
    """
    from drawform.eval.score import MODIFIER_TOKENS, REAL_TOKEN_GROUP
    from drawform.rules.notation import PATTERNS

    kinds = {kind for kind, _ in PATTERNS} - {"sep"}

    unclassified = kinds - set(REAL_TOKEN_GROUP) - set(MODIFIER_TOKENS)
    assert not unclassified, f"这些 token 类型既不算尺寸也不算修饰语，会被静默丢弃：{unclassified}"

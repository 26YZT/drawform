"""壁厚/台阶尺寸的价值与风险，一次量清楚。

三个问题：
1. 发出「面到面间隙」对 recall 有多少真实增益，代价是多少噪声；
2. 用一条有依据的排除规则能不能把噪声压下来；
3. 它们会不会和已有的外廓总尺寸拼成封闭尺寸链 —— 以及我们自己的 linter 查不查得到。

走的是生产代码 `find_gaps`（相邻轴平行平面 + 排除等于外廓那一对，`solid` 只作标记不作筛选），
不再在这里另写一份判据。早先这脚本自己写过 all-pairs 版本，把 159 条量成了 490 条，
教训是**探针必须和生产用同一份实现**，否则量出来的代价是假的。
"""

from __future__ import annotations

from pathlib import Path

from drawform.dimensioning.scheme import derive_scheme
from drawform.eval.corpus import load_corpus
from drawform.eval.score import generated_value_groups, real_value_groups, score_groups
from drawform.geometry.features import extract_features, find_gaps, planar_faces
from drawform.geometry.loader import import_step

CORPUS = Path(__file__).resolve().parents[1] / "corpus" / "cadsy"
AXIS_INDEX = {"x": 0, "y": 1, "z": 2}
RATIO = 0.20


def variants(model):
    """三种发法，都走生产代码 `find_gaps`（相邻轴平行平面 + 排除等于外廓那一对）。

    「只发实心怀」曾设想把空腔剔掉，实测剔掉亏 1.0pt F2 —— 槽宽本来就是真图纸会标的
    尺寸，所以 `solid` 只做标记，不做筛选。
    「坐标式」是按 GB/T 的基准注法发的：每个内表面的位置从基准面量，不发段长。
    它与总尺寸**按构造拼不出封闭链**（总数 + k 个坐标比段数少一条方程），
    代价是写出来的数字和链式注法不同，所以对分未必一样好看 —— 这正是本探针要量的。
    """
    gaps = [g for g in find_gaps(model.shape, model.bbox)
            if g.gap <= RATIO * max(model.bbox)]

    def tilings():
        for axis in "xyz":
            run, cursor, links = [], 0.0, []
            for g in sorted((g for g in gaps if g.axis == axis), key=lambda g: g.low):
                if abs(g.low - cursor) < 0.02:
                    cursor = g.high
                    links.append(g)
                    run.append(round(g.gap, 3))
            if len(run) >= 2 and abs(cursor - model.bbox[AXIS_INDEX[axis]]) < 0.02:
                yield axis, links

    # GB/T 的常规做法：链式发，但每条完整铺满外廓的链**留一环不标**（或降级成参考尺寸）。
    # 取最大那一环 —— 它的累积误差本来就最大，工程上也最该由总尺寸保证。
    dropped = {id(g) for _, links in tilings() for g in [max(links, key=lambda g: g.gap)]}
    legal = [round(g.gap, 3) for g in gaps if id(g) not in dropped]

    faces = planar_faces(model.shape)
    coordinates = []
    for axis, index in AXIS_INDEX.items():
        positions = sorted({round(f.position, 6) for f in faces if f.axis == axis})
        for position in positions:
            relative = position - model.origin[index]
            if relative <= 1e-6 or abs(relative - model.bbox[index]) < 1e-6:
                continue  # 两个端面就是外廓本身，总尺寸已覆盖
            if any(abs(g.low - position) < 1e-6 or abs(g.high - position) < 1e-6
                   for g in gaps if g.axis == axis):
                coordinates.append(round(relative, 3))
    return {"链式（相邻段长）": [round(g.gap, 3) for g in gaps],
            "链式·只发实心怀": [round(g.gap, 3) for g in gaps if g.solid],
            "链式·每链留一环不标": legal,
            "坐标式（从基准面量）": coordinates}


def f2(matched, real_total, generated_total):
    recall = matched / real_total if real_total else 0.0
    precision = matched / generated_total if generated_total else 0.0
    weight = 4.0  # STEERING_BETA = 2.0 → β² = 4
    denominator = weight * precision + recall
    return ((1 + weight) * precision * recall / denominator if denominator else 0.0,
            recall, precision)


def main() -> None:
    scores = {}
    chains = []
    nparts = 0
    for sample in load_corpus(CORPUS):
        try:
            model = import_step(sample.directory / "model.step")
            if not model.is_valid:
                continue
            features = extract_features(model.shape, model.bbox)
            scheme = derive_scheme(model, features)
        except Exception as error:
            print(f"  跳过 {sample.sample_id}: {type(error).__name__}")
            continue
        nparts += 1
        real = real_value_groups(sample.annotations)
        base = generated_value_groups(scheme.plan)

        for name, values in variants(model).items():
            merged = {group: list(v) for group, v in base.items()}
            merged["linear"] = merged["linear"] + values
            score = score_groups(real, merged)
            entry = scores.setdefault(name, [0, 0, 0, 0, 0])
            entry[0] += score.matched
            entry[1] += score.real_total
            entry[2] += score.generated_total
            entry[3] += len(values)
            entry[4] += 1 if values else 0

        anchor = scores.setdefault("不发壁厚(基线)", [0, 0, 0, 0, 0])
        score = score_groups(real, base)
        anchor[0] += score.matched
        anchor[1] += score.real_total
        anchor[2] += score.generated_total

        gaps = [g for g in find_gaps(model.shape, model.bbox)
                if g.gap <= RATIO * max(model.bbox)]
        for axis in "xyz":
            run, cursor = [], 0.0
            for g in sorted((g for g in gaps if g.axis == axis), key=lambda g: g.low):
                if abs(g.low - cursor) < 0.02:
                    cursor = g.high
                    run.append(round(g.gap, 3))
            total = model.bbox[AXIS_INDEX[axis]]
            if len(run) >= 2 and abs(cursor - total) < 0.02:
                chains.append((sample.sample_id, axis, run, round(total, 3)))

    print(f"\n有效零件 {nparts}")
    for name, (matched, real_total, generated, emitted, parts) in scores.items():
        value, recall, precision = f2(matched, real_total, generated)
        print(f"{name:<12} 发出 {emitted:>4} 条（{parts} 件有产出，"
              f"平均 {emitted / max(parts, 1):.1f}/件）  "
              f"recall {recall:.1%}  precision {precision:.1%}  F2 {value:.1%}")

    print(f"\n与外廓拼成封闭链：{len(chains)} 处")
    for spot in chains[:12]:
        print("  ", spot)


if __name__ == "__main__":
    main()

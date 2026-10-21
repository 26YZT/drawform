#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""在真实语料上重跑一遍全链路，产出项目文档唯一允许引用的那组数字。

文档里的每个指标都必须能由本脚本重现；不能重现的数字一律不许写进文档。
用法：
    cd drawform && PYTHONPATH=src ../spike/.venv/bin/python ../research/measure_corpus_baseline.py
"""
from __future__ import annotations

import sys
import time
from collections import Counter
from pathlib import Path

from drawform.delivery import gate_for, unplaced_dimensions
from drawform.dimensioning.constraints import Sized
from drawform.dimensioning.place import place_scheme
from drawform.dimensioning.scheme import derive_scheme
from drawform.eval.corpus import load_corpus
from drawform.eval.score import (DOF_GROUP, GROUPS, ABS_TOL, STEERING_BETA, Score,
                               _buckets, _pair,
                                 generated_value_groups, real_value_groups)
from drawform.geometry.features import extract_features
from drawform.geometry.loader import import_step
from drawform.geometry.projection import FRONT, LEFT, TOP, project_view

CORPUS = Path(__file__).resolve().parents[1] / "corpus" / "cadsy"


def measure(step_path: Path, annotations):
    model = import_step(step_path)
    load_gate = gate_for(model)
    features = extract_features(model.shape, model.bbox)
    scheme = derive_scheme(model, features)

    projections = {d.name: project_view(model.shape, d) for d in (FRONT, TOP, LEFT)}
    dims = place_scheme(scheme, projections)
    verdict = gate_for(model, scheme, dims)

    real = real_value_groups(annotations)
    gen = generated_value_groups(scheme.plan)

    pools = {}
    for group in GROUPS:
        rb = _buckets(real.get(group, ()))
        gb = _buckets(gen.get(group, ()))
        pairs = _pair(rb, gb, ABS_TOL)
        short = sum(1 for rv, gv in pairs if rb[rv] > gb[gv])
        pools[group] = (len(rb), len(gb), len(pairs), short)

    planned = sum(1 for c in scheme.plan.constraints if isinstance(c, Sized))
    missing = unplaced_dimensions(scheme, dims)
    return dict(pools=pools, planned=planned, drawn=len(dims), covered=planned-len(missing),
                blocked=not verdict.allowed,
                failed=verdict.failed,
                unplaced=len(missing),
                missing=[dof for _, dof in missing])



def main() -> int:
    samples = list(load_corpus(CORPUS))
    if not samples:
        print(f"语料不存在：{CORPUS}/samples", file=sys.stderr)
        return 2

    agg = {g: [0, 0, 0, 0] for g in GROUPS}
    planned = drawn = covered = blocked = 0
    fail_ids: Counter = Counter()
    unplaced = 0
    by_dof: Counter = Counter()
    errors = []

    started = time.time()
    for index, sample in enumerate(samples, 1):
        try:
            r = measure(sample.directory / "model.step", sample.annotations)
        except Exception as exc:
            errors.append((sample.sample_id, repr(exc)))
            continue
        for group, values in r["pools"].items():
            for slot, value in enumerate(values):
                agg[group][slot] += value
        planned += r["planned"]
        drawn += r["drawn"]
        covered += r["covered"]
        unplaced += r["unplaced"]
        by_dof.update(r["missing"])
        blocked += int(r["blocked"])
        fail_ids.update(r["failed"])
        print(f"[{index}/{len(samples)}] {sample.sample_id} "
              f"planned={r['planned']} drawn={r['drawn']}"
              f"{' BLOCK ' + ','.join(r['failed']) if r['blocked'] else ''}",
              flush=True)

    matched = sum(v[2] for v in agg.values())
    real_total = sum(v[0] for v in agg.values())
    gen_total = sum(v[1] for v in agg.values())
    shortfall = sum(v[3] for v in agg.values())
    recall = matched / real_total if real_total else 0.0
    precision = matched / gen_total if gen_total else 0.0
    # 走 Score.f() 而不是在这里再写一遍公式：口径分两处算，迟早算出两个数，
    # 而文档引用的数字必须能由这个脚本原样重现。
    total = Score(sample_id="", matched=matched, real_total=real_total,
                  generated_total=gen_total)

    print(f"\n===== 全库（去重口径，{len(samples)} 件 cadsy 合成参照；非人工工程图验收）=====")
    print(f"参与评分零件 {len(samples) - len(errors)} / {len(samples)}，"
          f"跑批失败 {len(errors)}")
    print(f"⭐ 判定口径 F{STEERING_BETA:g} = {total.f():.1%}"
          f"（漏标按 β²={STEERING_BETA ** 2:.0f} 倍代价计）")
    print(f"   历史口径 F1 = {total.f(1.0):.1%}（仅供与旧记录对比，不要再当进步依据）")
    print(f"recall {recall:.1%}  precision {precision:.1%}")
    print(f"漏标 {real_total - matched} 条 ／ 多标 {gen_total - matched} 条")
    print(f"真实去重量 {real_total} / 产出去重量 {gen_total} / 命中 {matched} / "
          f"值已覆盖但书写不足 {shortfall}（只报告不计分）")

    print("\n----- 分池 -----")
    for group in GROUPS:
        r, g, m, s = agg[group]
        rec = f"{m / r:.0%}" if r else "—"
        pre = f"{m / g:.0%}" if g else "—"
        print(f"{group:<12} 真实 {r:>4}  产出 {g:>4}  命中 {m:>4}  "
              f"漏 {r - m:>4}  多 {g - m:>4}  recall {rec:>4}  precision {pre:>4}"
              f"  shortfall {s}")

    print("\n----- 落位与交付门 -----")
    print(f"方案发出 {planned} 条，图上标注 {drawn} 条，覆盖方案来源 {covered} 条，"
          f"落位覆盖 {covered / planned:.1%}" if planned else "无方案产出")
    print(f"交付门拦下 {blocked} 件；未落位尺寸累计 {unplaced} 条")
    print("未落位按自由度：", dict(by_dof.most_common()))
    for key, count in fail_ids.most_common():
        print(f"  {key or '(无 id)'}: {count}")

    if errors:
        print("\n----- 跑批失败（必须逐条进 Bad case，不许静默跳过）-----")
        for sid, exc in errors:
            print(f"  {sid}: {exc}")

    print(f"\n耗时 {time.time() - started:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())

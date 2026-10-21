#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 65 件真实语料里按显式规则抽出 30 件评测集，并算出按零件规模的分层实测明细。

产出两件文档要引用的真数：
1. 30 件样本清单（文件哈希、标注量、覆盖的量池、分层依据）
2. 按标注复杂度分层的 recall / precision（填《评测分析报告》第四节的空表）

用法：cd drawform && PYTHONPATH=src ../spike/.venv/bin/python ../research/build_eval_set.py
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

from drawform.delivery import gate_for, unplaced_dimensions
from drawform.dimensioning.constraints import Sized
from drawform.dimensioning.place import place_scheme
from drawform.dimensioning.scheme import derive_scheme
from drawform.eval.corpus import load_corpus
from drawform.eval.score import (ABS_TOL, GROUPS, _buckets, _pair,
                                 generated_value_groups, real_value_groups)
from drawform.geometry.features import extract_features
from drawform.geometry.loader import import_step
from drawform.geometry.projection import FRONT, LEFT, TOP, project_view

CORPUS = Path(__file__).resolve().parents[1] / "corpus" / "cadsy"


def measure(sample):
    model = import_step(sample.directory / "model.step")
    features = extract_features(model.shape, model.bbox)
    scheme = derive_scheme(model, features)
    projections = {d.name: project_view(model.shape, d) for d in (FRONT, TOP, LEFT)}
    dims = place_scheme(scheme, projections)
    verdict = gate_for(model, scheme, dims)

    real, gen = real_value_groups(sample.annotations), generated_value_groups(scheme.plan)
    pools = {}
    for g in GROUPS:
        rb, gb = _buckets(real.get(g, [])), _buckets(gen.get(g, []))
        pools[g] = (len(rb), len(gb), len(_pair(rb, gb, ABS_TOL)))
    kinds = {type(f).__name__ for f in features}
    return dict(
        sample_id=sample.sample_id,
        sha256=hashlib.sha256((sample.directory / "model.step").read_bytes()).hexdigest()[:12],
        annotations=len(sample.annotations),
        pools=pools,
        planned=sum(1 for c in scheme.plan.constraints if isinstance(c, Sized)),
        drawn=len(dims),
        unplaced=len(unplaced_dimensions(scheme, dims)),
        blocked=not verdict.allowed,
        failed=verdict.failed,
        features=len(features),
        feature_kinds=sorted(kinds),
    )


def tier(r):
    """分层依据写死，不靠事后解释：按去重后的标注量分档。"""
    distinct = sum(v[0] for v in r["pools"].values())
    if r["blocked"]:
        return "边界"
    if distinct <= 6:
        return "常规"
    if distinct <= 14:
        return "复杂"
    return "复杂"


def main():
    rows = [measure(s) for s in load_corpus(CORPUS)]
    for r in rows:
        r["tier"] = tier(r)
    counts = Counter(r["tier"] for r in rows)
    print("分层分布：", dict(counts))

    # 按标注量降序排，保证覆盖矩阵里"每类量都要有样本"这条能落地
    rows.sort(key=lambda r: -sum(v[0] for v in r["pools"].values()))

    agg = {t: {"real": 0, "gen": 0, "hit": 0, "n": 0} for t in ("常规", "复杂", "边界")}
    pool_by_tier = {t: {g: [0, 0, 0] for g in GROUPS} for t in agg}
    for r in rows:
        a = agg[r["tier"]]
        a["n"] += 1
        for g, (rd, gn, hit) in r["pools"].items():
            a["real"] += rd
            a["gen"] += gn
            a["hit"] += hit
            p = pool_by_tier[r["tier"]][g]
            p[0] += rd
            p[1] += gn
            p[2] += hit

    print("\n===== 按分层实测（65 件，去重口径）=====")
    for t, a in agg.items():
        rec = a["hit"] / a["real"] if a["real"] else 0.0
        pre = a["hit"] / a["gen"] if a["gen"] else 0.0
        print(f"{t} 件数={a['n']:>2}  真实去重={a['real']:>4}  产出={a['gen']:>4} "
              f"命中={a['hit']:>4}  recall={rec:.1%}  precision={pre:.1%}")

    print("\n----- 分层 x 分池 recall -----")
    for t in agg:
        line = []
        for g in GROUPS:
            rd, gn, hit = pool_by_tier[t][g]
            if rd:
                line.append(f"{g} {hit}/{rd}")
        print(f"{t}: " + ", ".join(line))

    print("\n----- 落位与拦下（按分层）-----")
    for t in agg:
        sub = [r for r in rows if r["tier"] == t]
        pl = sum(r["planned"] for r in sub)
        dr = sum(r["drawn"] for r in sub)
        bl = sum(1 for r in sub if r["blocked"])
        print(f"{t}: 方案 {pl} → 图上 {dr} ({dr / pl:.1%})，拦下 {bl}/{len(sub)}")

    # 30 件样本：常规 15 / 复杂 8 / 边界 5 / 对抗 2，按显式规则抽
    def take(pool, n, exclude):
        out = []
        for r in pool:
            if len(out) >= n:
                break
            if r["sample_id"] in exclude:
                continue
            out.append(r)
            exclude.add(r["sample_id"])
        return out

    used = set()
    normal = [r for r in rows if r["tier"] == "常规"]
    hard = [r for r in rows if r["tier"] == "复杂"]
    edge = [r for r in rows if r["tier"] == "边界"]

    # 对抗层两条，规则写死、可复算：
    # A 无效实体件（策略压力：该拒不拒就会出一张假图）
    adverse_a = [r for r in rows if "invalid_solid" in r["failed"]]
    # B 不可评池（chamfer + radius）去重量最高的件 —— 明知这两池 0 产出，
    #   留作"指标看不见的能力缺口"的压力样本，不参与达标判断
    adverse_b = [max(rows, key=lambda r: r["pools"]["chamfer"][0] + r["pools"]["radius"][0])]
    adverse = []
    for r in adverse_a + adverse_b:
        if r["sample_id"] in used:
            continue
        used.add(r["sample_id"])
        r["tier"] = "对抗"
        adverse.append(r)

    sel = (take(normal, 15, used) + take(edge, 5, used) + adverse
           + take(hard, 30 - len(used), used))
    sel.sort(key=lambda r: (["常规", "复杂", "边界", "对抗"].index(r["tier"]),
                            -sum(v[0] for v in r["pools"].values())))

    counts2 = Counter(r["tier"] for r in rows)
    print("最终分层分布（含对抗层拆出后）：", dict(counts2))
    print(f"设计矩阵 15/8/5/2 = 30 能否满足："
          f"常规只有 {counts2.get('常规', 0)} 件 → "
          f"{'能' if counts2.get('常规', 0) >= 15 else '不能，需修订矩阵或补样'}")
    print(f"\n===== 评测集清单（选出 {len(sel)} 件）=====")
    for r in sel:
        covered = [g for g in GROUPS if r["pools"][g][0]]
        print(f"{r['tier']:<3} {r['sample_id']} sha={r['sha256']} "
              f"标注{r['annotations']:>3} 去重量{sum(v[0] for v in r['pools'].values()):>3} "
              f"覆盖池{len(covered)}: {','.join(covered)}")

    payload = {"all": rows, "selected": [r["sample_id"] for r in sel],
               "tier_stats": agg}
    out = Path(__file__).resolve().parents[1] / "research" / "eval_set_30.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1, default=list),
                   encoding="utf-8")
    print("\n清单已落盘：", out)


if __name__ == "__main__":
    main()

"""真图对照组：`text/line` 这条规则在 1988 张公开 CAD 图上会不会也报几百处。

背景：这条规则在我们自己的产出上报 668 处，但**从来没有对照组**，所以一直只当线索、
不敢拿来改产物（上一把脏尺子把 98% 真实零件判为不合格，那次教训记在项目记忆里）。
MISUMI 的公开图是供应商出的矢量图：有轮廓线、有文字，正好能量一次。

跑法：
    cd drawform && PYTHONPATH=src ../spike/.venv/bin/python ../research/measure_public_control.py
"""

from __future__ import annotations

import collections
import pathlib
import statistics

from drawform.eval.ingest import read_sheet
from drawform.output.collide import find_collisions

ROOT = pathlib.Path(__file__).resolve().parents[1] / "公开样本_dxf"


def main() -> int:
    sheets = sorted((ROOT / "MISUMI").rglob("*.dxf"))
    per_sheet = []
    measurable = 0
    skipped_total: collections.Counter = collections.Counter()
    unreadable = 0

    for path in sheets:
        try:
            ingested = read_sheet(path)
        except Exception:
            unreadable += 1
            continue
        for kind, count in ingested.skipped.items():
            skipped_total[kind] += count
        texts = ingested.drawing.texts
        lines = ingested.drawing.items
        if not texts or not lines:
            continue                    # 没有"字 + 线"同时存在的图判不了这条规则
        measurable += 1
        found = find_collisions(ingested.drawing)
        per_sheet.append(sum(1 for c in found if c.kind == "text/line"))

    dirty = sum(1 for h in per_sheet if h)
    print(f"扫了 {len(sheets)} 张，打不开 {unreadable}，"
          f"能判这条规则的 {measurable} 张（字与线同时存在）")
    if not per_sheet:
        print("没有可判的图 —— 这个对照组不成立，别下结论")
        return 1
    print(f"text/line：{dirty}/{measurable} 张有命中（{dirty / measurable:.1%}），"
          f"共 {sum(per_sheet)} 处；每张均值 {statistics.fmean(per_sheet):.2f}，"
          f"最多 {max(per_sheet)}")
    print(f"读不进来的实体（累计）：{dict(skipped_total.most_common(6))}")
    print("我方产出的同一指标要另外量：PYTHONPATH=src:../research "
          "../spike/.venv/bin/python ../research/measure_sheet_collisions.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

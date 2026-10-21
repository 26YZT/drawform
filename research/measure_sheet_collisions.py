"""图面互斥检测器：拿真实图纸当对照组，先验尺子再照自己。

背景：检测器在 64 件真实零件上判出 98% 脏、963 处冲突。一个把 98% 真实零件判为
不合格的尺子，和图纸本身一样可能是错的 —— 脏尺子会误杀好规则。

语料里没有真图的 DXF（只有 PNG + 标注 JSON），所以只有 `text/text` 一条能验：
标注 JSON 给的是归一化文字框，两两重叠可以直接数。
`text/line` 与 `leader/line` **没有对照组可查**（PNG 里没有矢量线），
它们的数字只能当线索，不能当判据。
"""

from __future__ import annotations

import json
import pathlib
from collections import Counter
from itertools import combinations

from drawform.dimensioning.place import place_datum_marks, place_scheme
from drawform.dimensioning.scheme import derive_scheme
from drawform.eval.corpus import load_corpus
from drawform.geometry.features import extract_features
from drawform.geometry.layout import layout_views
from drawform.geometry.projection import FRONT, TOP, LEFT, project_view
from drawform.geometry.loader import import_step
from drawform.output.collide import find_collisions
from drawform.output.drawing import build_drawing

CORPUS = pathlib.Path(__file__).resolve().parents[1] / "corpus" / "cadsy"


def control_text_overlaps() -> tuple[int, int, int]:
    """真实图纸自己的文字重叠率 —— `text/text` 规则的对照组。"""
    parts = dirty = pairs = 0
    for sample in sorted((CORPUS / "samples").iterdir()):
        labels = sample / "iso_labels.json"
        if not labels.exists():
            continue
        boxes = [item["box"] for item in json.loads(labels.read_text())["annotations"]
                 if item.get("box") and len(item["box"]) == 4]
        if len(boxes) < 2:
            continue
        parts += 1
        hits = sum(1 for a, b in combinations(boxes, 2)
                   if not (a[2] <= b[0] or b[2] <= a[0]
                           or a[3] <= b[1] or b[3] <= a[1]))
        pairs += hits
        dirty += 1 if hits else 0
    return parts, dirty, pairs


def ours() -> tuple[int, int, Counter]:
    parts = dirty = 0
    kinds: Counter = Counter()
    for sample in load_corpus(CORPUS):
        try:
            model = import_step(sample.directory / "model.step")
            if not model.is_valid:
                continue
            features = extract_features(model.shape, model.bbox)
            scheme = derive_scheme(model, features)
            projections = {d.name: project_view(model.shape, d) for d in (FRONT, TOP, LEFT)}
            dims = place_scheme(scheme, projections)
            marks = place_datum_marks(scheme, projections, dims)
            drawing = build_drawing(layout_views(tuple(projections.values())),
                                    dimensions=dims, datum_marks=marks)
        except Exception:
            continue
        parts += 1
        found = find_collisions(drawing)
        dirty += 1 if found else 0
        kinds.update(c.kind for c in found)
    return parts, dirty, kinds


def main() -> None:
    c_parts, c_dirty, c_pairs = control_text_overlaps()
    o_parts, o_dirty, o_kinds = ours()
    print(f"对照组（真实图纸）  {c_parts} 张：文字重叠 {c_dirty} 张、{c_pairs} 对")
    print(f"我们（同一类判据）  {o_parts} 件：有冲突 {o_dirty} 件（{o_dirty/max(o_parts,1):.0%}）")
    for kind, count in o_kinds.most_common():
        verdict = "有对照组，可信" if kind == "text/text" else "无对照组，只当线索"
        print(f"   {kind:<12} {count:>4} 处   {verdict}")


if __name__ == "__main__":
    main()

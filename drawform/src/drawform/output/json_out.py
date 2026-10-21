"""JSON 出口：把一次出图的全部事实交给机器读。

为什么要单独一个出口，SVG/PDF 不是给人看就够了：下游还要拿它做二次处理
（批量核对、接 PDM、喂给检索）。人读的图和机读的数必须是**同一次计算**的产物，
所以这里不重新推断任何东西，只把 pipeline 已经算出来的东西原样序列化。

`assumptions` 是这个文件里最有价值的一段：它记的不是"图上有什么"，
而是"机器替人做了哪些决定"。那部分信息在其他任何出口里都读不出来。
"""

from __future__ import annotations

import json
import pathlib
from dataclasses import asdict

from drawform.dimensioning.constraints import Chain, Sized
from drawform.dimensioning.general import general_tolerances
from drawform.geometry.features import Bore, Gap, Shaft
from drawform.output.assumptions import assumptions_for

#: 改结构就 +1。下游按这个字段决定能不能吃，不要靠猜。
SCHEMA_VERSION = 2


def _feature_dict(feature) -> dict:
    base = {"id": feature.feature_id, "kind": type(feature).__name__}
    for name in ("diameter", "depth", "length", "gap", "through", "solid", "axis"):
        value = getattr(feature, name, None)
        if value is not None:
            base[name] = value
    return base


def build_document(*, model, sheet, scheme, dimensions, datum_marks, views,
                   verdict=None, questions=None, answers=(), answer_notes=(),
                   drawing_scale=1.0, scale_label="1:1", reference_facts=(), reference_presentations=None) -> dict:
    """一次出图的完整事实。键的顺序稳定，便于人逐条核对与 diff。"""
    constraints, chains = [], []
    # 未注公差按画法不上图，但下游必须能逐条查到"这条实际允许差多少"。
    general = {(row["feature"], row["dof"]): row
               for row in general_tolerances(scheme.plan)}
    for item in scheme.plan.constraints:
        if isinstance(item, Chain):
            chains.append({"name": item.name, "total": item.total,
                           "members": [list(m) for m in item.members],
                           "reference": [list(r) for r in item.reference]})
        elif isinstance(item, Sized):
            row = {"feature": item.feature, "dof": item.dof,
                   "value": item.value, "quantity": item.quantity,
                   "tolerance": item.tolerance}
            derived = general.get((item.feature, item.dof))
            if derived is not None:
                row["general"] = {key: value for key, value in derived.items()
                                  if key not in ("feature", "dof", "value")}
            constraints.append(row)
    document = {
        "schema_version": SCHEMA_VERSION,
        "part": {"bbox": list(model.bbox), "origin": list(model.origin),
                 "is_valid": model.is_valid},
        "sheet": sheet,
        "drawing_scale": drawing_scale,
        "scale_label": scale_label,
        "views": [{"name": view.projection.direction.name,
                   "origin": list(view.origin)} for view in views],
        "features": [_feature_dict(f) for f in scheme.features
                     if isinstance(f, (Bore, Shaft, Gap))],
        "constraints": constraints,
        "chains": chains,
        "datums": [{"letter": m.letter, "view": m.view,
                    "anchor": list(m.anchor)} for m in datum_marks],
        "dimensions": [{"kind": d.kind, "view": d.view, "text": d.text,
                        "value": d.value, "axis": d.axis,
                        "p1": list(d.p1), "p2": list(d.p2),
                        "base": list(d.base) if d.base is not None else None,
                        "source": list(d.source) if d.source else None,
                        "shared_sources": [list(source) for source in d.shared_sources],
                        "shared_witnesses": [[list(p1),list(p2)] for p1,p2 in d.shared_witnesses],
                        "shared_values": list(d.shared_values),
                        **({'measurement_axis': list(d.measurement_axis)}
                           if d.measurement_axis is not None else {}),
                        **({'reference_points_3d': [list(p) for p in d.reference_points_3d]}
                           if d.reference_points_3d else {})}
                       for d in dimensions],
        "notes": list(scheme.notes),
        "annotations": [],
        "assumptions": assumptions_for(model, scheme, dimensions, datum_marks,
                                       verdict=verdict, answers=answers),
        "verdict": None if verdict is None else {
            "decision": verdict.decision, "failed": list(verdict.failed),
            "reasons": list(verdict.reasons), "warnings": list(verdict.warnings)},
    }
    if model.excluded_geometry:
        document['part']['excluded_geometry'] = list(model.excluded_geometry)
    if reference_facts:
        document['schema_version'] = 4
        document['reference_confirmations'] = [asdict(fact) for fact in reference_facts]
        document['reference_presentations'] = reference_presentations
    if questions is not None:
        document["questions"] = [asdict(question) for question in questions]
        document["truncated"] = any(question.truncated for question in questions)
    if answers:
        document["schema_version"] = 4 if reference_facts else 3
        document["answers"] = [asdict(answer) for answer in answers]
        document["answer_notes"] = list(answer_notes)
        document["answered_count"] = len(answers)
        document["unknown_count"] = sum(answer.chosen_option_id is None for answer in answers)
    return document


def write_json(path, **kwargs) -> pathlib.Path:
    """落盘 UTF-8 JSON。`ensure_ascii=False` 是必须的：⌀//⌴ 转义成 \\uXXXX
    人就没法直接看了，而这个文件的一半用途是人来核对。"""
    target = pathlib.Path(path)
    target.write_text(json.dumps(build_document(**kwargs), ensure_ascii=False,
                                 indent=2), encoding="utf8")
    return target

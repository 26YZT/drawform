"""cadsy 配对语料的读取层。

语料是 cadsy/cad-technical-drawings（Apache-2.0）：Cadsy 从 Zero-to-CAD-100k 的
STEP 模型自动生成 ISO / ASME 两套风格的图纸，并给出每条标注的类型、所属视图、
原文与归一化 bbox。

必须记住的边界：这是**合成参照**，不是专家金标准。对齐它等于对齐 Cadsy 的画法
策略 —— 可以判视图选择、尺寸选与放、标注语法与几何量测，不能判设计意图
（为什么这个面给 ±0,05 而那个走一般公差）。语料里也没有粗糙度与螺纹标注。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from drawform.rules.notation import FeatureControl, Token, parse_fcf, parse_notation


@dataclass(frozen=True)
class Annotation:
    type: str
    view: str
    text: str
    box: tuple[float, float, float, float]
    tokens: tuple[Token, ...] = ()
    frames: tuple[FeatureControl, ...] = ()


@dataclass(frozen=True)
class Sample:
    sample_id: str
    directory: Path
    annotations: tuple[Annotation, ...]

    @property
    def step_path(self) -> Path:
        return self.directory / "model.step"

    def of_type(self, annotation_type: str) -> tuple[Annotation, ...]:
        return tuple(a for a in self.annotations if a.type == annotation_type)


#: 只有这两类走标注语法。标题栏文字（"ISO 2768-f"、"Issue date"）与基准字母
#: 不是标注文法，套上去会产出成百上千条假 unknown，把真正的解析缺口淹掉。
NOTATION_TYPES = frozenset({"dimension", "callout"})


def load_sample(directory: Path) -> Sample:
    payload = json.loads((directory / "iso_labels.json").read_text(encoding="utf-8"))
    annotations = []
    for item in payload.get("annotations", []):
        text = item.get("text", "")
        kind = item.get("type", "")
        annotations.append(Annotation(
            type=kind,
            view=item.get("view", ""),
            text=text,
            box=tuple(float(v) for v in item.get("box", ())),
            tokens=parse_notation(text) if kind in NOTATION_TYPES else (),
            frames=parse_fcf(text) if kind == "feature_control_frame" else (),
        ))
    return Sample(sample_id=directory.name, directory=directory,
                  annotations=tuple(annotations))


def load_corpus(root: Path) -> tuple[Sample, ...]:
    base = root / "samples" if (root / "samples").is_dir() else root
    samples = []
    for directory in sorted(base.glob("part_*")):
        if (directory / "model.step").is_file() and (directory / "iso_labels.json").is_file():
            samples.append(load_sample(directory))
    return tuple(samples)

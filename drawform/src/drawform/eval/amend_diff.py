"""改后图 vs 机器底稿：把工程师的改动翻译成能统计的动作。

动作分两类，**它们的严重度差一个量级**：
- 取舍类（`removed` / `added`）：这条尺寸该不该标 —— 是偏好与经验；
- 正确性类（`value_changed` / `symbol_changed` / `wrong_count`）：这个数本身就错了 —— 照图加工会废件。
混在一起统计，"我们标错了"就会被稀释成"工程师有个人偏好"。所以 `blocking` 单独一个位。

配对规则只有一条：**配不上就退回"删 + 加"，不猜**。
把一条挪到 60 mm 外的尺寸当成"改了数值"，教给机器的是不存在的关系。
"""

from __future__ import annotations

import math
import pathlib
import re
from dataclasses import dataclass

import ezdxf

from drawform.eval.dwg_roundtrip import dimension_labels
from drawform.eval.ingest import read_sheet
from drawform.review import BLOCKING_REASONS

#: 尺寸文字锚点的就近配对容差（mm）。超过就不认为是同一条。
MATCH_TOL = 2.0

_SYMBOLS = ("⌀", "Ø", "Φ", "⌴", "↧", "R")
_QUANTITY = re.compile(r"^\s*(\d+)\s*×")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")

#: 动作 → 集团试点口径里的理由分类（`review.REASONS`）。
ACTION_REASON = {
    "value_changed": "wrong_value",
    "symbol_changed": "wrong_symbol",
    "wrong_count": "wrong_count",
    "added": "missing_dimension",
    "removed": "extra_dimension",
    "datum_changed": "datum_choice",
    "geometry_touched": "view_choice",
}
ACTION_ORDER = tuple(ACTION_REASON)


@dataclass(frozen=True)
class Diff:
    actions: dict
    #: 逐项明细。计数能统计，不能核对 —— 复核台要当场指出"是哪一条、从几改成几"，
    #: 否则"逐项接受/修改/驳回"这句话只是验收标准上的四个字。
    items: tuple = ()

    def as_dict(self) -> dict:
        return {k: v for k, v in self.actions.items() if v}

    @property
    def reasons(self) -> tuple[str, ...]:
        hit = {ACTION_REASON[a] for a, n in self.actions.items() if n}
        return tuple(r for r in ACTION_REASON.values() if r in hit)

    @property
    def blocking(self) -> bool:
        """A 组（数值正确性）出现过一次，这张图就不该被当成"只是偏好不同"。"""
        return any(reason in BLOCKING_REASONS for reason in self.reasons)


def _label_parts(label: str) -> tuple[str | None, tuple[str, ...], frozenset[str]]:
    """拆成（数量前缀, 去掉前缀后的数值, 符号集合）。"""
    quantity = _QUANTITY.match(label)
    core = label[quantity.end():] if quantity else label
    return (quantity.group(1) if quantity else None,
            tuple(_NUMBER.findall(core)),
            frozenset(s for s in _SYMBOLS if s in label))


def _anchors(path) -> list:
    """每条尺寸的（文字锚点, 显示值）。锚点缺失时退回插入点。"""
    doc = ezdxf.readfile(str(pathlib.Path(path)))
    out = []
    for entity, label in zip(_dimension_entities(doc), dimension_labels(doc)):
        point = entity.dxf.get("text_midpoint", None)
        if point is None:
            point = entity.dxf.get("insert", None)
        if point is None:
            continue
        out.append(((float(point[0]), float(point[1])), label))
    return out


def _dimension_entities(doc):
    from drawform.eval.intake import dimension_entities
    return list(dimension_entities(doc))


def _datum_letters(path) -> dict:
    doc = ezdxf.readfile(str(pathlib.Path(path)))
    found: dict[str, tuple[float, float]] = {}
    for layout in doc.layouts:
        for entity in layout:
            if entity.dxftype() not in ("TEXT", "MTEXT", "ATTRIB"):
                continue
            text = (entity.dxf.get("text") or "").strip()
            if len(text) == 1 and text in "ABC":
                point = entity.dxf.get("insert")
                if point is not None:
                    found[text] = (float(point[0]), float(point[1]))
    return found


def _layer_counts(path) -> dict:
    return _count_by_layer(read_sheet(path).drawing.items)


def _count_by_layer(items) -> dict:
    counts: dict[str, int] = {}
    for item in items:
        counts[item.layer] = counts.get(item.layer, 0) + 1
    return counts


def _pair(before: list, after: list) -> list[tuple[int, int]]:
    """按锚点距离就近配对；一条只配一次，超过容差不配。"""
    candidates = []
    for i, (point, _label) in enumerate(before):
        for j, (other, _other_label) in enumerate(after):
            distance = math.dist(point, other)
            if distance <= MATCH_TOL:
                # 先认标签再认距离：显示值没变是"同一条尺寸"最强的证据。
                # 只按距离配，会把"删掉一条、另一条挪近点"读成"这个数被改了"。
                candidates.append((0 if before[i][1] == after[j][1] else 1,
                                   distance, i, j))
    taken_before: set[int] = set()
    taken_after: set[int] = set()
    pairs: list[tuple[int, int]] = []
    for _same, _distance, i, j in sorted(candidates):
        if i in taken_before or j in taken_after:
            continue
        taken_before.add(i)
        taken_after.add(j)
        pairs.append((i, j))
    return pairs


def compare(draft, amended) -> Diff:
    """机器底稿 vs 工程师的生效版本。"""
    actions = {name: 0 for name in ACTION_ORDER}
    before, after = _anchors(draft), _anchors(amended)
    matched = _pair(before, after)
    items: list[dict] = []

    paired_before = {i for i, _j in matched}
    paired_after = {j for _i, j in matched}

    def record(action, point, old, new):
        actions[action] += 1
        items.append({"action": action, "before": old, "after": new,
                      "at": (round(point[0], 2), round(point[1], 2))})

    for i, j in matched:
        left, right = before[i][1], after[j][1]
        if left == right:
            continue
        (q_left, n_left, s_left) = _label_parts(left)
        (q_right, n_right, s_right) = _label_parts(right)
        # 一条尺寸可能同时踩两件事（数量与符号都变了）：两条都记，不折叠成一条，
        # 折叠就把"改了什么"这条信息丢了 —— 而那正是这份明细存在的理由。
        if q_left != q_right:
            record("wrong_count", after[j][0], left, right)
        if s_left != s_right:
            record("symbol_changed", after[j][0], left, right)
        if n_left != n_right:
            record("value_changed", after[j][0], left, right)
    for i, (point, label) in enumerate(before):
        if i not in paired_before:
            record("removed", point, label, None)
    for j, (point, label) in enumerate(after):
        if j not in paired_after:
            record("added", point, None, label)

    letters_before, letters_after = _datum_letters(draft), _datum_letters(amended)
    for letter, point in letters_before.items():
        other = letters_after.get(letter)
        if other is None or math.dist(point, other) > MATCH_TOL:
            actions["datum_changed"] += 1
    actions["datum_changed"] += len(set(letters_after) - set(letters_before))

    if _layer_counts(draft) != _layer_counts(amended):
        actions["geometry_touched"] += 1

    # 基准与几何两类**不进明细**：它们的位置与"哪一条尺寸"不同构（基准代号是字母、
    # 几何是整层增删），凑成同一种行会把可读性换成假的一致性。
    return Diff(actions=actions, items=tuple(items))

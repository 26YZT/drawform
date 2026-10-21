"""人的回答只携带候选 id；note 只供审计，不参与尺寸或图面数值。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from collections import Counter
from itertools import zip_longest
import math
import re
import xml.etree.ElementTree as ET

from drawform.dimensioning.constraints import Sized
from drawform.dimensioning.general import general_tolerance_note
from drawform.dimensioning.place import position_anchors
from drawform.dimensioning.scheme import ZERO_POSITION
from drawform.service.questions import (DATUM_CHOICE, SPECIAL_TOLERANCE, UNNOTED_ANGLE,
                                        _dim_id, _fingerprint, _fit_options, _subject_kinds,
                                        datum_candidates_for)

MAX_NOTE_LENGTH = 4096


@dataclass(frozen=True)
class Answer:
    question_id: str
    chosen_option_id: str | None = None
    note: str = ""

    def __post_init__(self):
        if not isinstance(self.question_id, str) or not self.question_id.strip():
            raise ValueError("question_id 必须是非空字符串")
        if self.chosen_option_id is not None and (
                not isinstance(self.chosen_option_id, str) or not self.chosen_option_id):
            raise ValueError("option_id 必须是非空字符串或 null（不知道）")
        if not isinstance(self.note, str) or len(self.note) > MAX_NOTE_LENGTH:
            raise ValueError(f"free_note 必须是字符串，最多 {MAX_NOTE_LENGTH} 字")


class AnswerError(ValueError):
    def __init__(self, message, reason="invalid_answer", status_code=400):
        super().__init__(message)
        self.reason = reason
        self.status_code = status_code


def bind_scheme(scheme, model):
    """只绑定引擎量出的事实，不从人的回答解析任何坐标。"""
    return replace(scheme, origin=model.origin,
                   datum_candidates=datum_candidates_for(model.shape))


def validate_answers(questions, answers):
    by_id = {question.id: question for question in questions}
    accepted, notes, seen = [], [], set()
    for answer in answers:
        if not isinstance(answer, Answer):
            raise AnswerError("answers 中的每一项必须是 Answer")
        if answer.question_id in seen:
            notes.append(f"问题 {answer.question_id} 重复回答，取第一条")
            continue
        seen.add(answer.question_id)
        question = by_id.get(answer.question_id)
        if question is None:
            raise AnswerError(f"问题 {answer.question_id} 不属于这张图")
        if answer.chosen_option_id is not None:
            option = next((o for o in question.options if o.id == answer.chosen_option_id), None)
            if option is None:
                raise AnswerError(f"选项 {answer.chosen_option_id} 不在该问题的候选里")
            if question.kind == DATUM_CHOICE and (
                    question.datum not in ("A", "B", "C") or question.axis not in ("x", "y", "z")
                    or (option.id.startswith("face-") and option.axis not in ("x", "y", "z"))):
                raise AnswerError("缺少该基准候选的引擎方向依据，请重新出图后再选择", "missing_basis", 409)
            if (question.kind == DATUM_CHOICE and option.axis
                    and question.axis != option.axis):
                raise AnswerError("该平面的法向与当前定位方向不平行，会使基准体系冲突；"
                                  f"请选择{question.axis.upper()}=常数的平面（法向沿{question.axis.upper()}方向）", "incompatible_datum")
        accepted.append(answer)
    return tuple(accepted), tuple(notes)


def apply_answers(scheme, questions, answers):
    """纯函数。先全量校验，再引用引擎候选；任一非法项使整份方案保持不变。"""
    try:
        accepted, explanations = validate_answers(questions, answers)
    except AnswerError as error:
        return scheme, (str(error),)
    by_id = {question.id: question for question in questions}
    constraints = list(scheme.plan.constraints)
    targets = {_fingerprint("q", (kind, _dim_id(c, scheme))): index
               for index, c in enumerate(constraints) if isinstance(c, Sized)
               for kind in (SPECIAL_TOLERANCE, UNNOTED_ANGLE)}
    subject_kinds = _subject_kinds(scheme)
    choices = dict(scheme.datum_choices)
    candidates = {face.id: face for face in scheme.datum_candidates}
    notes, explanations = list(scheme.notes), list(explanations)
    for answer in accepted:
        question = by_id[answer.question_id]
        if answer.chosen_option_id is None:
            explanations.append(f"问题 {question.id}：人也不知道，保留未注状态")
            continue
        option = next(o for o in question.options if o.id == answer.chosen_option_id)
        if option.id in ("current", "none") or option.id.startswith("bbox-"):
            explanations.append(f"问题 {question.id}：{option.label}，未写入默认值")
            continue
        if question.kind == DATUM_CHOICE:
            face = candidates.get(option.id)
            if face is None or not scheme.origin:
                return scheme, ("缺少该基准面的 STEP 几何依据，不能回写",)
            axis_index = "xyz".index(face.axis)
            anchors = position_anchors(scheme)
            updated = []
            for dimension in constraints:
                if isinstance(dimension, Sized) and dimension.dof == face.axis:
                    anchor = anchors.get(dimension.feature)
                    if anchor is None:
                        return scheme, ("缺少定位要素的 STEP 几何依据，不能换基准",)
                    distance = abs(anchor[axis_index] - face.position)
                    dimension = replace(dimension, value=0.0 if distance < ZERO_POSITION else distance)
                updated.append(dimension)
            constraints = updated
            choices[question.datum] = face.id
            continue
        index = targets.get(question.id)
        if index is None:
            return scheme, (f"问题 {question.id} 找不到对应的引擎尺寸",)
        dimension = constraints[index]
        if question.kind == SPECIAL_TOLERANCE:
            # Persisted question catalogues may predate the hole/shaft split. Their
            # option ids are audit evidence, not authority over current geometry.
            current_options = _fit_options(dimension.value, subject_kinds.get(dimension.feature))
            if option.id not in {candidate.id for candidate in current_options}:
                return scheme, ("专门公差候选与当前孔轴类型或已核对数表不符，"
                                "请重新出图并读取问题卡",)
            constraints[index] = replace(dimension, tolerance=option.id)
        elif question.kind == UNNOTED_ANGLE:
            tolerance = f"±{option.value:g}°"
            constraints[index] = replace(dimension, tolerance=tolerance)
            notes.insert(0, f"角度公差 {dimension.value:g}°：{tolerance}")
    plan = replace(scheme.plan, constraints=tuple(constraints))
    if plan != scheme.plan:
        notes = [general_tolerance_note(plan) if note.startswith("一般公差") else note for note in notes]
    result = replace(scheme, plan=plan,
                     notes=tuple(notes), datum_choices=tuple(sorted(choices.items())))
    return result, tuple(explanations)


def dimension_overrides(scheme, dimensions):
    tolerances = {(c.feature, c.dof): c.tolerance for c in scheme.plan.constraints
                  if isinstance(c, Sized) and c.tolerance is not None and c.dof != "angle"}
    return {f"dim:{dim.source}": dim.text + tolerances[dim.source]
            for dim in dimensions if dim.source in tolerances}


def write_dimension_overrides(path, dimensions, overrides):
    """改原生 DIMENSION 的显示覆盖及其文字块，不重渲染、不改测量几何。"""
    if not overrides:
        return
    import ezdxf

    document = ezdxf.readfile(str(path))
    entities = list(document.modelspace().query("DIMENSION"))
    if len(entities) != len(dimensions):
        raise AnswerError("DXF 尺寸实体与引擎落位尺寸数量不一致")
    for entity, dim in zip(entities, dimensions):
        content = overrides.get(f"dim:{dim.source}")
        if content is None:
            continue
        entity.dxf.text = content
        for text in document.blocks[entity.dxf.geometry].query("TEXT MTEXT"):
            if text.dxftype() == "TEXT":
                text.dxf.text = content
            else:
                text.text = content
    document.saveas(str(path))


def drawing_changes(before, after):
    """比较两份引擎 SVG；直接比较坐标字符串，不从人的回答解析数值。"""
    def elements(path):
        root = ET.parse(path).getroot()
        texts = [("".join(node.itertext()), tuple(node.get(key) for key in
                  ("x", "y", "transform", "text-anchor"))) for node in root.findall("{*}text")]
        shapes = {group.get("id"): [tuple(sorted(node.attrib.items())) for node in group]
                  for group in root.findall("{*}g")}
        return texts, shapes

    old_texts, old_shapes = elements(before)
    new_texts, new_shapes = elements(after)
    changed_texts = sum(a is None or b is None or a[0] != b[0]
                        for a, b in zip_longest(old_texts, new_texts))
    changed_positions = sum(a[1] != b[1] for a, b in zip(old_texts, new_texts))
    for layer in old_shapes.keys() | new_shapes.keys():
        changed_positions += sum(a != b for a, b in zip_longest(
            old_shapes.get(layer, ()), new_shapes.get(layer, ())))
    return {"changed_texts": changed_texts, "changed_positions": changed_positions}


def drawing_annotations(before, after):
    """文字变化的位置取自两份引擎 SVG；提示框复用引擎字框估算，不是制造尺寸。"""
    from drawform.output.collide import text_box
    from drawform.output.drawing import Text

    roots = [ET.parse(path).getroot() for path in (before, after)]
    fields = ("x", "y", "text-anchor", "transform", "font-size", "font-family")

    def texts(root):
        return Counter(("".join(node.itertext()), *(node.get(key, "") for key in fields))
                       for node in root.findall("{*}text"))

    old, new = map(texts, roots)
    removed, added = sorted((old - new).elements()), sorted((new - old).elements())
    if not removed and not added:
        return []
    try:
        pages = [tuple(float(value) for value in root.attrib["viewBox"].split()) for root in roots]
        if (pages[0] != pages[1] or len(pages[0]) != 4 or pages[0][:2] != (0.0, 0.0)
                or not all(math.isfinite(value) for value in pages[0])
                or min(pages[0][2:]) <= 0):
            raise ValueError
        width, height = pages[0][2:]
    except (KeyError, TypeError, ValueError):
        raise ValueError("变更提示缺少一致的引擎图纸坐标系") from None

    def box(item):
        content, raw_x, raw_y, anchor, transform, size, _font = item
        try:
            x, y, text_height = float(raw_x), float(raw_y), float(size)
            if not all(math.isfinite(n) for n in (x, y, text_height)) or text_height <= 0:
                raise ValueError
            rotate = 0.0
            if transform:
                match = re.fullmatch(r"rotate\(([-+\d.eE]+)\s+([-+\d.eE]+)\s+([-+\d.eE]+)\)", transform)
                if match is None:
                    raise ValueError
                rotate, centre_x, centre_y = map(float, match.groups())
                if (not all(math.isfinite(n) for n in (rotate, centre_x, centre_y))
                        or (centre_x, centre_y) != (x, y)):
                    raise ValueError
            if anchor not in ("", "start", "middle", "end"):
                raise ValueError
            x0, y0, x1, y1 = text_box(Text(content, x, height - y, anchor or "start",
                                           rotate=rotate, height=text_height))
            # 1 mm 留白只用于定位提示，不写回原图或尺寸值。
            left, top = max(0.0, x0 - 1.0), max(0.0, height - y1 - 1.0)
            right, bottom = min(width, x1 + 1.0), min(height, height - y0 + 1.0)
            if right <= left or bottom <= top:
                raise ValueError
            return [left, top, right - left, bottom - top]
        except (TypeError, ValueError):
            raise ValueError("变更文字缺少有效的引擎坐标，不能猜测位置") from None

    annotations = []
    for current in added:
        # 同位置改文字优先；同一文字唯一地移动时只标新位置，避免叠两个提示。
        matching = [item for item in removed if item[1:5] == current[1:5]]
        if not matching:
            same_text = [item for item in removed if item[0] == current[0]]
            if len(same_text) == 1 and sum(item[0] == current[0] for item in added) == 1:
                matching = same_text
        previous = matching[0] if matching else None
        if previous is not None:
            removed.remove(previous)
        kind = ("moved" if previous[0] == current[0] else "changed") if previous else "added"
        annotations.append({"kind": kind, "before": None if previous is None else previous[0],
                            "after": current[0], "box": box(current)})
    annotations.extend({"kind": "removed", "before": previous[0], "after": None,
                        "box": box(previous)} for previous in removed)
    return annotations

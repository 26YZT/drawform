"""待确认项：机器没有依据、需要人从引擎的封闭候选中作出选择。

assumptions 陈述机器已经作出的决定；questions 提问尚未确认的决定。
候选中的数值只能由引擎给出，问题自身不接受人工数值。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import logging

from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.BRepGProp import BRepGProp
from OCP.GeomAbs import GeomAbs_Circle, GeomAbs_Plane
from OCP.GProp import GProp_GProps
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS

from drawform.dimensioning.constraints import Sized
from drawform.dimensioning.scheme import DatumCandidate
from drawform.dimensioning.general import ANGULAR_DOFS, broken_promises, general_tolerances
from drawform.geometry.features import (Bore, Shaft, _axis_offset, _canonical,
                                        find_counterbores, group_bores,
                                        group_counterbores, group_shafts, planar_faces)
from drawform.output import assumptions
from drawform.rules import iso286

SPECIAL_TOLERANCE = "special_tolerance"
DATUM_CHOICE = "datum_choice"
UNNOTED_ANGLE = "unnoted_angle"
KINDS = (SPECIAL_TOLERANCE, DATUM_CHOICE, UNNOTED_ANGLE)

MAX_OPTIONS = 64
_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class Option:
    id: str
    label: str
    value: float | None
    note: str = ""
    axis: str = ""


@dataclass(frozen=True)
class Question:
    id: str
    kind: str
    target: str
    claim: str
    basis: str
    impact: str
    options: tuple[Option, ...]
    allow_unknown: bool = True
    truncated: bool = False
    datum: str = ""
    axis: str = ""

    def __post_init__(self):
        assert self.kind in KINDS, self.kind
        assert isinstance(self.impact, str) and self.impact.strip(), "必须说明不确认的后果"
        assert self.options, f"{self.id} 没有候选：宁可只给一项，不许给空"
        ids = [option.id for option in self.options]
        assert len(ids) == len(set(ids)), f"选项 id 重复：{ids}"
        assert self.allow_unknown is True, "必须允许选择不知道"
        if self.kind == DATUM_CHOICE:
            assert all(option.value is None for option in self.options), "基准候选不带数值"


def _rounded(value):
    rounded = round(value, 6)
    return 0.0 if rounded == 0 else rounded   # -0.0 与 +0.0 是同一处几何


def _xyz(point):
    return tuple(_rounded(c) for c in (point.X(), point.Y(), point.Z()))


def _normal(direction):
    vector = (direction.X(), direction.Y(), direction.Z())
    index = max(range(3), key=lambda k: abs(vector[k]))
    sign = 1 if vector[index] > 0 else -1
    return tuple(_rounded(sign * c) for c in vector)


def _fingerprint(kind, geometry):
    encoded = json.dumps(geometry, separators=(",", ":"), ensure_ascii=False).encode()
    return f"{kind}-{hashlib.sha256(encoded).hexdigest()[:16]}"


def _edge_id(edge) -> str:
    """边的几何指纹，不含拓扑序号、内存地址或参数化方向。"""
    curve = BRepAdaptor_Curve(edge)
    props = GProp_GProps()
    BRepGProp.LinearProperties_s(edge, props)
    geometry = [str(curve.GetType()), _rounded(props.Mass()), _xyz(props.CentreOfMass())]
    if curve.GetType() == GeomAbs_Circle:
        circle = curve.Circle()
        # 整圆的缝可以换位置：不用参数起点认圆，用圆心/半径/法向认。
        geometry += [_xyz(circle.Location()), _rounded(circle.Radius()),
                     _normal(circle.Axis().Direction())]
    else:
        first, last = curve.FirstParameter(), curve.LastParameter()
        geometry.append(sorted(_xyz(curve.Value(first + (last - first) * i / 8))
                               for i in range(9)))
    return _fingerprint("edge", geometry)


def _face_id(face) -> str:
    """平面的法向/位置/面积/质心及边界。等面积的不同面也不能撞 id。"""
    surface = BRepAdaptor_Surface(face)
    assert surface.GetType() == GeomAbs_Plane, "基准候选必须是引擎识别的平面"
    plane = surface.Plane()
    normal = _normal(plane.Axis().Direction())
    offset = _rounded(sum(a * b for a, b in zip(normal, _xyz(plane.Location()))))
    props = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, props)
    edges = set()
    explorer = TopExp_Explorer(face, TopAbs_EDGE)
    while explorer.More():
        edges.add(_edge_id(TopoDS.Edge(explorer.Current())))
        explorer.Next()
    return _fingerprint("face", (normal, offset, _rounded(props.Mass()),
                                 _xyz(props.CentreOfMass()), sorted(edges)))


def _quantized(value):
    if isinstance(value, float):
        return _rounded(value)
    if isinstance(value, dict):
        return {key: _quantized(value[key]) for key in sorted(value)}
    if isinstance(value, (tuple, list)):
        return tuple(_quantized(item) for item in value)
    return value


def _feature_key(feature):
    geometry = asdict(feature)
    geometry.pop("feature_id")
    # 邻接记录含本次提取的局部id，不参与稳定几何身份。
    geometry.pop("shoulder_neighbors", None)
    geometry.pop("topology_checked", None)
    geometry.pop("end_tolerances", None)
    if isinstance(feature, (Bore, Shaft)):
        geometry["axis_point"] = _axis_offset(feature)
        geometry["axis_direction"] = _canonical(feature.axis_direction)
    return _fingerprint("feature", (type(feature).__name__, _quantized(geometry)))


def _subjects(scheme):
    """将方案的临时特征名接到几何身份；组不能跟着第一个成员换身份。"""
    features = scheme.features
    subjects = {f.feature_id: _feature_key(f) for f in features}
    subjects["outline"] = sorted((c.dof, _rounded(c.value)) for c in scheme.plan.constraints
                                 if isinstance(c, Sized) and c.feature == "outline")
    bores = [f for f in features if isinstance(f, Bore)]
    pairs = find_counterbores(bores)
    cbore_ids = {pair.cbore_id for pair in pairs}
    for group in group_bores([b for b in bores if b.feature_id not in cbore_ids]):
        geometry = sorted(subjects[member] for member in group.members)
        subjects[group.group_id] = geometry
        subjects[f"{group.group_id}:center"] = ("center", geometry)
    shafts = [f for f in features if isinstance(f, Shaft)]
    for group in group_shafts(shafts):
        subjects[group.group_id] = sorted(subjects[f.feature_id] for f in shafts
                                         if round(f.diameter, 6) == round(group.diameter, 6))
    for group in group_counterbores(pairs):
        subjects[group.group_id] = sorted(subjects[member] for member in group.members)
    return subjects


#: 尺寸对象属于内圆柱面还是外圆柱面。孔用孔制代号，轴用轴制代号 —— 这是语义，
#: 不是偏好：在外圆上标 H7 意味着下偏差为零、实际直径永远不小于公称值，
#: 装配意图整个反了。两者都**不删候选**，只是各给对的那一类。
HOLE, SHAFT = "hole", "shaft"


def _subject_kinds(scheme):
    """把方案里的每个尺寸主体映射到它到底是孔还是轴。

    `_subjects` 已经把孔组/轴组/沉孔组的身份算出来了，但它只用于指纹，
    没往下传。这里复用同一套分组，保证尺寸主体名与 `_subjects` 完全一致。
    """
    features = scheme.features
    kinds = {}
    bores = [f for f in features if isinstance(f, Bore)]
    pairs = find_counterbores(bores)
    cbore_ids = {pair.cbore_id for pair in pairs}
    plain = [b for b in bores if b.feature_id not in cbore_ids]
    for bore in plain:
        kinds[bore.feature_id] = HOLE
    for group in group_bores(plain):
        kinds[group.group_id] = HOLE
    for group in group_counterbores(pairs):
        kinds[group.group_id] = HOLE
    shafts = [f for f in features if isinstance(f, Shaft)]
    for shaft in shafts:
        kinds[shaft.feature_id] = SHAFT
    for group in group_shafts(shafts):
        kinds[group.group_id] = SHAFT
    return kinds


def _dim_id(dim, scheme) -> str:
    """测量对象、方向、基准的三元组；不把显示顺序或措辞当身份。"""
    subject = _subjects(scheme).get(dim.feature, dim.feature)
    datum = scheme.plan.datum.planes if scheme.plan.datum else ()
    return _fingerprint("dim", (subject, dim.dof, datum))


_DOF_LABELS = {"length": "外形长度", "width": "外形宽度", "height": "外形高度",
               "diameter": "直径", "counterbore": "沉孔直径", "depth": "深度",
               "gap": "间隙", "pcd": "分布圆直径", "angle": "角度",
               "x": "X 向定位", "y": "Y 向定位", "z": "Z 向定位"}


def _target(dim, scheme):
    unit = "°" if dim.dof in ANGULAR_DOFS else " mm"
    quantity = f"{dim.quantity}×" if dim.quantity > 1 else ""
    return (f"{quantity}{_DOF_LABELS.get(dim.dof, dim.dof)} {dim.value:g}{unit}"
            f"（{_dim_id(dim, scheme)}）")


def _question(*, kind, geometry, target, claim, basis, impact, options, datum="", axis=""):
    ordered = tuple(sorted(options, key=lambda option: option.id))
    assert MAX_OPTIONS > 0, "候选上限必须至少为一"
    truncated = len(ordered) > MAX_OPTIONS
    question_id = _fingerprint("q", (kind, geometry))
    if truncated:
        _LOG.warning("问题 %s 候选截断：共 %s 项，仅列出 %s 项",
                     question_id, len(ordered), MAX_OPTIONS)
    return Question(id=question_id, kind=kind, target=target, claim=claim, basis=basis,
                    impact=impact, options=ordered[:MAX_OPTIONS], truncated=truncated,
                    datum=datum, axis=axis)


def _fit_letters(kind):
    """这个尺寸对象能用哪一类代号。孔只给孔制、轴只给轴制，两边都不预筛常用度。"""
    if kind == SHAFT:
        return iso286.SHAFT_LETTERS
    if kind == HOLE:
        return iso286.HOLE_LETTERS
    # 认不出内外圆柱面（间隙、外形这类）时不猜方向，两类都给 —— 删候选比多给更糟。
    return iso286.HOLE_LETTERS | iso286.SHAFT_LETTERS


def _fit_options(nominal, kind=None):
    """不按常用程度预筛；只列已有数表确实能查出的、且与该对象内外方向相符的代号。"""
    options = []
    for letter in sorted(_fit_letters(kind)):
        for grade in iso286.IT_GRADES:
            code = f"{letter}{grade}"
            try:
                upper, lower = iso286.fit_for(code, nominal)
            except iso286.UnsupportedDeviation:
                continue
            # 上下偏差是两个数，单个 value 无法如实表达；按审核决定留空。
            options.append(Option(code, code, None,
                                  f"ISO 286 / GB/T 1800.1 已核对表："
                                  f"上偏差 {upper:+g} mm，下偏差 {lower:+g} mm"))
    if not options:
        options.append(Option("current", "维持当前未注专门公差", None,
                              "该公称尺寸的配合公差数表未核对，不补值；"
                              "当前仍未注专门公差，也可选择不知道"))
    return tuple(options)


def _tolerance_dimensions(scheme):
    """问题卡与 warning 共用触发条件；这里只找对象，不枚举候选。"""
    dimensions = {(c.feature, c.dof): c for c in scheme.plan.constraints
                  if isinstance(c, Sized)}
    gaps = {(row["feature"], row["dof"]): row
            for row in broken_promises(general_tolerances(scheme.plan))}
    # 已批准：孔径、轴径即使被一般公差覆盖，也要让人选择是否给专门公差。
    subjects = _subjects(scheme)
    for key, dim in dimensions.items():
        if (dim.tolerance is None and dim.dof in ("diameter", "counterbore")
                and dim.feature != "outline" and dim.feature in subjects):
            gaps.setdefault(key, {"why": "一般公差不表达装配意图；孔径、轴径的专门公差由人确认"})
    return tuple((dimensions[key], row["why"]) for key, row in gaps.items())


def special_tolerance_questions(scheme) -> tuple[Question, ...]:
    gaps = _tolerance_dimensions(scheme)
    if not gaps:
        return ()
    inference = assumptions._tolerance(None, scheme)
    kinds = _subject_kinds(scheme)
    questions = []
    for dim, why in gaps:
        questions.append(_question(
            kind=SPECIAL_TOLERANCE, geometry=_dim_id(dim, scheme),
            target=_target(dim, scheme), claim=f"{inference['claim']}；本条未给出专门公差",
            basis=f"{inference['basis']}；{why}", impact=inference["impact"],
            options=_fit_options(dim.value, kinds.get(dim.feature))))
    return tuple(sorted(questions, key=lambda q: (q.kind, q.target)))


def datum_candidates_for(shape):
    # 候选范围只取已有 planar_faces；原始拓扑仅用于补齐面积与边界指纹。
    allowed = {(face.axis, _rounded(face.position), _quantized(face.centroid))
               for face in planar_faces(shape)}
    candidates, matched = {}, set()
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    while explorer.More():
        face = TopoDS.Face(explorer.Current())
        surface = BRepAdaptor_Surface(face)
        if surface.GetType() == GeomAbs_Plane:
            normal = _normal(surface.Plane().Axis().Direction())
            index = max(range(3), key=lambda i: abs(normal[i]))
            props = GProp_GProps()
            BRepGProp.SurfaceProperties_s(face, props)
            centroid = _xyz(props.CentreOfMass())
            axis, position = "xyz"[index], centroid[index]
            key = (axis, position, centroid)
            if abs(normal[index]) > 0.999 and key in allowed:
                face_id = _face_id(face)
                boundary = []
                edges = TopExp_Explorer(face, TopAbs_EDGE)
                while edges.More():
                    curve = BRepAdaptor_Curve(TopoDS.Edge(edges.Current()))
                    first, last = curve.FirstParameter(), curve.LastParameter()
                    for i in range(9):
                        point = curve.Value(first + (last - first) * i / 8)
                        boundary.append((point.X(), point.Y(), point.Z()))
                    edges.Next()
                centre = props.CentreOfMass()
                candidates[face_id] = DatumCandidate(
                    face_id, axis, (centre.X(), centre.Y(), centre.Z())[index],
                    (centre.X(), centre.Y(), centre.Z()), props.Mass(), tuple(boundary))
                matched.add(key)
        explorer.Next()
    assert matched == allowed, "平面要素与 STEP 拓扑失配，不得静默丢弃候选"
    return tuple(candidates.values())


def _plane_options(shape):
    return tuple(Option(face.id, f"{face.axis.upper()}={_rounded(face.position):g} mm 的平面（{face.id}）",
                        None, f"来自 planar_faces；质心 {_quantized(face.centroid)}，面积 {face.area:g} mm²",
                        axis=face.axis) for face in datum_candidates_for(shape))


def _unconfirmed_datums(scheme, model, dimensions):
    inference = assumptions._datum_faces(model, scheme, dimensions)
    if inference is None:
        return None, ()
    letters = scheme.plan.datum.planes if scheme.plan.datum else ()
    datums = [(letter, axis, inference["value"][letter])
              for letter, axis in zip(letters, assumptions.DATUM_AXES)
              if letter in inference["value"] and letter not in dict(scheme.datum_choices)]
    return inference, datums


def datum_choice_questions(scheme, model, features, dimensions=None) -> tuple[Question, ...]:
    inference, datums = _unconfirmed_datums(scheme, model, dimensions)
    if not datums:
        return ()
    options = _plane_options(model.shape)
    if not options:
        options = (Option(_fingerprint("bbox", _quantized((model.origin, model.bbox))),
                          "维持当前包围盒占位基准", None,
                          "未识别到轴平行平面；这里只保留当前占位，不代表设计基准"),)
    return tuple(_question(
        kind=DATUM_CHOICE, geometry=(letter, axis, _rounded(position)),
        target=f"基准 {letter}（{axis.upper()} 向，当前 {position:g} mm）",
        claim=f"{inference['claim']}（占位，非设计基准）",
        basis=f"无依据（设计基准未确认）；当前占位依据：{inference['basis']}",
        impact=inference["impact"], options=options, datum=letter, axis=axis)
        for letter, axis, position in datums)


_ANGLE_OPTIONS = (Option("none", "不给", None, "图上不写角度未注公差"),
                  Option("pm0.5", "±0.5°", 0.5), Option("pm1", "±1°", 1.0),
                  Option("pm2", "±2°", 2.0))


def _unnoted_angles(scheme):
    return tuple(dim for dim in scheme.plan.constraints
                 if isinstance(dim, Sized) and dim.dof in ANGULAR_DOFS and dim.tolerance is None)


def unnoted_angle_questions(scheme) -> tuple[Question, ...]:
    dimensions = _unnoted_angles(scheme)
    if not dimensions:
        return ()
    inference = assumptions._tolerance(None, scheme)
    questions = [_question(
        kind=UNNOTED_ANGLE, geometry=_dim_id(dim, scheme), target=_target(dim, scheme),
        claim=f"{inference['claim']}；本条角度未归口未注公差",
        basis=f"无依据（角度未注公差）；{inference['basis']}",
        impact=f"未确认角度公差，车间验收可能不合设计意图；{inference['impact']}",
        options=_ANGLE_OPTIONS) for dim in dimensions]
    return tuple(sorted(questions, key=lambda q: (q.kind, q.target)))


def questions_for(model, features, scheme, dimensions=None) -> tuple[Question, ...]:
    questions = (special_tolerance_questions(scheme)
                 + datum_choice_questions(scheme, model, features, dimensions)
                 + unnoted_angle_questions(scheme))
    return tuple(sorted(questions, key=lambda q: (q.kind, q.target)))


def confirmation_warnings(model, scheme, dimensions, answers=()) -> tuple[str, ...]:
    """从问题的同一触发依据计数，默认出图不因此枚举平面或公差候选。"""
    targets = {_fingerprint("q", (SPECIAL_TOLERANCE, _dim_id(dim, scheme)))
               for dim, _why in _tolerance_dimensions(scheme)}
    targets.update(_fingerprint("q", (UNNOTED_ANGLE, _dim_id(dim, scheme)))
                   for dim in _unnoted_angles(scheme))
    _inference, datums = _unconfirmed_datums(scheme, model, dimensions)
    targets.update(_fingerprint("q", (DATUM_CHOICE, (letter, axis, _rounded(position))))
                   for letter, axis, position in datums)
    pending = targets - {answer.question_id for answer in answers}
    unknown = {answer.question_id for answer in answers if answer.chosen_option_id is None}
    warnings = []
    if pending:
        warnings.append(f"待人工确认 {len(pending)} 项：请在问题卡选择引擎候选或明确选择不知道；"
                        "未回答不代表已有设计依据")
    if unknown:
        warnings.append(f"工程师明确选择不知道 {len(unknown)} 项：相关要求仍未确定，"
                        "未填入默认值，交付前须人工复核")
    if model.excluded_geometry:
        warnings.append('输入还含有额外非实体几何，当前仅按唯一实体生成零件图；'
                        '原 STEP 保留，请核对导出范围。排除类型：'+', '.join(model.excluded_geometry))
    return tuple(warnings)

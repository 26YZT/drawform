"""基线尺寸方案生成。

可推与不可推的界线：尺寸「数值」全部可以从模型量出来，是客观事实；
「标哪些、从哪个基准量、给什么公差」是设计意图，推不出来。

所以这里只做一件站得住的事：按一条写死的策略把客观可量到的量全部钉住，
产出一套完整且必然过 linter 的基线方案。它的价值在于当**分数的下限** ——
真实图纸的标注值与它比对，才能算出「AI 到底会不会标尺寸」。

策略（写死，不假装是判断）：
- 外形三个方向总尺寸必标；
- 每个识别到的孔标直径、以及垂直于轴线的两个定位坐标；盲孔与沉孔那一级加标深度，
  贯通孔不标深度；
- 基准取包围盒最小角那个面（A/B/C），定位尺寸从它量起；
- 一律不指定差，交给标题栏一般公差覆盖。
"""

from __future__ import annotations

from dataclasses import dataclass
import math

from drawform.dimensioning.constraints import Chain, Datum, Plan, Sized
from drawform.dimensioning.general import general_tolerance_note
from drawform.geometry.features import (Bore, Gap, Shaft, bolt_circle,
                                        find_counterbores, group_bores,
                                        group_counterbores, group_shafts,
                                        outline_partitions)
from drawform.geometry.loader import LoadedModel

AXIS_DOFS = ("x", "y", "z")
OUTLINE_DOF = {0: "length", 1: "width", 2: "height"}
#: 壁厚/台阶只发「不超过外廓最大边 20%」的那几段。再宽的就是另一类量了。
GAP_RATIO = 0.20


@dataclass(frozen=True)
class DatumCandidate:
    """从 STEP 平面量得的基准事实，回答只能引用 id。"""
    id: str
    axis: str
    position: float
    centroid: tuple[float, float, float]
    area: float
    boundary: tuple[tuple[float, float, float], ...]


@dataclass(frozen=True)
class Scheme:
    plan: Plan
    unknowns: tuple[tuple[str, str], ...]
    #: 除了尺寸之外必须写在图上的话。一般公差注记尤其关键 —— 基线一律不指定差，
    #: 整个公差策略就建立在这句话被写出来之上。
    notes: tuple[str, ...] = ()
    #: 尺寸要落到图上，得有孔的轴线、入口端、投影出来的圆。带着走而不是让调用方
    #: 另外传一份 —— 忘了传就退化成"方案里 46 条深度、图上一条都没有"。
    features: tuple = ()
    origin: tuple = ()
    datum_candidates: tuple[DatumCandidate, ...] = ()
    datum_choices: tuple[tuple[str, str], ...] = ()
    #: 由唯一流水线从当前STEP与闭合选择重新验证的图前参考，不接受人工坐标。
    reference_facts: tuple = ()
    #: 只为严格二段实心轴保存已核验端面/肩缘点；其他零件为空。
    shoulder_supports: tuple = ()


def _perpendicular_indices(axis_direction) -> tuple[int, ...]:
    """沿轴方向那个坐标在轴线上是任取的，只有垂直于轴的两个分量才是定位尺寸。"""
    return tuple(i for i, c in enumerate(axis_direction) if abs(c) < 0.5)


#: 分布圆判据只有一份，落在 features 里 —— 方案层在这里发 PCD，中心线层在那边
#: 把同一个圆画成细点划线，两处必须判同一件事。
_bolt_circle = bolt_circle


PROJECTION_NOTE = {"first_angle": "视图按第一角画法", "third_angle": "视图按第三角画法"}

#: 定位尺寸小到这个量级就是**浮点噪声**：OCCT 把"孔轴正好落在基准面上"算成 1e-07
#: （真实语料 part_0011 是 1e-07、part_0056 是 5.8e-12），于是 JSON 与图纸上出现一个
#: 谁也不该读的数字。做法是**归零并保留这条尺寸** —— 不能删：删掉之后那个孔在这个
#: 方向上就没有任何定位依据了，而"0"表达的正是与基准面共面这件事。
#: 阈值只切噪声，不碰可制造的最小尺寸（真实语料里正当的 0.5 mm 有 4 件）。
ZERO_POSITION = 1e-6


def _emit_position(constraints, unknowns, feature, axis_index, value) -> None:
    """发一条定位尺寸：噪声先归零，尺寸与未知量始终成对出现。"""
    snapped = 0.0 if abs(value) < ZERO_POSITION else value
    constraints.append(Sized(feature, AXIS_DOFS[axis_index], snapped))
    unknowns.append((feature, AXIS_DOFS[axis_index]))


def derive_scheme(model: LoadedModel, features,
                  general_tolerance: str = "ISO 2768-m",
                  projection_mode: str = "first_angle", *, reference_facts=()) -> Scheme:
    constraints: list[Sized] = []
    unknowns: list[tuple[str, str]] = []

    for index, dof in OUTLINE_DOF.items():
        constraints.append(Sized("outline", dof, model.bbox[index]))
        unknowns.append(("outline", dof))

    bores = [f for f in features if isinstance(f, Bore)]
    by_id = {bore.feature_id: bore for bore in bores}
    # 沉孔的 ⌀ 属于沉孔那一池，不是普通直径：把它从孔组里摘出来单独发。
    # 它与引导孔同轴，定位由引导孔给出，自己不再发坐标（否则同自由度钉两次 = 超定）。
    counterbores = find_counterbores(bores)
    cbore_ids = {pair.cbore_id for pair in counterbores}

    for group in group_bores([b for b in bores if b.feature_id not in cbore_ids]):
        constraints.append(Sized(group.group_id, "diameter", group.diameter,
                                 quantity=group.count))
        unknowns.append((group.group_id, "diameter"))
        # 贯通孔不给深度：真实图纸上它只写直径，通不通由剖视/侧视自己说明。
        # 65 个真实零件实测 —— 每个孔都发深度会得到 118 条 ↧，真图纸一共只有 37 条。
        if not group.through and group.mouth is not None:
            constraints.append(Sized(group.group_id, "depth", group.depth,
                                     quantity=group.count))
            unknowns.append((group.group_id, "depth"))

        axes = _perpendicular_indices(group.axis_direction)
        anchors = {fact.feature_id:fact.point for fact in reference_facts}
        reference_axes = {fact.axes for fact in reference_facts if fact.feature_id in group.members}
        if reference_axes:
            from drawform.geometry.references import ReferenceUnavailable
            if len(reference_axes)!=1:
                raise ReferenceUnavailable('unsupported_reference_position','孔组参考定位方向不一致，未合并或删减约束')
            axes = tuple('xyz'.index(axis) for axis in next(iter(reference_axes)))
        plane_of = {bore.feature_id: tuple(anchors.get(bore.feature_id,bore.axis_point)[i] - model.origin[i] for i in axes)
                    for bore in (by_id[m] for m in group.members)}

        circle = _bolt_circle([plane_of[m] for m in group.members])
        if circle is not None:
            if reference_axes:
                raise ReferenceUnavailable('unsupported_oblique_pcd',
                    '首版不支持斜孔分布圆，需要工程师另行制图；未改用任意轴点或删减约束')
            center, radius = circle
            center_id = f"{group.group_id}:center"
            for offset, index in enumerate(axes):
                _emit_position(constraints, unknowns, center_id, index, center[offset])
            constraints.append(Sized(group.group_id, "pcd", 2.0 * radius,
                                     quantity=group.count))
            unknowns.append((group.group_id, "pcd"))
            continue

        # 组只合并「相同的量」；非螺栓圆的孔位每个都不同，必须逐个钉住
        for member in group.members:
            for offset, index in enumerate(axes):
                _emit_position(constraints, unknowns, member, index,
                               plane_of[member][offset])

    # 沉孔：`⌀引导孔 ⌴⌀沉孔 ↧沉孔深`。直径进 counterbore 池，深度进 depth 池。
    for group in group_counterbores(counterbores):
        constraints.append(Sized(group.group_id, "counterbore", group.diameter,
                                 quantity=group.count))
        constraints.append(Sized(group.group_id, "depth", group.depth,
                                 quantity=group.count))
        unknowns += [(group.group_id, "counterbore"), (group.group_id, "depth")]

    # 外圆：只发直径。台阶轴的定位由外形总尺寸与中心线给出，不是每个外圆各给两个坐标。
    for group in group_shafts([f for f in features if isinstance(f, Shaft)]):
        constraints.append(Sized(group.group_id, "diameter", group.diameter,
                                 quantity=group.quantity))
        unknowns.append((group.group_id, "diameter"))

    # 壁厚与台阶：相邻轴平行面之间的间隙。实测把 F2 从 73.1% 抬到 77.4%
    # （research/measure_wall_value.py），而且真实图纸上这一类是漏标主力。
    from drawform.geometry.stepped_shaft import two_stage_profile,unique_shaft_gap_ids
    shaft_profile=two_stage_profile(model)
    if shaft_profile:features=unique_shaft_gap_ids(features,shaft_profile)
    shoulder_supports=shaft_profile.supports(features) if shaft_profile else ()
    shoulder_ids={row[0] for row in shoulder_supports}
    gaps = [g for g in features if isinstance(g, Gap)
            and (g.gap <= GAP_RATIO * max(model.bbox) or g.feature_id in shoulder_ids)]
    # 首尾相接正好铺满外廓的那一串就是封闭尺寸链：四环全标实尺 + 总尺寸也标，
    # 四个数只能满足三个。按 GB/T 留一环不标（由总尺寸保证），留最大那一环 ——
    # 它的累积误差本就最大，工程上最该由总尺寸兜。
    demoted: set[str] = set()
    for axis, run in outline_partitions(gaps, model.bbox):
        drop = max(run, key=lambda g: g.gap)
        demoted.add(drop.feature_id)
        constraints.append(Chain(
            name=f"chain:{axis}",
            members=tuple((g.feature_id, "gap") for g in run),
            total=model.bbox[AXIS_DOFS.index(axis)],
            reference=((drop.feature_id, "gap"),)))
    for gap in gaps:
        unknowns.append((gap.feature_id, "gap"))
        if gap.feature_id not in demoted:
            constraints.append(Sized(gap.feature_id, "gap", gap.gap))

    plan = Plan(constraints=tuple(constraints),
                datum=Datum("A", "B", "C"),
                general_tolerance=general_tolerance)
    # 标题栏那句「一般公差」是我们自己写的，所以它必须说准：有尺寸落在该表覆盖段之外时，
    # 把这件事一起写上去，而不是留一句过宽的承诺。
    # 反过来"盖不住就拦下整张图"是错的做法 —— 0.5 mm 的壁厚是正当尺寸，
    # 真实语料里 4/65 件因此被拒等于让工具少做 6% 的活，而缺陷只是那句话没说全。
    notes = []
    note = general_tolerance_note(plan)
    if note:
        notes.append(note)
    if projection_mode in PROJECTION_NOTE:
        notes.append(PROJECTION_NOTE[projection_mode])

    return Scheme(
        plan=plan,
        unknowns=tuple(unknowns),
        notes=tuple(notes),
        features=tuple(features),
        origin=model.origin,
        reference_facts=tuple(reference_facts),
        shoulder_supports=shoulder_supports,
    )

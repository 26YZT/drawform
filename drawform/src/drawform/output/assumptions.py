"""出图说明：机器替人做了哪些决定。

把系统实际做出的决定、依据、以及它会影响哪些数字，写成清单随图交付。
需要人选择的内容通过 questions 通道确认；这份说明不代替问题卡或人工制图审核。

这里的每一条都是从 `model / scheme / dimensions / marks / verdict` 里**算出来的**，
没有一条是写死的措辞。写死一句"基准取包围盒最小角"而不去读实际用的那个坐标，
这份说明就变成第二个"看起来确定、实际没验证"的东西 —— 本项目已经为此栽过四次。
"""

from __future__ import annotations

from drawform.delivery import unplaced_dimensions
from drawform.dimensioning.constraints import Chain, Sized
from drawform.dimensioning.general import general_tolerances
from drawform.geometry.projection import STANDARD_VIEWS
from drawform.output.titleblock import BLOCK_H, BLOCK_W, CHANGE_LABELS, SIGN_ROLES

#: 定位尺寸用的世界轴 → 基准字母的位置。`Plan.datum` 按 A/B/C 顺序接收，
#: 与 `place.py` 里 `WORLD_AXES` 的顺序是同一个约定。
DATUM_AXES = ("x", "y", "z")


def _datum_faces(model, scheme, dimensions=None):
    """真被引用到的基准，以及它在世界坐标里到底是哪个值。"""
    letters = scheme.plan.datum.planes if scheme.plan.datum else ()
    used = ({c.dof for c in scheme.plan.constraints if isinstance(c, Sized)}
            if dimensions is None else
            {d.axis for d in dimensions if d.kind == "position" and d.axis})
    value = {}
    selected = dict(scheme.datum_choices)
    candidates = {face.id: face for face in scheme.datum_candidates}
    for index, axis in enumerate(DATUM_AXES):
        if axis in used and index < len(letters) and index < len(model.origin):
            face = candidates.get(selected.get(letters[index]))
            value[letters[index]] = face.position if face is not None else model.origin[index]
    if not value:
        return None
    return {
        "id": "datum-face",
        "claim": ("定位尺寸使用工程师选定的 STEP 基准面；未选择方向仍取包围盒最小边"
                  if selected else "所有定位尺寸从包围盒最小角那三个面量起"),
        "basis": ("人从 STEP 平面候选选择；基准坐标与定位数值由引擎计算"
                  if selected else "place.py 取投影包围盒最小边"),
        "value": value,
        "impact": "换任何一个基准面，本图所有定位尺寸的数值都会变；"
                  "图纸上的基准代号 A/B/C 标的是这个面，不是设计基准",
    }


def _tolerance(model, scheme):
    sized = [c for c in scheme.plan.constraints if isinstance(c, Sized)]
    specified = sum(1 for c in sized if c.tolerance is not None)
    note = next((n for n in scheme.notes if n.startswith("一般公差")), None)
    rows = general_tolerances(scheme.plan)
    covered = [row for row in rows if row["covered"]]
    gaps = [row for row in rows if not row["covered"]]
    # 影响那句必须**从真实缺口推**，不能写死：语料里未覆盖的全是 0.5 mm 的壁厚/深度，
    # 一句写死的"角度是另一张表"会把复核人引到一个不存在的理由上。
    reasons = {
        "angular_table_missing": "角度未注公差是另一张表，还没核对",
        "out_of_range": "有尺寸超出该表的覆盖段，需专门公差",
        "blank_cell": "所声明的等级在该尺寸段是空格，标准没给值",
        "coincidence": "0 是与基准面共面，不是一段长度",
        "standard_unverified": "声明的标准我们没核对过，不替它报数",
    }
    kinds = sorted({row["kind"] for row in gaps})
    impact = ("需要配作/装配的尺寸（如轴承孔）不会被特别控制，"
              "验收时只能按一般公差判")
    if kinds:
        impact += "；未覆盖的 " + str(len(gaps)) + " 条：" + "、".join(
            reasons.get(kind, kind) for kind in kinds)
    return {
        "id": "general-tolerance",
        "claim": "没有任何一条尺寸带指定公差，全部由标题栏一般公差覆盖"
                 if specified == 0 else
                 f"{specified} 条尺寸带指定公差，其余走一般公差",
        "basis": "scheme.derive_scheme 一律不填 tolerance 字段；"
                 "关键尺寸需要专门公差，但 STEP 里没有这个信息。"
                 "未注公差的逐条数值由 rules/iso2768.py 的表算出（两份独立转录核对过）",
        "value": {"total": len(sized), "with_explicit_tolerance": specified,
                  "note": note,
                  "covered": len(covered), "uncovered": len(gaps),
                  "narrowest": min((r["plus_minus"] for r in covered), default=None),
                  "widest": max((r["plus_minus"] for r in covered), default=None)},
        "impact": impact,
    }


def _chains(model, scheme):
    chains = [c for c in scheme.plan.constraints if isinstance(c, Chain)]
    if not chains:
        return None
    return {
        "id": "open-chains",
        "claim": f"{len(chains)} 处尺寸链各有一环故意不标实尺",
        "basis": "GB/T 4458.1：封闭尺寸链的组成环与封闭环全标实尺时，"
                 "其中一个必为冗余。留的是该链里最大那一环（累积误差本就最大）",
        "value": {"count": len(chains), "chains": [
            {"name": c.name, "total": c.total,
             "left_open": [link for link, _dof in c.reference],
             "members": len(c.members)} for c in chains]},
        "impact": "被留空的那一环由总尺寸保证，不单独检验；"
                  "若客户要求逐环控制，需要改标法而不是补标",
    }


def _not_drawn(model, scheme, dimensions, verdict):
    if verdict is None:
        return None
    unplaced = unplaced_dimensions(scheme, dimensions)
    if not unplaced:
        return None
    return {
        "id": "not-drawn",
        "claim": f"{len(unplaced)} 条尺寸在方案里，但没画到图上",
        "basis": "落位层找不到能如实表达它的投影位置。例如奇数个孔的分布圆："
                 "取不到直径相对的两个圆心时，画出来的线段长度不等于标注值，"
                 "宁可不画",
        "value": {"unplaced": [list(item) for item in unplaced]},
        "impact": "这些量在图纸上缺失，需要人工补；不是漏标，是明知画不对而不画",
    }


def _views(model):
    return {
        "id": "views-fixed",
        "claim": "视图是固定三视图，不是按这个零件选的",
        "basis": "pipeline 恒投 STANDARD_VIEWS；"
                 "\"这个零件要几个视图、从哪投、剖哪里\"是制图的第一号决策，产品未参与",
        "value": {"chosen": False,
                  "views": [d.name for d in STANDARD_VIEWS]},
        "impact": "复杂件可能需要第四个视图或局部剖；本工具不会自己加，"
                  "也不会因为表达不清而报警",
    }


def _title_block():
    return {
        "id": "title-block-unverified",
        "claim": "标题栏已有更改区、签字区、其他区、名称及代号区；"
                 f"外廓沿用已核对的 {BLOCK_W:g} × {BLOCK_H:g}max，内部分区仍有未核对项",
        "basis": "GB/T 10609.1-2008 的既有核对记录见 docs/design/gbt10609_titleblock.md；"
                 "当前 titleblock.py 使用三列布局，更改区表头为"
                 + "/".join(CHANGE_LABELS) + "，签字角色为" + "/".join(SIGN_ROLES),
        "value": {"standard": "GB/T 10609.1-2008", "outline_verified": True,
                  "layout_verified": False, "outline": [BLOCK_W, BLOCK_H],
                  "allowed_layouts": ["图2", "图3"]},
        "impact": "中列与右列内部行高未逐格核对，完整布局不得声称已符合该标准；"
                  "图样名称、材料和签字人没有输入依据时保持留空，仍须工程师制图审核",
    }


def assumptions_for(model, scheme, dimensions, marks=(), verdict=None, answers=()) -> list[dict]:
    """列出这张图里机器替人做的决定。顺序稳定，便于人和程序逐条核对。"""
    items = [
        _datum_faces(model, scheme, dimensions),
        _tolerance(model, scheme),
        _chains(model, scheme),
        _not_drawn(model, scheme, dimensions, verdict),
        _views(model),
        _title_block(),
    ]
    if model.excluded_geometry:
        items.append({
            'id': 'geometry-scope',
            'claim': '本次按 STEP 中的唯一实体生成零件图，额外非实体几何不参与尺寸与投影',
            'basis': 'STEP 转移结果的实体数量与根几何类型；未推断非实体几何的设计用途',
            'value': {'excluded_geometry': list(model.excluded_geometry)},
            'impact': '原 STEP 保留；额外面/线/点未进入当前零件图，请核对导出范围',
        })
    unknown = [answer.question_id for answer in answers if answer.chosen_option_id is None]
    if unknown:
        items.append({
            "id": "human-unknown",
            "claim": "工程师也选择了不知道，相关公差未注、占位基准未被确认为设计基准",
            "basis": "工程师显式选择不知道；没有填入默认值",
            "value": {"questions": unknown},
            "impact": "这些要求仍需人工复核，不能把已回答当成已确认",
        })
    return [item for item in items if item]

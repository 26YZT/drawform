"""交付门：把已经量出来的事实变成一次拒绝。

门组合几何层的有效性、孔端肩面与入口核验，以及方案层的尺寸合法性检查。
几何计算仍由几何模块负责。这一层的职责只有一件事：**不过关就不出图**。

之所以要单独有这一层：那些事实如果只是被记录、不被消费，等于发现了问题却允许
它通过。检查器自身抛异常一律记为不通过（不是跳过），否则门第一版就会在最该拦
的地方放行。
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math

from drawform.dimensioning.constraints import Plan, Sized, check_plan

ALLOW = "allow"
BLOCK = "block"


@dataclass(frozen=True)
class Verdict:
    decision: str
    #: 没过的那几项的稳定 id。下游（图上的提示、转人工的问题清单、测试断言）都按这个
    #: 认，不按中文句子认 —— 措辞一改就会把不相干的地方改挂。
    failed: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    violations: tuple = ()
    #: 待确认与明确未知是附加信息，不参与 allowed / BLOCK 判定。
    warnings: tuple[str, ...] = ()

    @property
    def allowed(self) -> bool:
        return self.decision == ALLOW


INVALID_SOLID = "invalid_solid"
MULTIPLE_SOLIDS = "multiple_solids"
UNCLASSIFIED_FACES = "unclassified_faces"
ILLEGAL_PLAN = "illegal_plan"
UNPLACED_DIMENSIONS = "unplaced_dimensions"
INCONSISTENT_DIMENSIONS = "inconsistent_dimensions"
COUNTERBORE_SEMANTICS = "counterbore_semantics"


def unplaced_dimensions(scheme, placed) -> list[tuple[str, str]]:
    """方案里发了、图纸上却没画出来的尺寸。

    这一项存在的全部理由：落位层曾经只覆盖方案的 51%，322 条孔的定位尺寸一条没画，
    而 recall/precision 完全看不出来 —— 定位数值在分池配对里命中了，纸面上却没有。
    """
    drawn = {item.source for item in placed if item.source is not None}
    drawn.update(source for item in placed for source in item.shared_sources)
    return [(c.feature, c.dof) for c in scheme.plan.constraints
            if isinstance(c, Sized) and (c.feature, c.dof) not in drawn]


def check_delivery(*, is_valid: bool, unclassified_faces: int, plan, unknowns,
                   unplaced: tuple = (),
                   extra_checks: tuple[tuple[str, callable], ...] = ()) -> Verdict:
    """串行走完所有检查，任何一条不过就 BLOCK —— 不短路，一次把问题报齐。"""
    failed: list[str] = []
    reasons: list[str] = []

    if not is_valid:
        failed.append(INVALID_SOLID)
        reasons.append("实体未通过几何有效性检查，特征识别结果不可信")
    if unclassified_faces:
        failed.append(UNCLASSIFIED_FACES)
        reasons.append(f"{unclassified_faces} 个圆柱面判不出内外，有特征被悄悄丢掉")

    violations = check_plan(plan, unknowns)
    if violations:
        failed.append(ILLEGAL_PLAN)
        for item in violations:
            reasons.append(f"尺寸方案不合法：{item.message}")

    if unplaced:
        failed.append(UNPLACED_DIMENSIONS)
        reasons.append(f"{len(unplaced)} 条尺寸在方案里发了却没落到图上：{list(unplaced)[:4]}")

    for name, check in extra_checks:
        try:
            reason = check(plan=plan, unknowns=unknowns, is_valid=is_valid,
                           unclassified_faces=unclassified_faces)
        except Exception as error:
            failed.append(name)
            reasons.append(f"检查器「{name}」异常，按不通过处理：{error!r}")
            continue
        if reason:
            failed.append(name)
            reasons.append(f"{name}：{reason}")

    return Verdict(decision=BLOCK if failed else ALLOW, failed=tuple(failed),
                   reasons=tuple(reasons), violations=tuple(violations))


class DeliveryBlocked(Exception):
    """门拦下了这张图。带上没过的项目 id，调用方不用解析中文。"""

    def __init__(self, verdict: Verdict):
        super().__init__("；".join(verdict.reasons))
        self.failed = verdict.failed
        self.verdict = verdict


def gate_for(model, scheme=None, placed=None, *, projections=None) -> Verdict:
    """把装载层与特征层已经产出的事实组装成一次判定 —— 只有一处接线，调用方不会接错。

    `scheme` 省略时只查装载层就能知道的事实：**这两项必须能在投影之前就拦下来**，
    无效实体会先让视图层跑出裸异常，排在后面的门等于没门。
    """
    from drawform.geometry.features import unclassified_face_count

    if model.solid_count > 1:
        return Verdict(BLOCK, (MULTIPLE_SOLIDS,),
                       (f"检测到 {model.solid_count} 个实体，只支持单体零件。请在 CAD 中拆成单体，逐件导出 STEP 后重新上传。",))
    if model.solid_count == 0:
        return Verdict(BLOCK, (INVALID_SOLID,), ("未找到可制图的实体，请导出一个单体实体零件。",))
    unclassified = unclassified_face_count(model.shape)
    if scheme is None:
        return check_delivery(is_valid=model.is_valid,
                              unclassified_faces=unclassified,
                              plan=Plan(()), unknowns=())
    def measurements(**_facts):
        from drawform.dimensioning.place import measurement_errors, diameter_source_errors
        errors = measurement_errors(scheme, placed) + diameter_source_errors(scheme, placed, projections)
        return f'{len(errors)} 处尺寸事实不一致：{errors[:4]}' if errors else None

    def counterbores(**_facts):
        from drawform.geometry.features import Bore, find_counterbores, group_counterbores, group_bores
        from drawform.geometry.bore_topology import mouth_region_clear
        bores = [f for f in scheme.features if isinstance(f, Bore)]
        by_id = {b.feature_id: b for b in bores}
        pairs = find_counterbores(bores)
        valid = {}; blocked_mouth = set()
        for pair in pairs:
            bore = by_id[pair.cbore_id]
            if not bore.topology_checked or bore.axis_span is None or pair.mouth is None:
                continue
            floor = 0 if pair.mouth == bore.axis_span[1] else 1
            if pair.pilot_id in bore.shoulder_neighbors[floor]:
                if mouth_region_clear(model, bore, pair.mouth):
                    valid[pair.cbore_id] = pair
                else:
                    blocked_mouth.add(pair.cbore_id)
        groups = {g.group_id: g for g in group_counterbores(pairs)}
        closed = {group.group_id for group in group_bores(bores)
                  if any(by_id[member].internal_enlargement for member in group.members)}
        closed.update(b.feature_id for b in bores if b.internal_enlargement)
        errors = []
        for constraint in scheme.plan.constraints:
            if not isinstance(constraint, Sized):
                continue
            if constraint.dof == "depth" and constraint.feature in closed:
                errors.append(f"{constraint.feature} 为两端经肩面转入小孔的内部扩腔，不能标单入口深度")
                continue
            if constraint.dof != "counterbore" and not (constraint.dof == "depth" and constraint.feature.startswith("cbore-")):
                continue
            group = groups.get(constraint.feature)
            if group is not None and any(member in blocked_mouth for member in group.members):
                errors.append(f"{constraint.feature} 的拟定入口邻域被材料阻挡，不能作为开口的沉孔")
                continue
            if group is None or any(member not in valid for member in group.members):
                errors.append(f"{constraint.feature} 缺少大孔端部经真实肩面连接引导孔的依据")
                continue
            value = group.diameter if constraint.dof == "counterbore" else group.depth
            if not math.isclose(constraint.value, value, rel_tol=0, abs_tol=1e-7) or constraint.quantity != group.count:
                errors.append(f"{constraint.feature} 的{constraint.dof}数值或数量与已核验孔组不符")

        # 一个沉孔组同时发 ⌀ 与 ↧ 两条约束，同一个「缺肩面」事实会被这两条各报一次。
        # 去重按句子而不是按约束：人读的是一次事故，不是两条。顺序保留，先报先算。
        unique = list(dict.fromkeys(errors))
        return "；".join(unique) if unique else None

    def shaft_dimensions(**_facts):
        from drawform.geometry.stepped_shaft import shaft_scheme_errors
        errors=shaft_scheme_errors(model,scheme,placed)
        return '；'.join(errors) if errors else None

    checks = ((COUNTERBORE_SEMANTICS, counterbores),('stepped_shaft_dimensions',shaft_dimensions))
    if placed is not None:
        checks += ((INCONSISTENT_DIMENSIONS, measurements),)
    return check_delivery(is_valid=model.is_valid, unclassified_faces=unclassified,
                          plan=scheme.plan, unknowns=scheme.unknowns,
                          unplaced=() if placed is None else unplaced_dimensions(scheme, placed),
                          extra_checks=checks)


def require_allowed(verdict: Verdict) -> Verdict:
    """出图前调用：不过关就抛。返回值让人可能忘记检查，异常不会。"""
    if not verdict.allowed:
        raise DeliveryBlocked(verdict)
    return verdict


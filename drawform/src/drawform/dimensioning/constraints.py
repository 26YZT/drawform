"""尺寸方案合法性检查（linter）。

判据不依赖任何参考答案：把「一套尺寸」看成「一组关于未知量的方程」。
- 有未知量没被钉住 → 欠定：照着这套尺寸造不出唯一零件；
- 尺寸链的组成环和封闭环都标实尺且没标参考 → 超定：无意闭合尺寸链；
- 有定位尺寸却没声明基准 → 尺寸没有出发依据；
- 钉住的量既无显式公差、标题栏也没有一般公差 → 验收时无据可依。

前两条是关于模型几何的事实，可机械判定，与设计师的意图无关；后两条是制图规范。
它同时是将来尺寸生成层的目标函数。
"""

from __future__ import annotations

from dataclasses import dataclass

UNDER_CONSTRAINED = "under_constrained"
OVER_CONSTRAINED = "over_constrained"
CLOSED_CHAIN = "closed_chain"
NO_DATUM = "no_datum"
NO_TOLERANCE_HOME = "no_tolerance_home"

#: 尺寸类型里属于「定位」的那些，必须有基准才能存在。
LOCATION_DOFS = frozenset({"x", "y", "z", "angle"})

#: general_tolerance 的第三种取值：方案里根本没提，就不检查这一项。
UNDECLARED = object()


@dataclass(frozen=True)
class Sized:
    """一条把某个自由度钉在数值上的尺寸。

    quantity 是「这个值适用于几个相同特征」，出图时写成 `4×⌀9` 而不是四条直径。
    """
    feature: str
    dof: str
    value: float
    tolerance: str | None = None
    quantity: int = 1


@dataclass(frozen=True)
class Chain:
    """一条尺寸链：组成环都标了实尺；total 非空表示封闭环也标了实尺。

    reference 列出被降级成参考尺寸（括号尺寸）的环节 —— 有它链才不超定。
    """
    name: str
    members: tuple[tuple[str, str], ...]
    total: float | None = None
    reference: tuple[tuple[str, str], ...] = ()


class Datum:
    """基准体系，按位置接收第一/第二/第三基准字母。"""

    __slots__ = ("planes",)

    def __init__(self, *planes: str):
        self.planes = planes

    def __bool__(self) -> bool:
        return bool(self.planes)


@dataclass(frozen=True)
class Plan:
    constraints: tuple
    datum: Datum | None = None
    general_tolerance: object = UNDECLARED


@dataclass(frozen=True)
class Violation:
    kind: str
    target: object
    message: str


def unknowns_for(outline: tuple[str, ...], features: dict[str, tuple[str, ...]]):
    """列出零件的全部未知量：外形尺寸 + 每个特征的自由度。"""
    unknowns = [("outline", dof) for dof in outline]
    for feature, dofs in features.items():
        unknowns.extend((feature, dof) for dof in dofs)
    return tuple(unknowns)


def _equations(constraints) -> dict[tuple[str, str], list]:
    """每个未知数收到几条**实尺**。超过一条 = 超定。

    只数直接标注的实尺。尺寸链不提供单环数值（它提供的是环与环的关系），
    把链算成"钉住某一环"会把"链 + 该环实尺"这种正常标注误判成矛盾。
    """
    equations: dict[tuple[str, str], list] = {}
    for item in constraints:
        if isinstance(item, Sized):
            equations.setdefault((item.feature, item.dof), []).append(item.value)
    return equations


def _pinned(constraints):
    """哪些未知量被这套尺寸**唯一确定**。

    一条链是**一个方程**：`total` 已知时它只能解出一个未知量 —— 就是被指定为参考
    （或由链算出）的那一环，前提是其余各环都已经钉住。`total` 未知时这个方程有
    n+1 个未知量，一个环都钉不住。

    旧实现无条件把"链上除参考环外的环"算成已钉住，于是**一条都没标实尺的链能冒充
    完整尺寸**通过欠定检查（GEO-012）。
    """
    pinned = set()
    for item in constraints:
        if isinstance(item, Sized):
            pinned.add((item.feature, item.dof))

    chains = [item for item in constraints if isinstance(item, Chain)]
    changed = True
    while changed:                     # 链可以互相解锁：A 链钉住的环可能正是 B 链的前提
        changed = False
        for chain in chains:
            if chain.total is None:
                continue
            derived = set(chain.reference)
            loose = [m for m in chain.members if m not in pinned]
            if not derived:
                if len(loose) != 1:
                    continue           # 没说哪一环由链算出来，且未定的不止一环 → 解不出
                derived = set(loose)
            others = [m for m in chain.members if m not in derived]
            if not all(m in pinned for m in others):
                continue
            for target in derived:
                if target not in pinned:
                    pinned.add(target)
                    changed = True
    return pinned


def check_plan(plan: Plan, unknowns) -> tuple[Violation, ...]:
    violations: list[Violation] = []
    pinned = _pinned(plan.constraints)

    for unknown in unknowns:
        if unknown not in pinned:
            violations.append(Violation(
                UNDER_CONSTRAINED, unknown,
                f"{unknown[0]}.{unknown[1]} 没有任何尺寸钉住，按此尺寸集造不出唯一零件"))

    for item in plan.constraints:
        if isinstance(item, Chain) and item.total is not None and not item.reference:
            violations.append(Violation(
                CLOSED_CHAIN, item.name,
                f"尺寸链「{item.name}」的组成环与封闭环全部标了实尺，"
                "其中一个必为冗余；应把一环改成参考尺寸或去掉封闭环"))

    for target, values in _equations(plan.constraints).items():
        if len(values) < 2:
            continue
        known = sorted({v for v in values if v is not None})
        if len(known) > 1:
            message = (f"{target[0]}.{target[1]} 被 {len(values)} 条尺寸钉住且数值互相矛盾 "
                       f"{known}，照这套尺寸造不出任何零件")
        else:
            message = (f"{target[0]}.{target[1]} 被 {len(values)} 条尺寸重复钉住，"
                       "同一无知数不需要两条方程，且重复标注会把封闭尺寸链藏起来")
        violations.append(Violation(OVER_CONSTRAINED, target, message))

    located = any(isinstance(c, Sized) and c.dof in LOCATION_DOFS for c in plan.constraints)
    if located and plan.datum is None:
        violations.append(Violation(
            NO_DATUM, "plan", "存在定位尺寸但未声明基准，尺寸没有测量出发依据"))

    if plan.general_tolerance is None:
        for item in plan.constraints:
            if isinstance(item, Sized) and item.tolerance is None:
                violations.append(Violation(
                    NO_TOLERANCE_HOME, (item.feature, item.dof),
                    f"{item.feature}.{item.dof} 既无显式公差，标题栏也无一般公差，验收无据"))

    return tuple(violations)

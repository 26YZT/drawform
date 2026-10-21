"""把基线尺寸方案和真实图纸的标注值对分。

配对**按量的是什么分类**：直径只和直径配，线性尺寸只和线性尺寸配。
只按裸数值配对的话，一个 ⌀9 会去蹭图上恰好等于 9 的深度或板厚，白送命中。
实测这个影响是 recall −2.6pt、precision −1.7pt（65 个零件），量级不大但方向明确：
不分类的分数偏高，且偏多少随零件的数值分布漂移，所以不能要。

定位尺寸从哪个基准量起属于设计意图，基线一律从包围盒最小角量，真实图纸可能从
中心或别的特征量，数值自然不同 —— 那是要量出来的差距，不是要修掉的 bug。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

GROUPS = ("diameter", "linear", "depth", "counterbore", "countersink",
          "chamfer", "radius", "angle", "pcd")

#: 真实标注 token 类型 → 评分分组。每类量各自一池：直径、深度、沉孔、倒角、圆角、
#: 角度、螺栓圆直径互不是一回事，混池会让全对的判定判成没命中。
#: `unknown`（`H n` 那 47 处）没有可靠解释，不计入分母 —— 这会让 recall 偏高，
#: 但把说不清的东西算成"你漏标了"同样是不诚实的，所以宁可显式记下这笔。
REAL_TOKEN_GROUP = {"diameter": "diameter", "size": "linear", "depth": "depth",
                    "counterbore": "counterbore", "countersink": "countersink",
                    "chamfer": "chamfer", "radius": "radius", "angle": "angle",
                    "pcd": "pcd"}

#: 修饰语不是「被标出来的量」，不进分母。
MODIFIER_TOKENS = frozenset({"count", "tolerance", "spotface", "across_flats", "unknown"})

#: 基线产出的自由度 → 评分分组，未列出的落进线性组。
#: counterbore 必须单列：真实标注把 `⌴⌀6,5` 的数值放在沉孔池，混进直径池两边都错。
DOF_GROUP = {"diameter": "diameter", "pcd": "pcd", "depth": "depth",
             "counterbore": "counterbore"}

#: 图纸上的字是按 GB/T 字高排出来的，读数容差取 0.02 mm（够吸收 22,50 与 22.504 的
#: 书写差，又不会把 ⌀22.4 与 ⌀22.6 混成一个值）。
ABS_TOL = 0.02


#: 生产风险的不对称系数。漏标一条与多标一条的代价不相等：漏一个孔的定位尺寸，
#: 这批件可能整批报废；多标一条只是图上啰嗦。文档里的判断是"差一个量级"，
#: 而 F_β 里 recall 的相对权重是 β² —— β=√10 才是那个量级。
#: 默认取 2.0（≈4 倍）而不是 √10：这是刻意保守的一档，把判断留在明面上，
#: 等有真实复核意见之后再动。β=1.0 退回 F1，用于和历史数对比。
STEERING_BETA = 2.0


@dataclass(frozen=True)
class Score:
    sample_id: str
    matched: int
    real_total: int
    generated_total: int
    #: 值已经覆盖、但书写次数少于真实图纸的去重量。只报告、不计分：
    #: 计进 recall 就把激励改成"多标才得高分"，扔掉则看不见"8 个孔只标了 1 个"。
    shortfall: int = 0

    @property
    def recall(self) -> float:
        return self.matched / self.real_total if self.real_total else 1.0

    @property
    def precision(self) -> float:
        return self.matched / self.generated_total if self.generated_total else 0.0

    def f(self, beta: float = STEERING_BETA) -> float:
        """F_β：β 越大越偏重"别漏标"。

        分母为 0 只可能发生在"一条都没标"上，那是最坏情况而不是完美情况 ——
        recall 在真实值为空时返回 1.0 的宽容不能传染到这里。
        """
        weight = beta * beta
        denominator = weight * self.precision + self.recall
        if not denominator:
            return 0.0
        return (1.0 + weight) * self.precision * self.recall / denominator


def _pair(left: Sequence[float], right: Sequence[float], abs_tol: float) -> list[tuple[float, float]]:
    """两侧各自排序后就近配对，每个值最多消耗一次。"""
    a, b = sorted(left), sorted(right)
    i = j = 0
    pairs: list[tuple[float, float]] = []
    while i < len(a) and j < len(b):
        if abs(a[i] - b[j]) <= abs_tol:
            pairs.append((a[i], b[j]))
            i += 1
            j += 1
        elif a[i] < b[j]:
            i += 1
        else:
            j += 1
    return pairs


def match_values(real: Sequence[float], generated: Sequence[float],
                 abs_tol: float = ABS_TOL) -> int:
    return len(_pair(real, generated, abs_tol))


def _buckets(values: Sequence[float], abs_tol: float = ABS_TOL) -> dict[float, int]:
    """按显示容差把一侧的值归并成 {代表值: 被书写几次}。"""
    buckets: dict[float, int] = {}
    last = None
    for value in sorted(values):
        if last is not None and abs(last - value) <= abs_tol:
            buckets[last] += 1
            continue
        buckets[value] = 1
        last = value
    return buckets


def real_value_groups(annotations) -> dict[str, list[float]]:
    groups: dict[str, list[float]] = {g: [] for g in GROUPS}
    for annotation in annotations:
        if annotation.type not in ("dimension", "callout"):
            continue
        for token in annotation.tokens:
            group = REAL_TOKEN_GROUP.get(token.kind)
            if group:
                groups[group].append(float(token.value))
    return groups


def generated_value_groups(plan) -> dict[str, list[float]]:
    from drawform.dimensioning.constraints import Sized

    groups: dict[str, list[float]] = {g: [] for g in GROUPS}
    for item in plan.constraints:
        if isinstance(item, Sized):
            groups[DOF_GROUP.get(item.dof, "linear")].append(float(item.value))
    return groups


def score_groups(real: dict[str, list[float]], generated: dict[str, list[float]]) -> Score:
    """按「这个零件的图纸声明了哪些量」计分，不按声明了几次。"""
    matched = real_total = generated_total = shortfall = 0
    for group in GROUPS:
        rb = _buckets(real.get(group, ()))
        gb = _buckets(generated.get(group, ()))
        pairs = _pair(rb, gb, ABS_TOL)
        matched += len(pairs)
        real_total += len(rb)
        generated_total += len(gb)
        for real_value, generated_value in pairs:
            if rb[real_value] > gb[generated_value]:
                shortfall += 1
    return Score(sample_id="", matched=matched, real_total=real_total,
                 generated_total=generated_total, shortfall=shortfall)


def score_sample(sample, model, features, sample_id: str = "") -> Score:
    from drawform.dimensioning.scheme import derive_scheme

    scheme = derive_scheme(model, features)
    score = score_groups(real_value_groups(sample.annotations),
                         generated_value_groups(scheme.plan))
    return Score(sample_id or sample.sample_id, score.matched,
                 score.real_total, score.generated_total)

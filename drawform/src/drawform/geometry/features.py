from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Iterator

from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepClass3d import BRepClass3d_SolidClassifier
from OCP.BRepGProp import BRepGProp
from OCP.GeomAbs import GeomAbs_Cylinder, GeomAbs_Plane
from OCP.GProp import GProp_GProps
from OCP.Precision import Precision
from OCP.TopAbs import TopAbs_FACE, TopAbs_IN
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS, TopoDS_Shape
from OCP.gp import gp_Pnt

XYZ = tuple[float, float, float]


def _xyz(p) -> XYZ:
    return (p.X(), p.Y(), p.Z())


def _sub(a: XYZ, b: XYZ) -> XYZ:
    return tuple(x - y for x, y in zip(a, b))


def _add(a: XYZ, b: XYZ) -> XYZ:
    return tuple(x + y for x, y in zip(a, b))


def _mul(a: XYZ, k: float) -> XYZ:
    return (a[0] * k, a[1] * k, a[2] * k)


def _dot(a: XYZ, b: XYZ) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


@dataclass(frozen=True)
class Feature:
    feature_id: str


@dataclass(frozen=True)
class Bore(Feature):
    """内圆柱面 —— 孔、沉孔、螺纹底孔的几何载体。"""

    diameter: float
    #: 轴线上一点。沿轴方向那个分量是任取的，定位只认垂直于轴的两个分量。
    axis_point: tuple[float, float, float]
    axis_direction: tuple[float, float, float]
    depth: float
    #: 两端都开口（材料被贯穿）。真实图纸对贯通孔只给直径、不给深度，
    #: 所以这个布尔值决定一条尺寸要不要发。
    through: bool = False
    #: 孔的两个端面在轴线方向上的投影坐标 (小, 大)。轴线上那个分量是任取的，
    #: 但这一对端点把"孔在轴向上占哪一段"说死了 —— 沉孔配对靠它。
    axis_span: tuple[float, float] | None = None
    #: 入口端的轴向投影坐标（刀具进来的那一面）。贯通孔两端都开口，没有单一入口 → None。
    #: 深度尺寸 `↧` 必须从这一端量起，只有 axis_span 分不出上下。
    mouth: float | None = None
    #: 两个有限端部经同一真实肩面连接到的更小同轴内孔；不是轴向区间重叠。
    shoulder_neighbors: tuple[tuple[str, ...], tuple[str, ...]] = ((), ())
    topology_checked: bool = False
    end_tolerances: tuple[float, float] = (0.0, 0.0)

    @property
    def internal_enlargement(self) -> bool:
        """两端都通过肩面转入小孔，不是具有单一大口入口的沉孔。"""
        return bool(self.shoulder_neighbors[0] and self.shoulder_neighbors[1])

    def canonical_axis(self) -> XYZ:
        """把轴向定成一个固定朝向：轴向坐标要可比，不能跟着每个面的参数化反号。"""
        index = max(range(3), key=lambda k: abs(self.axis_direction[k]))
        return (self.axis_direction if self.axis_direction[index] > 0
                else tuple(-c for c in self.axis_direction))

    def axis_offset(self) -> XYZ:
        """轴线上离全局原点最近的点 —— 去掉了轴向那个任取的分量。"""
        axis = self.axis_point
        canon = self.canonical_axis()
        return _sub(axis, _mul(canon, _dot(axis, canon)))

    def point_at(self, axial: float) -> XYZ:
        """轴线上给定轴向坐标的三维点，与 mouth / axis_span 同一把标尺。"""
        return _add(self.axis_offset(), _mul(self.canonical_axis(), axial))

    def floor(self) -> float | None:
        """孔底端的轴向坐标：`axis_span` 里不是入口的那一端。"""
        if self.mouth is None or self.axis_span is None:
            return None
        low, high = self.axis_span
        return low if self.mouth == high else high


@dataclass(frozen=True)
class Shaft(Feature):
    """外圆柱面 —— 轴颈、法兰外圆、台阶面的母面，与 Bore 互为反面。

    回转体零件的 ⌀ 大多标在外圆上：实测 65 个真实零件，只认内圆柱面时直径池
    79/149 命中，补上外圆能到 131。
    """

    diameter: float
    axis_point: tuple[float, float, float]
    axis_direction: tuple[float, float, float]
    length: float


@dataclass(frozen=True)
class PlanarFace(Feature):
    """法向平行于某一坐标轴的平面 —— 台阶、壁厚、槽宽的几何载体。

    只认轴平行面：图纸上的线性尺寸沿加工轴或对称轴量，斜面的尺寸要先定它出现在哪个
    投影里，不属于这一步。`centroid` 不是装饰 —— 判定两个平行面之间到底有没有材料，
    靠的是它们的中点探在哪个三维位置，而 `Plane().Location()` 给的是平面上任取的一点，
    会把中点探到空气里。
    """

    axis: str
    position: float
    centroid: XYZ


def planar_faces(shape: TopoDS_Shape) -> tuple[PlanarFace, ...]:
    """列出所有轴平行平面。坐标是全局的，减包围盒原点留给方案层做（与 Bore 一致）。"""
    faces: list[PlanarFace] = []
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    while explorer.More():
        face = TopoDS.Face(explorer.Current())
        adaptor = BRepAdaptor_Surface(face)
        if adaptor.GetType() == GeomAbs_Plane:
            normal = adaptor.Plane().Axis().Direction()
            vector = (normal.X(), normal.Y(), normal.Z())
            index = max(range(3), key=lambda k: abs(vector[k]))
            if abs(vector[index]) > 0.999:
                props = GProp_GProps()
                BRepGProp.SurfaceProperties_s(face, props)
                centroid = _xyz(props.CentreOfMass())
                faces.append(PlanarFace(
                    feature_id=f"plane-{index}-{round(centroid[index], 4)}-{len(faces)}",
                    axis="xyz"[index], position=centroid[index], centroid=centroid))
        explorer.Next()
    return tuple(faces)


@dataclass(frozen=True)
class Gap(Feature):
    """两个相邻轴平行平面之间的一段 —— 壁厚、台阶、槽宽、肋宽的标注载体。

    坐标都是全局的，与 `PlanarFace` 一致。`gap` 就是图纸上要写的那个数。
    `solid` 只是标记，不是筛选条件：槽宽和壁厚在图纸上都是正当的尺寸。
    """

    axis: str
    low: float
    high: float
    gap: float
    solid: bool
    #: 两面质点的中点。落位时尺寸线要有一个侧向位置，不能只靠沿轴的两个坐标 ——
    #: 同一个 `low/high` 在板的上下两端可以各有一段，投影里它们离得很远。
    mid: XYZ


def find_gaps(shape: TopoDS_Shape, bbox: XYZ) -> tuple[Gap, ...]:
    """列出所有**相邻**轴平行平面对，并标出中间是材料还是空腔。

    只取相邻：中间还夹着另一个面的话，那两个面之间的距离不是一段壁厚或槽宽，
    而是好几段加上中间的特征，标出来读的是别的东西。

    等于外廓的那一对不发 —— 总尺寸已经覆盖它，再发就是同自由度钉两次。
    """
    gaps: list[Gap] = []
    classifier = _classifier(shape)
    faces = planar_faces(shape)

    for index, axis in enumerate("xyz"):
        along = sorted((f for f in faces if f.axis == axis), key=lambda f: f.position)
        positions = sorted({round(f.position, 6) for f in along})
        for low, high in zip(positions, positions[1:]):
            gap = high - low
            if gap <= 1e-6 or abs(gap - bbox[index]) < 1e-6:
                continue
            lower = [f.centroid for f in along if round(f.position, 6) == low]
            upper = [f.centroid for f in along if round(f.position, 6) == high]
            # 同一层可以有多个面（槽壁被圆角分成两截），任取一对质点探到材料就算实心。
            # 探的是两面质点的连线中点，所以 `Plane().Location()` 那种任取点不能用。
            solid = any(_state_at(classifier, gp_Pnt(*[(a[k] + b[k]) / 2.0
                                                       for k in range(3)]))
                        for a in lower for b in upper)
            anchor = [sum(p[k] for p in lower + upper) / (len(lower) + len(upper))
                      for k in range(3)]
            gaps.append(Gap(feature_id=f"gap-{axis}-{low:.3f}",
                            axis=axis, low=low, high=high, gap=gap, solid=solid,
                            mid=tuple(anchor)))
    return tuple(gaps)


def outline_partitions(gaps, bbox: XYZ) -> tuple[tuple[str, tuple[Gap, ...]], ...]:
    """找出**首尾相接正好铺满外廓**的那串相邻间隙。

    这就是尺寸链本身：链上每一环都标实尺、封闭环（外廓总尺寸）也标实尺，
    按 GB/T 必须有一环降级成参考尺寸，否则四个数只能满足三个。
    认不出这条链，`CLOSED_CHAIN` 就永远是死代码。
    """
    runs = []
    for index, axis in enumerate("xyz"):
        ordered = sorted((g for g in gaps if g.axis == axis), key=lambda g: g.low)
        run, cursor = [], 0.0
        for gap in ordered:
            if abs(gap.low - cursor) < 0.02:
                cursor = gap.high
                run.append(gap)
        if len(run) >= 2 and abs(cursor - bbox[index]) < 0.02:
            runs.append((axis, tuple(run)))
    return tuple(runs)


def _state(shape: TopoDS_Shape, point: gp_Pnt) -> bool:
    classifier = BRepClass3d_SolidClassifier(shape)
    classifier.Perform(point, 1e-7)
    return classifier.State() == TopAbs_IN


def _cylindrical_faces(shape: TopoDS_Shape) -> Iterator[tuple]:
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    while explorer.More():
        face = TopoDS.Face(explorer.Current())
        adaptor = BRepAdaptor_Surface(face)
        if adaptor.GetType() == GeomAbs_Cylinder:
            yield face, adaptor, adaptor.Cylinder()
        explorer.Next()


def _classifier(shape: TopoDS_Shape) -> BRepClass3d_SolidClassifier:
    """一个实体建一次分类器。它内部缓存该实体的三角化定位结构，逐点重建会慢一个量级。"""
    return BRepClass3d_SolidClassifier(shape)


def _state_at(classifier, point) -> bool:
    classifier.Perform(point, 1e-7)
    return classifier.State() == TopAbs_IN


def _state(shape: TopoDS_Shape, point: gp_Pnt) -> bool:
    return _state_at(_classifier(shape), point)


def _radial_unit(adaptor, cylinder, u, v):
    """返回 (面上的点, 该处指向外的径向单位向量)；退化时给 None。"""
    axis = cylinder.Axis()
    origin, direction = _xyz(axis.Location()), _xyz(axis.Direction())
    point = _xyz(adaptor.Value(u, v))
    radial = _sub(point, origin)
    planar = _sub(radial, _mul(direction, _dot(radial, direction)))
    length = math.hypot(*planar)
    if length < 1e-12:
        return None
    return point, _mul(planar, 1.0 / length)


#: u 方向 3 档 × v 方向 3 档。面被修剪时单点采样会落到面外（实测 65 个真实零件里
#: 12 个圆柱面这样，两侧给同一个答案，整条特征被悄悄丢掉），撒点取多数才能扛住。
_PROBE_U = 3
_PROBE_V = 3

INTERNAL = "internal"
EXTERNAL = "external"


def probe_face_kind(shape: TopoDS_Shape, adaptor, cylinder) -> str | None:
    """这个圆柱面是孔（材料在外）还是外圆（材料在内）？判不出来返回 None。"""
    u1, u2 = adaptor.FirstUParameter(), adaptor.LastUParameter()
    v1, v2 = adaptor.FirstVParameter(), adaptor.LastVParameter()
    offset = max(cylinder.Radius() * 1e-3, 1e-3)
    classifier = _classifier(shape)
    votes = {INTERNAL: 0, EXTERNAL: 0}

    for i in range(_PROBE_U):
        for j in range(_PROBE_V):
            frame = _radial_unit(adaptor, cylinder,
                                 u1 + (u2 - u1) * (i + 0.5) / _PROBE_U,
                                 v1 + (v2 - v1) * (j + 0.5) / _PROBE_V)
            if frame is None:
                continue
            point, unit = frame
            outward = _state_at(classifier, gp_Pnt(*_add(point, _mul(unit, offset))))
            inward = _state_at(classifier, gp_Pnt(*_sub(point, _mul(unit, offset))))
            if outward and not inward:
                votes[INTERNAL] += 1
            elif inward and not outward:
                votes[EXTERNAL] += 1

    if votes[INTERNAL] > votes[EXTERNAL]:
        return INTERNAL
    if votes[EXTERNAL] > votes[INTERNAL]:
        return EXTERNAL
    return None


def classify_cylindrical_faces(shape: TopoDS_Shape) -> tuple[tuple[float, str | None], ...]:
    """每个圆柱面的 (直径, 内外判定)。给标注层用，也给回归测试当"一个面都不许悄悄丢"的哨兵。"""
    return tuple((2.0 * cylinder.Radius(), probe_face_kind(shape, adaptor, cylinder))
                 for _face, adaptor, cylinder in _cylindrical_faces(shape))


def unclassified_face_count(shape: TopoDS_Shape) -> int:
    """判不出内外的圆柱面数。非零就说明有特征被静默丢掉，交付门据此拦图。"""
    return sum(1 for _diameter, kind in classify_cylindrical_faces(shape) if kind is None)



#: 穿透检查必须打在孔自己的环带上，不能只打在轴线上。只测轴线会被下一级的更小孔
#: 骗过去 —— 沉孔脚下站着引导孔的空腔，轴线上「没有材料」，于是把必须标的 ↧ 深度
#: 当成通孔吃掉了。从孔心到孔壁取几道，任何一道撞上材料就说明这一端没有穿透。
_THROUGH_FAN = (0.5, 0.75, 0.9, 0.97)


def _end_frames(adaptor, cylinder):
    """孔两端的 (轴线上的端点, 该处的径向单位向量)，顺序对应 (FirstV, LastV)。

    u 取面自己的中值：`BRepAdaptor_Surface` 给的参数域已经是**修剪后**的范围
    （实测 part_0063 的 ⌀8 是 u=[-0.52, 3.67] 的残缺弧，同一个 ⌀5 孔被拆成
    u=[0, 4.07] 与 u=[5.36, 6.28] 两张面）—— 所以这里不能按整圆取 u=π。
    拆成两张面这件事本身也说明：深度/贯通是按"面"算的，同一条孔可能被算两次。
    """
    axis = cylinder.Axis()
    origin, direction = _xyz(axis.Location()), _xyz(axis.Direction())
    u = (adaptor.FirstUParameter() + adaptor.LastUParameter()) / 2

    frames = []
    for v in (adaptor.FirstVParameter(), adaptor.LastVParameter()):
        surface = _xyz(adaptor.Value(u, v))
        offset = _sub(surface, origin)
        center = _add(origin, _mul(direction, _dot(offset, direction)))
        radial = _sub(surface, center)
        length = math.hypot(*radial)
        if length < 1e-12:
            return None
        frames.append((center, _mul(radial, 1.0 / length)))
    return frames, direction


def _open_ends(shape: TopoDS_Shape, ends, direction, radius: float, depth: float):
    """两端各自是否开口（材料被穿透到外面）。

    顺序与 `ends` 一致，即 (FirstV 端, LastV 端)。贯通判定和深度落位都要用这个结果：
    前者要"两端都开口"，后者要知道**哪一端是入口**才能把 ↧ 画对方向。
    """
    eps = max(0.05, depth * 1e-3)
    opening = []

    for (center, radial), sign in zip(ends, (-1.0, 1.0)):
        tip = _add(center, _mul(direction, sign * eps))
        opening.append(not any(
            _state(shape, gp_Pnt(*_add(tip, _mul(radial, frac * radius))))
            for frac in _THROUGH_FAN))
    return tuple(opening)



def _canonical(direction: XYZ) -> XYZ:
    """圆柱面的轴向可正可倒：把主导分量翻成正的，两个孔的端点投影才在同一把标尺上。

    不这么做的话，同一个方向上相邻的两段会因为方向向量互为反号而投影到数轴两端，
    明明相接的区间看起来隔了整个零件 —— 沉孔就配不上了。
    """
    index = max(range(3), key=lambda k: abs(direction[k]))
    return direction if direction[index] > 0 else tuple(-c for c in direction)


@dataclass(frozen=True)
class Counterbore:
    """同轴上「大孔坐在小孔上」的那一对 —— 真实图纸写成 `⌀4,5 ⌴⌀6,5 ↧4`。

    大量径是**沉孔直径**，不是普通直径：语料把 `⌴` 的值放在沉孔池，我们若发进
    直径池，结果是沉孔池 0 产出、直径池凭空超发。
    """

    cbore_id: str
    pilot_id: str
    diameter: float
    depth: float
    #: 配对提出的入口端轴向坐标。开口探针可能被圆角/中心柱污染；
    #: 交付前还必须以真实肩面连接核验引导孔，不能把区间相交当作已确认的连接。
    mouth: float | None = None


#: 同轴判据（轴线垂距）与端面相接判据，单位 mm。真实零件上沉孔与引导孔的台阶
#: 是同一个位置，留 0.05 足够吸收 STEP 往返与布尔运算的噪声。
_COAXIAL_TOL = 0.05
_ADJACENCY_TOL = 0.05


def _axis_offset(bore) -> XYZ:
    """去掉轴线上那个任取的分量，剩下的垂距向量才是轴线的身份。"""
    direction = bore.axis_direction
    return _sub(bore.axis_point, _mul(direction, _dot(bore.axis_point, direction)))


def _same_axis(a, b) -> bool:
    """方向平行（符号可反，圆柱面的轴向可正可倒）且垂距重合。"""
    if a.axis_span is None or b.axis_span is None:
        return False
    if abs(abs(_dot(a.axis_direction, b.axis_direction)) - 1.0) > 1e-6:
        return False
    return math.hypot(*_sub(_axis_offset(a), _axis_offset(b))) <= _COAXIAL_TOL


def _touching(a, b) -> bool:
    """两段的轴向区间相接或重叠（沉孔的底正好落在引导孔走到的位置也算）。"""
    return (a.axis_span[0] <= b.axis_span[1] + _ADJACENCY_TOL
            and b.axis_span[0] <= a.axis_span[1] + _ADJACENCY_TOL)


def _counterbore_mouth(cbore, pilot) -> float | None:
    """沉孔的口 = 不贴着引导孔的那一端。两端轴向坐标都在 `_canonical` 的标尺上，
    同轴的两张面可以直接比 —— 这个归一化本来就是为它做的。
    """
    if cbore.axis_span is None or pilot.axis_span is None:
        return None
    lo, hi = cbore.axis_span

    def gap(end: float) -> float:
        return min(abs(end - pilot.axis_span[0]), abs(end - pilot.axis_span[1]))

    return hi if gap(lo) <= gap(hi) else lo


def find_counterbores(bores) -> tuple[Counterbore, ...]:
    """提出沉孔配对，并排除两端经真实肩面转入小孔的中间扩腔。

    剩余配对仍须经过交付层的正向肩面核验；轴向重叠本身不是证据。

    引导孔自己通不通都算 —— 语料里 `⌀4 ↧6 ⌴⌀6,5 ↧4` 这种两面钻进来刚好碰头的写法
    很常见。反过来也成立：**沉孔自己被判成贯通时也必须照配** —— part_0063 里 ⌀8
    脚下的台阶有 R2 圆角，把环带探针啃成空腔，贯通判据在那里本来就是错的；
    一个会被圆角污染的布尔值不该决定一条尺寸发不发。
    """
    found = []
    for candidate in bores:
        if candidate.internal_enlargement:
            continue
        # 不拿 mouth/through 当门槛：part_0063 的 ⌀8 两端都开口（mouth=None），
        # 真实图纸照样写 `⌀5 ⌴⌀8 ↧2`。一个会被圆角与拆面污染的布尔值不该决定
        # 一条尺寸发不发。
        underneath = [other for other in bores
                      if other is not candidate
                      and other.diameter < candidate.diameter - 0.05
                      and _same_axis(candidate, other)
                      and _touching(candidate, other)]
        if not underneath:
            continue
        # 三级以上的阶梯孔：沉孔认它是"紧贴着的下一级"，取最粗的那个
        pilot = max(underneath, key=lambda b: b.diameter)
        mouth = _counterbore_mouth(candidate, pilot)
        if mouth is None:
            continue
        # 轴向区间重叠不等于实际肩面相接。GEO-019的两端封肩事实来自
        # extract_features的共享边拓扑，已在本循环入口排除；不删原始圆柱特征。
        found.append(Counterbore(cbore_id=candidate.feature_id,
                                 pilot_id=pilot.feature_id,
                                 diameter=candidate.diameter,
                                 depth=candidate.depth,
                                 mouth=mouth))
    return tuple(found)



@dataclass(frozen=True)
class BoreGroup:
    """同直径、同深度、同轴向、同贯通性的孔并成一组。

    出图时这是一条带数量的标注（`4×⌀9`）而不是四条直径；但组只合并「相同的量」，
    每个孔的位置仍要逐个给。
    """

    group_id: str
    diameter: float
    depth: float
    axis_direction: tuple[float, float, float]
    members: tuple[str, ...]
    through: bool = False
    #: 代表孔的入口端轴向坐标。None = 没有入口（封闭内腔），这类孔不发深度。
    mouth: float | None = None

    @property
    def count(self) -> int:
        return len(self.members)


def group_bores(bores) -> tuple[BoreGroup, ...]:
    groups = {}
    for bore in bores:
        key = (round(bore.diameter, 6), round(bore.depth, 6),
               tuple(round(c, 6) for c in bore.axis_direction), bore.through, bore.internal_enlargement)
        groups.setdefault(key, []).append(bore)

    return tuple(
        BoreGroup(group_id=f"group-{members[0].feature_id}",
                  diameter=members[0].diameter,
                  depth=members[0].depth,
                  axis_direction=members[0].axis_direction,
                  members=tuple(m.feature_id for m in members),
                  through=members[0].through,
                  mouth=None if members[0].internal_enlargement else members[0].mouth)
        for members in groups.values()
    )


def bolt_circle(points, radial_tol: float = 1e-4, angle_tol: float = 1e-4):
    """判定一组点是否等分分布在同一个圆上，是则返回 (圆心, 半径)。

    真实图纸对这种孔系写一条 `N×⌀d EQUALLY SPACED ON ⌀D PCD`，
    而不是逐个给坐标 —— 4 个孔从 8 条定位尺寸压成 3 条。
    少于 3 个不成「等分」，两孔一律不算。

    这个圆不是「算出来的一条线」：圆心是各孔圆心的平均，半径是各孔圆心的平均距离，
    每个孔圆心都必须真的落在它上面。方案层用它发 PCD、中心线层用它把分布圆画成
    细点划线 —— 两处必须判同一件事，所以判据只写这一份。
    """
    if len(points) < 3:
        return None

    center = (sum(p[0] for p in points) / len(points),
              sum(p[1] for p in points) / len(points))
    radii = [math.hypot(p[0] - center[0], p[1] - center[1]) for p in points]
    if max(radii) - min(radii) > radial_tol or min(radii) <= radial_tol:
        return None

    angles = sorted(math.atan2(p[1] - center[1], p[0] - center[0]) for p in points)
    gaps = [b - a for a, b in zip(angles, angles[1:])]
    gaps.append(angles[0] + 2 * math.pi - angles[-1])
    if max(gaps) - min(gaps) > angle_tol:
        return None

    return center, sum(radii) / len(radii)


@dataclass(frozen=True)
class ShaftGroup:
    """同名义直径的外圆并成一条标注。

    ⌀30 被中间的大台阶隔成两段，图纸上 ⌀30 只写一次（同一名义直径适用所有该尺寸
    的表面），所以这里按直径去重、且 **不写 `2×`** —— 数量前缀是给重复孔用的。
    """

    group_id: str
    diameter: float
    quantity: int = 1


def group_shafts(shafts) -> tuple[ShaftGroup, ...]:
    groups = {}
    for shaft in shafts:
        groups.setdefault(round(shaft.diameter, 6), shaft)

    return tuple(
        ShaftGroup(group_id=f"shaft-group-{member.feature_id}",
                   diameter=member.diameter)
        for member in groups.values()
    )


@dataclass(frozen=True)
class CounterboreGroup:
    """同直径同深度的沉孔并成一条 `2×⌀4,5 ⌴⌀6 ↧2`。"""

    group_id: str
    diameter: float
    depth: float
    members: tuple[str, ...]

    @property
    def count(self) -> int:
        return len(self.members)


def group_counterbores(pairs) -> tuple[CounterboreGroup, ...]:
    groups = {}
    for pair in pairs:
        groups.setdefault((round(pair.diameter, 6), round(pair.depth, 6)), []).append(pair)

    return tuple(
        CounterboreGroup(group_id=f"cbore-{members[0].cbore_id}",
                         diameter=members[0].diameter,
                         depth=members[0].depth,
                         members=tuple(m.cbore_id for m in members))
        for members in groups.values()
    )


def _geometry_key(kind: str, diameter: float, offset: XYZ, span, direction) -> tuple | None:
    """同一个孔/同一段外圆被布尔运算切成多张面时的归并键。

    轴线位置取**垂距**（轴向那个分量是任取的，不能进键），再加上轴向区间：
    加上规范轴方向，防止同垂距同区间的异向孔被静默合并。撞上了还按特征各发一条，就会把
    一个孔写成 `4×⌀58` —— 错发的数量直接叫车间多钻三孔，比漏标更贵。
    """
    if span is None or offset is None:
        return None
    return (kind, round(diameter, 4), tuple(round(c, 4) for c in offset),
            tuple(round(t, 4) for t in span), tuple(round(c,6) for c in _canonical(direction)))


def extract_features(shape: TopoDS_Shape, bbox: XYZ) -> tuple[Feature, ...]:
    """从实体提取可标注特征。这是标注层唯一的几何事实源 —— 任何尺寸值都必须能追溯到这里。

    `bbox` 是必需的：判「两个相邻平面之间的间隙算不算又一段壁厚」要比它对外的廓总尺寸，
    而那个数只有装载层有（`AddOptimal` 精确包围盒，别在这里另算一份松的）。
    """
    features: list[Feature] = []
    seen: dict[tuple, int] = {}
    taken: dict[tuple,list] = {}
    internal_faces = []

    for face, adaptor, cylinder in _cylindrical_faces(shape):
        kind = probe_face_kind(shape, adaptor, cylinder)
        if kind not in (INTERNAL, EXTERNAL):
            continue
        internal = kind == INTERNAL
        if internal:
            internal_faces.append((face, adaptor, cylinder))
        axis = cylinder.Axis()
        origin = axis.Location()
        direction = axis.Direction()
        span = abs(adaptor.LastVParameter() - adaptor.FirstVParameter())
        axis_span = None
        offset = None
        through = False
        mouth = None
        frames = _end_frames(adaptor, cylinder)
        if frames is not None:
            ends, unit = frames
            canon = _canonical(unit)
            projections = [_dot(center, canon) for center, _r in ends]
            axis_span = tuple(sorted(projections))
            location = _xyz(origin)
            offset = _sub(location, _mul(canon, _dot(location, canon)))
            if internal:
                opening = _open_ends(shape, ends, unit, cylinder.Radius(), span)
                through = all(opening)
                # 只有一端开口才是盲孔，那端就是入口；两端都开口没有单一入口
                mouth = projections[opening.index(True)] if sum(opening) == 1 else None

        geometry = _geometry_key(kind, 2.0 * cylinder.Radius(), offset, axis_span, _xyz(direction))
        if geometry is not None:
            original = (2.0*cylinder.Radius(),offset,axis_span,_canonical(_xyz(direction)))
            bucket = taken.setdefault(geometry,[])
            # 量化只缩小查找范围；只有原精度几何在OCCT公差内相同才归并。
            if any(abs(original[0]-old[0])<=Precision.Confusion_s()
                   and math.dist(original[1],old[1])<=Precision.Confusion_s()
                   and max(abs(a-b) for a,b in zip(original[2],old[2]))<=Precision.Confusion_s()
                   and math.dist(original[3],old[3])<=Precision.Angular_s() for old in bucket):
                continue
            bucket.append(original)

        key = (round(origin.X(), 4), round(origin.Y(), 4), round(cylinder.Radius(), 4))
        index = seen.get(key, 0)
        seen[key] = index + 1
        common = dict(
            feature_id=f"{'bore' if internal else 'shaft'}-{key[0]}-{key[1]}"
                       f"-d{key[2]}-{index}",
            diameter=2.0 * cylinder.Radius(),
            axis_point=(origin.X(), origin.Y(), origin.Z()),
            axis_direction=(direction.X(), direction.Y(), direction.Z()),
        )
        if internal:
            features.append(Bore(**common, depth=span, axis_span=axis_span,
                                 through=through, mouth=mouth))
        else:
            features.append(Shaft(**common, length=span))

    from drawform.geometry.bore_topology import end_neighbors
    topology, tolerances = end_neighbors(shape, [f for f in features if isinstance(f, Bore)], internal_faces)
    features = [replace(f, shoulder_neighbors=topology[f.feature_id], topology_checked=True,
                        end_tolerances=tolerances[f.feature_id]) if isinstance(f, Bore) else f
                for f in features]
    features.extend(find_gaps(shape, bbox))
    return tuple(features)


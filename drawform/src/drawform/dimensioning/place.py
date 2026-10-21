"""把尺寸方案落到视图几何上，产出可绘制的尺寸定义。

关键约束：尺寸界线必须钉在**投影出来的真实轮廓**上，不能拿包围盒的数字凑 ——
包围盒和真实轮廓在圆角、斜面上不重合，用数字凑会画出量不到的尺寸。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from drawform.dimensioning.constraints import Sized
from drawform.domain.curves import FRONT, TOP, LEFT, Arc2D, Circle2D, Line2D, to_view, view_basis

#: 尺寸线离轮廓的距离（mm）。真实制图里这个距离要避开中心线和相邻视图。
OFFSET = 8.0
#: 找得到圆就行，但优先落在最能表达孔径的视图上。
VIEW_ORDER = ("top", "front", "left")
#: 孔径尺寸的引出角。同心孔若都用同一个角，文字会沿同一条射线叠在一起。
DIAMETER_ANGLES = (45.0, 135.0, 225.0, 315.0)
DIAMETER_PREFIX = "⌀"
#: 沉孔直径的符号前缀（GB/T 16675.1 / ISO 128 的沉孔记号）。
COUNTERBORE_PREFIX = "⌴⌀"
#: 深度符号。GB/T 16675.1 里 `↧` 之后写的是"从入口面到孔底"的长度。
DEPTH_PREFIX = "↧"


@dataclass(frozen=True)
class PlacedDimension:
    kind: str
    view: str
    p1: tuple[float, float]
    p2: tuple[float, float]
    base: tuple[float, float] | None
    text: str
    value: float
    #: 定位尺寸标的是世界哪个坐标（"x"/"y"/"z"）。落位时再决定它落在视图的哪个方向上
    #: —— 两者不能混成一个字段，否则"量得出的坐标"和"画出来的方向"会互相冒充。
    axis: str = ""
    #: 这条尺寸来自方案里的哪一条 (feature, dof)。交付门靠它数"发了没画"——
    #: 没有这个回指，方案层与图纸层之间就是两条各自自说自话的链。
    source: tuple[str, str] | None = None
    #: 同一几何尺寸共用标注时，保留全部方案来源；不累加孔数、不删除方案约束。
    shared_sources: tuple[tuple[str, str], ...] = ()
    #: 与 shared_sources 同序的原始测量端点，共线孔位共用尺寸时也不能丢掉几何依据。
    shared_witnesses: tuple[tuple[tuple[float, float], tuple[float, float]], ...] = ()
    #: 原始双精度值逐来源保存；几何指纹去掉浮点尾差不等于改写这些数值。
    shared_values: tuple[float, ...] = ()
    #: 非对齐边界点的线性尺寸按此投影方向测量；None 表示沿两端点连线。
    measurement_axis: tuple[float, float] | None = None
    #: 原模型毫米坐标：轮廓的两个真实支撑点，或定位基准的一个真实支撑点。
    reference_points_3d: tuple[tuple[float, float, float], ...] = ()

    def direction(self):
        vector = self.measurement_axis or (self.p2[0]-self.p1[0], self.p2[1]-self.p1[1])
        length = math.hypot(*vector)
        return (vector[0]/length, vector[1]/length) if length else (1.0, 0.0)


def _box(projection):
    points = [p for c in tuple(projection.visible) + tuple(projection.hidden)
              for p in c.sample()]
    return (min(p[0] for p in points), min(p[1] for p in points),
            max(p[0] for p in points), max(p[1] for p in points))


def _material_support(projection, axis):
    return next((s for s in projection.material_supports if s.axis == axis), None)


def _outline_witnesses(projection, axis):
    curves = (*projection.visible, *projection.hidden)
    points = [p for c in curves for p in c.sample()]
    low, high = min(p[axis] for p in points), max(p[axis] for p in points)
    groups = [[p for p in points if p[axis] == limit] for limit in (low, high)]
    supports = []
    for limit, group in zip((low, high), groups):
        spans = [(p[1-axis], p[1-axis], p, p) for p in group]
        for curve in curves:
            if isinstance(curve, Line2D) and all(abs(p[axis]-limit) <= 1e-9 for p in (curve.start, curve.end)):
                a, b = sorted((curve.start, curve.end), key=lambda p: p[1-axis])
                spans.append((a[1-axis], b[1-axis], a, b))
        supports.append(spans)
    pairs = []
    for a in supports[0]:
        for b in supports[1]:
            coordinate = max(a[0], b[0])
            if coordinate > min(a[1], b[1]):
                continue
            pair = []
            for span in (a, b):
                t = (coordinate-span[0])/(span[1]-span[0]) if span[1] != span[0] else 0
                pair.append(tuple(span[2][i]+t*(span[3][i]-span[2][i]) for i in (0, 1)))
            pairs.append((coordinate, tuple(pair)))
    if pairs:
        return min(pairs)[1]
    return min(((a, b) for a in groups[0] for b in groups[1]),
               key=lambda pair: (abs(pair[0][1-axis]-pair[1][1-axis]), pair[0], pair[1]))


def _place_outline(by_key, projections, dims):
    choices = (('length', (('front', 0), ('top', 0))),
               ('height', (('front', 1), ('left', 1))),
               ('width', (('top', 1), ('left', 0))))
    for dof, views in choices:
        item = by_key.get(('outline', dof))
        if item is None:
            continue
        for name, axis in views:
            projection = projections.get(name)
            if projection is None:
                continue
            box = _box(projection)
            if abs(box[axis+2]-box[axis]-item.value) > 1e-6:
                continue
            p1, p2 = _outline_witnesses(projection, axis)
            if abs(abs(p2[axis]-p1[axis])-item.value) > 1e-6:
                continue
            base = ((box[0], box[1]-OFFSET) if axis == 0 else
                    (box[0]-OFFSET, box[3] if dof == 'height' else box[1]))
            dims.append(PlacedDimension('linear', name, p1, p2, base, _text(item.value), item.value,
                                        source=('outline', dof),
                                        measurement_axis=(1.0, 0.0) if axis == 0 else (0.0, 1.0)))
            break
        else:
            # 所有绘制曲线都不够准确时，才使用原实体边界上已经验证的真实支撑点。
            for name, axis in views:
                projection = projections.get(name)
                support = _material_support(projection, axis) if projection is not None else None
                if support is None or abs(support.upper_2d[axis]-support.lower_2d[axis]-item.value) > 1e-6:
                    continue
                box = _box(projection)
                base = ((box[0], box[1]-OFFSET) if axis == 0 else
                        (box[0]-OFFSET, box[3] if dof == 'height' else box[1]))
                dims.append(PlacedDimension('linear', name, support.lower_2d, support.upper_2d, base,
                                            _text(item.value), item.value, source=('outline', dof),
                                            measurement_axis=(1.0,0.0) if axis == 0 else (0.0,1.0),
                                            reference_points_3d=(support.lower_3d, support.upper_3d)))
                break  # 没有已验证依据时，原约束仍留给交付门拒绝。


def _text(value: float, quantity: int = 1, prefix: str = "") -> str:
    """数量前缀必须留在图上：4 个孔只标「⌀9」在生产里就是漏标。"""
    return (f"{quantity}×" if quantity > 1 else "") + prefix + f"{value:g}"


def _find_circle(radius: float, projections, members, angle):
    """找该半径的投影圆心。整圆和**圆弧**都算 —— 被边界切掉的孔只剩一段弧，
    但圆心仍然确定，真实图纸照样从弧上标 ⌀。只认整圆会让这类尺寸静默消失
    （实测语料 35 条没落位的直径里 33 条是这个原因）。
    """
    for name in VIEW_ORDER:
        projection = projections.get(name)
        if projection is None:
            continue
        basis = view_basis(projection.direction)
        centers = [to_view(member.axis_point, *basis) for member in members
                   if math.hypot(*to_view(member.axis_direction, *basis)) <= 1e-6]
        for curve in tuple(projection.visible) + tuple(projection.hidden):
            if (isinstance(curve, (Circle2D, Arc2D)) and abs(curve.radius - radius) < 1e-6
                    and any(math.dist(curve.center, center) <= 1e-6 for center in centers)):
                theta = angle
                if isinstance(curve, Arc2D):
                    low, high = sorted((curve.start_angle, curve.end_angle))
                    theta += 2*math.pi*math.ceil((low-theta)/(2*math.pi))
                    if theta > high:
                        theta = (low+high)/2
                point = (curve.center[0]+curve.radius*math.cos(theta),
                         curve.center[1]+curve.radius*math.sin(theta))
                return name, curve.center, point
    return None


def _diameter_members(features):
    """A diameter belongs to its feature group, not any equal-radius circle."""
    from drawform.geometry.features import (Bore, Shaft, group_bores, group_shafts,
                                            find_counterbores, group_counterbores)
    bores = [f for f in features if isinstance(f, Bore)]
    shafts = [f for f in features if isinstance(f, Shaft)]
    by_id = {f.feature_id: f for f in (*bores, *shafts)}
    result = {key: (value,) for key, value in by_id.items()}
    counterbores = find_counterbores(bores)
    cbore_ids = {pair.cbore_id for pair in counterbores}
    for group in (*group_bores([b for b in bores if b.feature_id not in cbore_ids]),
                  *group_counterbores(counterbores)):
        result[group.group_id] = tuple(by_id[member] for member in group.members)
    for group in group_shafts(shafts):
        result[group.group_id] = tuple(f for f in shafts if round(f.diameter, 6) == round(group.diameter, 6))
    return result


def _on_projected_circle(point, center, radius, curve):
    """Test the analytic finite arc, including reversed and unwrapped angles."""
    if not isinstance(curve, (Circle2D, Arc2D)):
        return False
    if not all(math.isfinite(value) for value in (*curve.center, curve.radius)) or curve.radius <= 0:
        return False
    if math.dist(curve.center, center) > 1e-6 or abs(curve.radius-radius) > 1e-6:
        return False
    if abs(math.dist(point, center)-radius) > 1e-6:
        return False
    if isinstance(curve, Circle2D):
        return True
    low, high = sorted((curve.start_angle, curve.end_angle))
    if not all(math.isfinite(value) for value in (low, high)):
        return False
    angle = math.atan2(point[1]-center[1], point[0]-center[0])
    epsilon = 1e-6/max(radius, 1e-6)
    angle += 2*math.pi*math.ceil((low-angle-epsilon)/(2*math.pi))
    return angle <= high+epsilon


def _on_projected_wall(point, member, basis, curve):
    """A side-wall witness must lie on its real finite parallel line segment."""
    if not isinstance(curve, Line2D):
        return False
    direction = to_view(member.canonical_axis(), *basis)
    norm = math.hypot(*direction)
    if norm <= 1e-6:
        return False
    normal = (-direction[1]/norm, direction[0]/norm)
    axis = to_view(member.axis_offset(), *basis)
    offsets = [sum((p[i]-axis[i])*normal[i] for i in range(2))
               for p in (curve.start, curve.end)]
    if abs(offsets[0]-offsets[1]) > 1e-7 or abs(abs(offsets[0])-member.diameter/2) > 5e-7:
        return False
    dx, dy = curve.end[0]-curve.start[0], curve.end[1]-curve.start[1]
    length = math.hypot(dx, dy)
    if length <= 1e-9:
        return False
    fraction = ((point[0]-curve.start[0])*dx+(point[1]-curve.start[1])*dy)/(length*length)
    projected = (curve.start[0]+fraction*dx, curve.start[1]+fraction*dy)
    return (-1e-6/length <= fraction <= 1+1e-6/length
            and math.dist(point, projected) <= 1e-6)


def diameter_source_errors(scheme, dimensions, projections=None):
    """Verify the source cylinder and, when supplied, its original projected witness.

    Existing callers without projections retain the source-only contract. The
    delivery pipeline supplies its unscaled original projections before export.
    """
    from drawform.geometry.features import Bore
    members = _diameter_members(scheme.features)
    views = {view.name: view for view in (FRONT, TOP, LEFT)}
    errors = []
    for dim in dimensions:
        if dim.kind != 'diameter':
            continue
        view = views.get(dim.view)
        if view is None:
            errors.append((dim.source, '孔径视图没有可核验的投影方向'))
            continue
        basis = view_basis(view)
        projection = projections.get(dim.view) if projections is not None else None
        if projections is not None and (projection is None or projection.direction != view):
            errors.append((dim.source, '孔径视图缺少对应的原始投影曲线'))
            continue
        for source, (p1, p2) in [(dim.source, (dim.p1, dim.p2)), *zip(dim.shared_sources, dim.shared_witnesses)]:
            matched = False
            for member in members.get(source[0] if source else None, ()):
                if abs(2*math.dist(p1, p2)-member.diameter) > 1e-6:
                    continue
                direction = to_view(member.axis_direction, *basis)
                if math.hypot(*direction) <= 1e-6:
                    matched = math.dist(p1, to_view(member.axis_point, *basis)) <= 1e-6
                    if matched and projection is not None:
                        matched = any(_on_projected_circle(p2, p1, member.diameter/2, curve)
                                      for curve in (*projection.visible, *projection.hidden))
                elif isinstance(member, Bore) and member.axis_span is not None:
                    direction = to_view(member.canonical_axis(), *basis)
                    norm = math.hypot(*direction)
                    delta = tuple(a-b for a,b in zip(p1,to_view(member.axis_offset(),*basis)))
                    t = sum(a*b for a,b in zip(delta,direction))/(norm*norm)
                    matched = (math.hypot(*(a-t*b for a,b in zip(delta,direction))) <= 1e-6
                               and member.axis_span[0]-1e-6 <= t <= member.axis_span[1]+1e-6
                               and abs(sum((b-a)*v for a,b,v in zip(p1,p2,direction)))/norm <= 1e-6)
                    if matched and projection is not None:
                        matched = any(_on_projected_wall(p2, member, basis, curve)
                                      for curve in (*projection.visible, *projection.hidden))
                if matched:
                    break
            if not matched:
                errors.append((source, '直径引注没有对应本组圆柱轴线、有限孔段或实际投影曲线，不能借用其他孔或弧外点'))
    return errors


def _find_bore_wall(bore, projections):
    """斜孔的直径锚在真实投影孔壁上，圆心来自同一孔的三维轴线。

    轴线不正对三视图时，孔口是椭圆或交线，不能放宽圆半径去认别的孔。
    沿孔轴的投影孔壁与轴线相距真实半径；只取轴向区间内的实际线段。
    """
    if bore is None or bore.axis_span is None:
        return None
    radius = bore.diameter / 2.0
    candidates = []
    for order, name in enumerate(VIEW_ORDER):
        projection = projections.get(name)
        if projection is None:
            continue
        basis = view_basis(projection.direction)
        direction = to_view(bore.canonical_axis(), *basis)
        norm = math.hypot(*direction)
        if norm < 1e-6:
            continue
        ux, uy = direction[0]/norm, direction[1]/norm
        nx, ny = -uy, ux
        axis = to_view(bore.axis_offset(), *basis)
        low, high = (t*norm for t in bore.axis_span)
        for curve in (*projection.visible, *projection.hidden):
            if not isinstance(curve, Line2D):
                continue
            a, b = curve.start, curve.end
            offsets = [(p[0]-axis[0])*nx+(p[1]-axis[1])*ny for p in (a, b)]
            if abs(offsets[0]-offsets[1]) > 1e-7 or abs(abs(offsets[0])-radius) > 5e-7:
                continue
            ta, tb = ((p[0]-axis[0])*ux+(p[1]-axis[1])*uy for p in (a, b))
            start, end = max(min(ta, tb), low), min(max(ta, tb), high)
            if end-start <= 1e-6 or abs(tb-ta) <= 1e-9:
                continue
            along = (start+end)/2.0
            fraction = (along-ta)/(tb-ta)
            point = (a[0]+fraction*(b[0]-a[0]), a[1]+fraction*(b[1]-a[1]))
            center = (axis[0]+along*ux, axis[1]+along*uy)
            error = abs(2*math.dist(center, point)-bore.diameter)
            if error <= 1e-6:
                candidates.append((error, -norm, order, name, center, point))
    if not candidates:
        return None
    _error, _norm, _order, name, center, point = min(candidates)
    return name, center, point


def _group_members(features):
    """方案里的组 id → 代表孔。落位要用孔的轴线与入口端，光有尺寸数值定不出端点。"""
    from dataclasses import replace

    from drawform.geometry.features import (Bore, find_counterbores, group_bores,
                                            group_counterbores)

    bores = [f for f in features if isinstance(f, Bore)]
    by_id = {bore.feature_id: bore for bore in bores}
    reps = {}
    pairs = {pair.cbore_id: pair for pair in find_counterbores(bores)}
    for group in group_bores([b for b in bores if b.feature_id not in pairs]):
        reps[group.group_id] = replace(by_id[group.members[0]], mouth=group.mouth)
    for group in group_counterbores(pairs.values()):
        rep = by_id[group.members[0]]
        # 沉孔的入口端用配对关系算出来的那个，不用探针那个：底面被圆角或
        # 中心柱污染时探针会给反，而 ↧ 必须知道从哪端量。
        reps[group.group_id] = replace(rep, mouth=pairs[group.members[0]].mouth)
    return reps


def _axis_view(bore, projections):
    """孔的轴平行于视图面的那一个视图 —— 只有在那里"孔有多深"才看得见。

    在俯视图上孔是一个圆，深度在那个方向上根本不成线，画不出 `↧`。
    """
    for name in VIEW_ORDER:
        projection = projections.get(name)
        if projection is None:
            continue
        if abs(sum(a * b for a, b in zip(projection.direction.direction,
                                         bore.axis_direction))) < 1e-6:
            return name, projection
    return None, None


def _place_depths(scheme, projections, dims):
    reps = _group_members(scheme.features)

    for item in scheme.plan.constraints:
        if getattr(item, "dof", None) != "depth":
            continue
        bore = reps.get(item.feature)
        if bore is None or bore.mouth is None or bore.floor() is None:
            continue
        name, projection = _axis_view(bore, projections)
        if projection is None:
            continue
        x_basis, y_basis = view_basis(projection.direction)
        mouth = to_view(bore.point_at(bore.mouth), x_basis, y_basis)
        floor = to_view(bore.point_at(bore.floor()), x_basis, y_basis)
        dims.append(PlacedDimension(
            "depth", name, mouth, floor,
            (mouth[0] + OFFSET, (mouth[1] + floor[1]) / 2.0),
            _text(item.value, 1, DEPTH_PREFIX), item.value,
            source=(item.feature, "depth")))


#: 世界三个坐标轴。定位尺寸要挑一个"该轴正向落在视图平面内"的视图才量得出来。
WORLD_AXES = {"x": (1.0, 0.0, 0.0), "y": (0.0, 1.0, 0.0), "z": (0.0, 0.0, 1.0)}


def _view_axes(projection):
    """(视图水平方向, 视图竖直方向) 上的二维坐标。"""
    x_basis, y_basis = view_basis(projection.direction)
    return x_basis, y_basis


def _position_view(axis: str, projections, origin=()):
    """找一个把该世界轴**正向**落在视图平面内的视图。

    只认正向（点积 >0.99）不认反向：反了的话"从基准面往正向量"会变成"从另一侧量"，
    标出来的数值和线段的朝向就对不上了。
    """
    unit = WORLD_AXES[axis]
    fallback = []
    for name in VIEW_ORDER:
        projection = projections.get(name)
        if projection is None:
            continue
        for view_axis, basis in (("x", _view_axes(projection)[0]),
                                 ("y", _view_axes(projection)[1])):
            if sum(a * b for a, b in zip(unit, basis)) > 0.99:
                if origin:
                    along = 0 if view_axis == "x" else 1
                    if abs(_box(projection)[along]-origin["xyz".index(axis)]) > 1e-6:
                        support = _material_support(projection, along)
                        if support is not None and abs(support.lower_2d[along]-origin["xyz".index(axis)]) <= 1e-6:
                            fallback.append((name, projection, view_axis))
                        continue
                return name, projection, view_axis
    return fallback[0] if fallback else (None, None, None)


def _group_map(scheme):
    """组 id → 成员孔 id。螺栓圆的圆心与分布圆都由成员孔的实圆心定义。"""
    from drawform.geometry.features import Bore, group_bores

    bores = [f for f in scheme.features if isinstance(f, Bore)]
    return {g.group_id: g.members for g in group_bores(bores)}, {b.feature_id: b for b in bores}


def _normal_view(bore, projections):
    """孔的轴**垂直于**视图面的那个视图 —— 只有在那里孔才是一个圆。"""
    for name in VIEW_ORDER:
        projection = projections.get(name)
        if projection is None:
            continue
        if abs(sum(a * b for a, b in zip(projection.direction.direction,
                                         bore.axis_direction))) > 0.99:
            return name, projection
    return None, None


def _place_pcd(scheme, projections, dims):
    """分布圆 ⌀PCD 标在分布圆上，而不是靠「正好有一对直径相对的孔」。

    分布圆是假想圆，投影里不存在那个半径的圆，`_find_circle` 永远找不到它。
    偶数孔有一对直径相对的真实圆心，两端都钉在孔上，那条线量的就是直径；
    奇数孔取不到这样一对（5 个孔最远的一对只有 ⌀D·cos36°），原先直接跳过，
    于是方案发了 PCD、图上没画 —— 交付门判 `unplaced_dimensions`，整张图拒交。
    真实图纸对奇数孔照样标 ⌀PCD，所以改为：圆心和半径都由各孔圆心独立量出，
    两端落在**穿过一个真实孔的径向**与分布圆的两个交点上。仍然不拿数字凑一条
    没有依据的线 —— 这条线整体就是分布圆本身，中心线层把同一个圆画成点划线。
    """
    from drawform.geometry.features import bolt_circle

    groups, bores = _group_map(scheme)

    for item in scheme.plan.constraints:
        if getattr(item, "dof", None) != "pcd":
            continue
        members = groups.get(item.feature)
        if not members:
            continue
        bore = bores[members[0]]
        name, projection = _normal_view(bore, projections)
        if projection is None:
            continue
        x_basis, y_basis = _view_axes(projection)
        centers = [to_view(bores[m].axis_offset(), x_basis, y_basis) for m in members]
        circle = bolt_circle(centers)
        if circle is None:
            continue
        center, radius = circle
        # The independently measured circle must support the plan. In particular,
        # never manufacture endpoints from the very value the gate must verify.
        if not math.isfinite(item.value) or abs(2.0 * radius - item.value) > 1e-6:
            continue
        pair = max(((a, b) for i, a in enumerate(centers) for b in centers[i + 1:]),
                   key=lambda ab: math.hypot(ab[0][0] - ab[1][0], ab[0][1] - ab[1][1]),
                   default=None)
        if pair is None:
            continue
        span = math.hypot(pair[0][0] - pair[1][0], pair[0][1] - pair[1][1])
        if abs(span - item.value) > 1e-6:
            dx, dy = centers[0][0] - center[0], centers[0][1] - center[1]
            length = math.hypot(dx, dy)
            if length < 1e-9:
                continue
            dx, dy = dx / length, dy / length
            half = radius
            pair = ((center[0] - dx * half, center[1] - dy * half),
                    (center[0] + dx * half, center[1] + dy * half))
        dims.append(PlacedDimension(
            "pcd", name, pair[0], pair[1], None,
            f"{DIAMETER_PREFIX}{item.value:g} PCD", item.value,
            source=(item.feature, "pcd")))


#: 偏移带。外形总尺寸占第一带，孔位尺寸从第二带起逐条外推 —— 挤在同一条带上
#: 文字会互相压字，而这张图在车间里就没法读了。
FIRST_LANE, POSITION_LANE = 1, 2


def position_anchors(scheme):
    """定位的绝对几何坐标；换基准不能从已经取过绝对值的旧尺寸反推位置。"""
    groups, bores = _group_map(scheme)
    anchors = {}
    references = {fact.feature_id:fact.point for fact in scheme.reference_facts}
    for key, bore in bores.items():
        if key in references:
            anchors[key] = references[key]
            continue
        if sum(abs(c) > 1e-6 for c in bore.axis_direction) > 1:
            # 方案用 axis_point 发定位值；斜轴的 axis_offset 是另一个截面，
            # 不能拿它换掉原测量位置。任取点若不在真实孔段内则留给缺失约束门。
            axial = sum(a*b for a,b in zip(bore.axis_point, bore.canonical_axis()))
            if (bore.axis_span is None or axial < bore.axis_span[0]-1e-6
                    or axial > bore.axis_span[1]+1e-6):
                continue
            anchors[key] = bore.axis_point
        else:
            anchors[key] = bore.axis_offset()
    for group, members in groups.items():
        if any(member not in anchors for member in members):
            continue
        points = [anchors[member] for member in members]
        anchors[f"{group}:center"] = tuple(sum(p[i] for p in points) / len(points) for i in range(3))
    return anchors


def _place_positions(scheme, projections, dims):
    """孔（和螺栓圆圆心）的定位尺寸：从基准面量到轴线，画在该轴落得进视图平面的视图上。"""
    anchors = position_anchors(scheme)
    lane = {}

    for item in scheme.plan.constraints:
        axis = getattr(item, "dof", None)
        if axis not in WORLD_AXES:
            continue
        anchor = anchors.get(item.feature)
        if anchor is None:
            continue
        letters = scheme.plan.datum.planes if scheme.plan.datum else ()
        index = "xyz".index(axis)
        chosen = dict(scheme.datum_choices).get(letters[index]) if index < len(letters) else None
        face = next((f for f in scheme.datum_candidates if f.id == chosen), None)
        name, projection, view_axis = _position_view(
            axis, projections, () if face is not None else scheme.origin)
        if projection is None:
            continue

        x_basis, y_basis = _view_axes(projection)
        u, v = to_view(anchor, x_basis, y_basis)
        along, lateral = (u, v) if view_axis == "x" else (v, u)
        box = _box(projection)
        datum = box[0] if view_axis == "x" else box[1]
        references = ()
        if face is not None:
            datum = face.position
        elif scheme.origin and abs(datum-scheme.origin[index]) > 1e-6:
            support = _material_support(projection, 0 if view_axis == "x" else 1)
            if support is None:
                continue
            datum = support.lower_2d[0 if view_axis == "x" else 1]
            references = (support.lower_3d,)
        band = lane[(name, view_axis)] = lane.get((name, view_axis), POSITION_LANE) + 1
        if view_axis == "x":
            p1, p2 = (datum, lateral), (along, lateral)
            base = ((datum + along) / 2.0, box[1] - OFFSET * (band - 1))
        else:
            p1, p2 = (lateral, datum), (lateral, along)
            base = (box[0] - OFFSET * (band - 1), (datum + along) / 2.0)
        value = item.value if face is not None else abs(along - datum)
        dims.append(PlacedDimension("position", name, p1, p2, base,
                                    _text(value), value,
                                    axis=axis, source=(item.feature, item.dof),
                                    reference_points_3d=references))


def _place_gaps(scheme, projections, dims):
    """壁厚/台阶：两个相邻平面之间的间隙，两端钉在那两个面的投影位置上。

    与定位尺寸同构，差别是量的是两个内部坐标之差，而不是「基准面 → 一个坐标」。
    侧向位置取两面质点的中点 —— 同一段 `low/high` 在板的上下两端可以各有一段，
    只有沿轴的坐标定不出该画在哪一侧。
    """
    from drawform.geometry.features import Gap

    gaps = {g.feature_id: g for g in scheme.features if isinstance(g, Gap)}
    lane = {}

    for item in scheme.plan.constraints:
        if getattr(item, "dof", None) != "gap":
            continue
        gap = gaps.get(item.feature)
        if gap is None:
            continue
        name, projection, view_axis = _position_view(gap.axis, projections)
        if projection is None:
            continue

        x_basis, y_basis = _view_axes(projection)
        unit = WORLD_AXES[gap.axis]
        middle = gap.mid
        supports={row[0]:(row[1],row[2]) for row in scheme.shoulder_supports}
        references=supports.get(gap.feature_id,())
        ends = []
        for axial in (gap.low, gap.high):
            shift = axial - sum(middle[k] * unit[k] for k in range(3))
            point = tuple(middle[k] + unit[k] * shift for k in range(3))
            ends.append(to_view(point, x_basis, y_basis))
        if references:ends=[to_view(point,x_basis,y_basis) for point in references]
        (ul, vl), (uh, vh) = ends
        along_low, along_high = (ul, uh) if view_axis == "x" else (vl, vh)
        lateral = (vl + vh) / 2.0 if view_axis == "x" else (ul + uh) / 2.0

        box = _box(projection)
        band = lane[(name, view_axis)] = lane.get((name, view_axis), 0) + 1
        if view_axis == "x":
            p1, p2 = (along_low, lateral), (along_high, lateral)
            base = ((along_low + along_high) / 2.0, box[1] - OFFSET * band)
        else:
            p1, p2 = (lateral, along_low), (lateral, along_high)
            base = (box[0] - OFFSET * band, (along_low + along_high) / 2.0)
        dims.append(PlacedDimension("linear", name, p1, p2, base,
                                    _text(along_high - along_low),
                                    along_high - along_low,
                                    axis=gap.axis, source=(item.feature, item.dof),
                                    reference_points_3d=references))


def place_datum_marks(scheme, projections, dims=()) -> tuple:
    """给**真被引用过**的基准画代号。

    基准就是定位尺寸量起的那个面（包围盒最小角），字母按 A=x、B=y、C=z 的约定配给
    `Plan.datum`。给没被任何尺寸引用的基准也补一个代号，等于教图纸说谎 ——
    纸上标了 A/B/C 却没有一条尺寸从它量起。

    锚点取该方向上**真实投影轮廓**最外侧那批点的中间，不取包围盒角点：
    左下角被切掉一块的零件，包围盒角点是空气。

    `dims` 用来算引出线要往外让多远：代号和尺寸带抢的是同一圈空间，不让就糊上去。
    """
    from drawform.output.drawing import (DATUM_BOX, DATUM_LEADER, DatumMark,
                                        TEXT_HEIGHT)

    planes = scheme.plan.datum.planes if scheme.plan.datum else ()
    referenced = {c.dof for c in scheme.plan.constraints
                  if isinstance(c, Sized) and c.dof in WORLD_AXES}
    marks = []
    for index, axis in enumerate(WORLD_AXES):
        if axis not in referenced or index >= len(planes):
            continue
        chosen = dict(scheme.datum_choices).get(planes[index])
        face = next((f for f in scheme.datum_candidates if f.id == chosen), None)
        name, projection, view_axis = _position_view(
            axis, projections, () if face is not None else scheme.origin)
        if projection is None:
            continue
        points = [p for curve in projection.visible for p in curve.sample()]
        if not points:
            continue
        along = 0 if view_axis == "x" else 1
        extreme = min(p[along] for p in points)
        on_face = [p for p in points if abs(p[along] - extreme) < 0.02]
        if face is not None:
            x_basis, y_basis = _view_axes(projection)
            on_face = [to_view(point, x_basis, y_basis) for point in face.boundary]
            extreme = face.position
        elif scheme.origin and abs(extreme-scheme.origin[index]) > 1e-6:
            support = _material_support(projection, along)
            if support is not None:
                on_face = [support.lower_2d]
                extreme = support.lower_2d[along]
        lateral = sum(p[1 - along] for p in on_face) / len(on_face)
        anchor = (extreme, lateral) if view_axis == "x" else (lateral, extreme)
        outward = (-1.0, 0.0) if view_axis == "x" else (0.0, -1.0)
        if face is not None:
            anchor = sorted(on_face, key=lambda p: p[1 - along])[len(on_face) // 2]
            box = _box(projection)
            if extreme >= (box[along] + box[along + 2]) / 2.0:
                outward = (1.0, 0.0) if view_axis == "x" else (0.0, 1.0)
        # 该侧所有尺寸带里最往外的那一条，方框要整体让过它再加一点字高余量
        side = outward[along]
        lanes = [side * (d.base[along] - extreme) for d in dims
                 if d.view == name and d.base is not None
                 and side * (d.base[along] - extreme) > 0]
        standoff = max(DATUM_LEADER, max(lanes, default=0.0) + DATUM_BOX / 2.0 + TEXT_HEIGHT)
        # 这条棱有多长，代号就能沿它滑多远：基准标注的是一个平面，滑在面上任何一点
        # 都同样成立；滑出这个范围就是挂到别的面上去了，那是假避让。
        tangent = (-outward[1], outward[0])
        spread = [p[0] * tangent[0] + p[1] * tangent[1] for p in on_face]
        reach = (max(spread) - min(spread)) / 2.0
        if face is not None:
            reach = 0.0  # 选定面的真实边界点，不沿有限面跨孔洞或跳到相邻面。
        marks.append(DatumMark(view=name, anchor=anchor, outward=outward,
                               letter=planes[index], standoff=standoff, reach=reach))
    return tuple(marks)


LANE_START = 8.0        # 第一条尺寸线离视图轮廓的距离
LANE_PITCH = 8.0        # 往外每加一条带的间距


def _compact_lane_assignment(items, original):
    """Try an interval sweep, preserving inner/outer nesting and existing ties.

    Shortest-first first-fit can waste a lane on partially overlapping intervals.
    The sweep minimizes overlap lanes; a color dependency order keeps contained
    dimensions inside their containers. Cyclic dependencies retain the original
    layout, as does a candidate that saves no space.
    """
    ends, assignment = [], {}
    for index in sorted(range(len(items)), key=lambda i: (items[i][1], repr(items[i][0].source))):
        lo, hi = items[index][1]
        lane = next((j for j, end in enumerate(ends) if lo >= end-1e-6), len(ends))
        if lane == len(ends):
            ends.append(hi)
        else:
            ends[lane] = hi
        assignment[index] = lane
    if len(ends) >= max(original, default=-1)+1:
        return original
    before = {lane: set() for lane in range(len(ends))}
    for outer, (_, a, _) in enumerate(items):
        for inner, (_, b, _) in enumerate(items):
            if (a[0] <= b[0] and b[1] <= a[1] and a != b
                    and assignment[outer] != assignment[inner]):
                before[assignment[outer]].add(assignment[inner])
    order = []
    while len(order) < len(ends):
        ready = [lane for lane, dependencies in before.items()
                 if lane not in order and dependencies.issubset(order)]
        if not ready:
            return original
        order.append(min(ready))
    rank = {lane: index for index, lane in enumerate(order)}
    return [rank[assignment[i]] for i in range(len(items))]


def _lane_dimensions(dims, projections):
    """把线性尺寸重新排进**尽量少的**偏移带：短跨在内、长跨在外，相邻不重叠的共用一条。

    早先每发一条尺寸就新开一条带，15 条壁厚从零件底下甩出 120 mm 一条瀑布 ——
    落位指标全绿，因为它只问"画了没有"，不问"能不能读"。这是被截图抓住的那次。
    """
    from dataclasses import replace

    groups: dict[tuple, list] = {}
    for dim in dims:
        if dim.base is None or dim.view not in projections:
            continue
        box = _box(projections[dim.view])
        ux, uy = dim.direction()
        if abs(uy) < 1e-9:
            horizontal = True
            span = (min(dim.p1[0], dim.p2[0]), max(dim.p1[0], dim.p2[0]))
            side = 1 if (dim.base[1] + dim.p1[1]) / 2.0 >= (box[1] + box[3]) / 2.0 else -1
        elif abs(ux) < 1e-9:
            horizontal = False
            span = (min(dim.p1[1], dim.p2[1]), max(dim.p1[1], dim.p2[1]))
            side = 1 if (dim.base[0] + dim.p1[0]) / 2.0 >= (box[0] + box[2]) / 2.0 else -1
        else:
            continue        # 斜尺寸不占直角带，保持原样
        groups.setdefault((dim.view, horizontal, side), []).append((dim, span, box))

    moved = {}
    for (view, horizontal, side), items in groups.items():
        lanes: list[list[tuple[float, float]]] = []
        items = sorted(items, key=lambda t: t[1][1] - t[1][0])
        assignment = []
        # 短跨先占内带：嵌套尺寸自然一层层往外排，读图时才不会互相压字
        for dim, span, box in items:
            for lane, placed in enumerate(lanes):
                if all(span[0] >= hi - 1e-6 or span[1] <= lo + 1e-6 for lo, hi in placed):
                    break
            else:
                lane = len(lanes)
                lanes.append([])
            lanes[lane].append(span)
            assignment.append(lane)
        for (dim, span, box), lane in zip(items, _compact_lane_assignment(items, assignment)):
            offset = LANE_START + LANE_PITCH * lane
            if horizontal:
                y = (box[3] if side > 0 else box[1]) + side * offset
                moved[dim.source or (view, dim.text)] = replace(dim, base=(sum(span) / 2.0, y))
            else:
                x = (box[2] if side > 0 else box[0]) + side * offset
                moved[dim.source or (view, dim.text)] = replace(dim, base=(x, sum(span) / 2.0))

    return tuple(moved.get(d.source or (d.view, d.text), d) for d in dims)


def _coalesce_dimensions(dims, scheme):
    """共用标注仅限相同几何、数值、数量、公差和测量种类；来源逐条留存。"""
    from dataclasses import replace
    from drawform.geometry.features import Bore, group_bores

    constraints = {(c.feature, c.dof): c for c in scheme.plan.constraints if isinstance(c, Sized)}
    bores = {b.feature_id: b for b in scheme.features if isinstance(b, Bore)}
    bore_axes = {g.group_id: tuple(sorted((bores[m].axis_offset(), bores[m].canonical_axis())
                                        for m in g.members)) for g in group_bores(bores.values())}
    groups = {}
    for dim in dims:
        constraint = constraints[dim.source]
        geometry = (dim.p1, dim.p2)
        if dim.kind == 'position':
            # 共线孔位共享同一基准到同一坐标的尺寸，横向偏置不是另一个被测量。
            along = 0 if abs(dim.p1[1]-dim.p2[1]) < 1e-9 else 1
            # 这里只归一化匹配指纹（毫米坐标小数点后12位），原端点和数值原样保存。
            geometry = (round(dim.p1[along],12), round(dim.p2[along],12))
        if dim.kind == 'diameter':
            # 只有确实属于同一组轴线的孔才共享；孔/轴和仅半径相同的圆不能混用。
            axes = bore_axes.get(dim.source[0])
            geometry = (dim.p1, axes) if axes is not None else dim.source
        key = (dim.kind, dim.view, dim.axis, dim.measurement_axis, round(dim.value,12), dim.text, geometry,
               round(constraint.value,12), constraint.quantity, constraint.tolerance, dim.source[1])
        groups.setdefault(key, []).append(dim)
    result = []
    for members in groups.values():
        # 分布圆圆心的语义比成员孔投影更明确，仍保留成员孔来源。
        representative = next((d for d in members if d.source[0].endswith(':center')), members[0])
        result.append(replace(representative, shared_sources=tuple(
            d.source for d in members if d is not representative), shared_witnesses=tuple(
            (d.p1,d.p2) for d in members if d is not representative), shared_values=tuple(
            d.value for d in members if d is not representative)))
    return tuple(result)


def place_scheme(scheme, projections) -> tuple[PlacedDimension, ...]:
    by_key = {(c.feature, c.dof): c for c in scheme.plan.constraints
              if isinstance(c, Sized)}
    dims: list[PlacedDimension] = []

    _place_outline(by_key, projections, dims)

    ordinal = 0
    from drawform.geometry.features import Bore
    diameter_members = _diameter_members(scheme.features)
    for (feature, dof), item in by_key.items():
        if dof not in ("diameter", "counterbore"):
            continue
        # 沉孔直径带着 ⌴ 符号落在它自己的圆上；只改文字不改几何，引出方式与孔径相同。
        prefix = COUNTERBORE_PREFIX if dof == "counterbore" else DIAMETER_PREFIX
        members = diameter_members.get(feature, ())
        angle = math.radians(DIAMETER_ANGLES[ordinal % len(DIAMETER_ANGLES)])
        found = _find_circle(item.value / 2.0, projections, members, angle)
        if found is None:
            wall = next((wall for member in members if isinstance(member, Bore)
                         if (wall := _find_bore_wall(member, projections)) is not None), None)
            if wall is None:
                continue
            view_name, center, point = wall
        else:
            view_name, center, point = found
            ordinal += 1
        dims.append(PlacedDimension(
            # kind 仍是 diameter：DXF 侧按 kind 选尺寸实体类型，沉孔就是按直径画的，
            # 区别只在文字上的 ⌴ 符号。
            "diameter", view_name, center, point,
            None, _text(item.value, item.quantity, prefix), item.value,
            source=(feature, dof)))

    _place_depths(scheme, projections, dims)
    _place_positions(scheme, projections, dims)
    _place_pcd(scheme, projections, dims)
    _place_gaps(scheme, projections, dims)

    return _lane_dimensions(_coalesce_dimensions(dims, scheme), projections)


def measurement_errors(scheme, dimensions):
    """模型毫米下逐来源核对方案与测量；1e-6 是数值误差界，不是制造公差。"""
    expected = {(c.feature, c.dof): c for c in scheme.plan.constraints if isinstance(c, Sized)}
    kinds = {'diameter': 'diameter', 'counterbore': 'diameter', 'depth': 'depth',
             'pcd': 'pcd', 'x': 'position', 'y': 'position', 'z': 'position',
             'length': 'linear', 'width': 'linear', 'height': 'linear', 'gap': 'linear'}
    errors, seen = [], set()
    for dim in dimensions:
        if dim.reference_points_3d:
            references = dim.reference_points_3d
            view = next((v for v in (FRONT,TOP,LEFT) if v.name == dim.view), None)
            if any(len(p) != 3 or not all(math.isfinite(v) for v in p) for p in references):
                errors.append((dim.source, '三维参考点无效'))
            elif view is None:
                errors.append((dim.source, '三维参考点指向未知基本视图'))
            else:
                basis = view_basis(view)
                points = tuple(to_view(p, *basis) for p in references)
                if dim.kind == 'linear' and dim.source and dim.source[0] == 'outline' and len(points) == 2:
                    if math.dist(points[0],dim.p1) > 1e-6 or math.dist(points[1],dim.p2) > 1e-6:
                        errors.append((dim.source, '三维支撑点与轮廓测量端点不一致'))
                elif dim.kind == 'linear' and dim.source and dim.source[1]=='gap' and len(points)==2:
                    supports={row[0]:(row[1],row[2]) for row in scheme.shoulder_supports}
                    expected_points=supports.get(dim.source[0])
                    if expected_points is None or any(math.dist(p,q)>1e-6 for p,q in zip(references,expected_points)) or math.dist(points[0],dim.p1)>1e-6 or math.dist(points[1],dim.p2)>1e-6:
                        errors.append((dim.source,'三维支撑点与肩面来源或测量端点不一致'))
                elif dim.kind == 'position' and dim.axis in WORLD_AXES and len(points) == 1:
                    along = next((i for i,b in enumerate(basis)
                                  if sum(a*c for a,c in zip(b,WORLD_AXES[dim.axis])) > .99), None)
                    if along is None or abs(points[0][along]-dim.p1[along]) > 1e-6:
                        errors.append((dim.source, '三维支撑点与定位基准不一致'))
                else:
                    errors.append((dim.source, '三维参考点数量或用途不一致'))
        if not len(dim.shared_sources) == len(dim.shared_values) == len(dim.shared_witnesses):
            errors.append((dim.source, '共享来源、数值和测量端点数量不一致'))
            continue
        if dim.kind in ('linear', 'position', 'depth') and (
                dim.base is None or len(dim.base) != 2 or not all(math.isfinite(v) for v in dim.base)):
            errors.append((dim.source, '线性尺寸没有有效尺寸线位置'))
        primary = expected.get(dim.source)
        records = [(dim.source, dim.value, (dim.p1, dim.p2)),
                   *zip(dim.shared_sources, dim.shared_values, dim.shared_witnesses)]
        for source, value, (p1, p2) in records:
            if source in seen:
                errors.append((source, '同一来源重复标注'))
            seen.add(source)
            constraint = expected.get(source)
            if constraint is None:
                errors.append((source, '没有对应方案约束'))
                continue
            if kinds.get(constraint.dof) != dim.kind:
                errors.append((source, '尺寸种类与方案不一致'))
            if primary is not None and (constraint.quantity != primary.quantity
                                        or constraint.tolerance != primary.tolerance):
                errors.append((source, '共享标注的数量或公差不同'))
            if not math.isfinite(value) or not math.isfinite(constraint.value):
                errors.append((source, '尺寸值不是有限数'))
                continue
            if abs(value-constraint.value) > 1e-6:
                errors.append((source, '落位值与方案值不一致'))
            if len(p1) != 2 or len(p2) != 2 or not all(math.isfinite(v) for v in (*p1, *p2)):
                errors.append((source, '测量端点无效'))
                continue
            axis = dim.measurement_axis
            if axis is None:
                measured = math.dist(p1, p2)
            else:
                if len(axis) != 2 or not all(math.isfinite(v) for v in axis) or math.hypot(*axis) == 0:
                    errors.append((source, '测量方向无效'))
                    continue
                measured = abs(sum((b-a)*u for a,b,u in zip(p1,p2,axis)))/math.hypot(*axis)
            if dim.kind == 'diameter':
                measured *= 2
            if not math.isfinite(measured) or abs(measured-value) > 1e-6:
                errors.append((source, '真实测量与标注值不一致'))
    return errors

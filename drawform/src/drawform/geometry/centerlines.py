from __future__ import annotations

import math

from drawform.domain.curves import Centerline, Circle2D, Line2D, to_view, view_basis
from drawform.geometry.features import Bore, bolt_circle, group_bores
from drawform.geometry.projection import Projection

OVERSHOOT = 3.5
TOL = 1e-3


def _dot(a, b) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross_circles(circles, bore: Bore):
    radius = bore.diameter / 2.0
    return [c for c in circles if abs(c.radius - radius) < TOL]


def _concentric_radius(circles, center) -> float:
    return max(
        (c.radius for c in circles
         if math.hypot(c.center[0] - center[0], c.center[1] - center[1]) < TOL),
        default=0.0,
    )


def _wall_pairs(lines, span_axis, separation):
    """找出相距 separation 且沿孔轴方向真正重叠的平行线对 —— 那才是同一个孔的两条孔壁。

    只比间距会把底板边缘与凸台壁配成一对（两者恰好相差一个孔径），
    于是画出一条压在实体上的假中心线；同一个孔的两壁必然贯穿同一段深度。
    """
    vertical = span_axis == "y"
    buckets: dict[float, list[tuple[float, float]]] = {}
    for line in lines:
        (x1, y1), (x2, y2) = line.start, line.end
        if vertical:
            if abs(x1 - x2) > TOL:
                continue
            buckets.setdefault(round(x1, 4), []).append((min(y1, y2), max(y1, y2)))
        else:
            if abs(y1 - y2) > TOL:
                continue
            buckets.setdefault(round(y1, 4), []).append((min(x1, x2), max(x1, x2)))

    extents = {position: (min(lo for lo, _ in spans), max(hi for _, hi in spans))
               for position, spans in buckets.items()}

    positions = sorted(buckets)
    found = []
    for i, first in enumerate(positions):
        for second in positions[i + 1:]:
            if abs((second - first) - separation) > TOL:
                continue
            lo_a, hi_a = extents[first]
            lo_b, hi_b = extents[second]
            lo, hi = max(lo_a, lo_b), min(hi_a, hi_b)
            if hi - lo < 0.5 * min(hi_a - lo_a, hi_b - lo_b):
                continue
            found.append(((first + second) / 2.0, lo - OVERSHOOT, hi + OVERSHOOT))
    return found


def _pitch_circles(projection: Projection, bores) -> tuple[Circle2D, ...]:
    """螺栓圆孔系的分布圆，画成细点划线。

    分布圆是**假想圆**，投影里没有它 —— 不画出来，`⌀50 PCD` 这条尺寸就没有
    任何指向的实体，奇数孔更是一条线都拉不出来（没有直径相对的一对圆心）。
    圆心是各孔圆心的平均、半径是平均距离，判据与方案层发 PCD 时同一个
    `bolt_circle`：画出来的圆和发出来的值不可能各说各话。
    """
    if not bores:
        return ()
    x_basis, y_basis = view_basis(projection.direction)
    found = []
    for group in group_bores(bores):
        members = [b for b in bores if b.feature_id in group.members]
        # 只有孔轴**平行于视线**时，孔才是一圈圆点、分布圆才是个圆；
        # 其余视图里孔是两条壁线，那里画分布圆就成了一个谁也没要求的椭圆。
        if len(members) < 3 or any(abs(_dot(b.axis_direction, projection.direction.direction)) < 1 - 1e-6
                                   for b in members):
            continue
        points = [to_view(b.axis_offset(), x_basis, y_basis) for b in members]
        circle = bolt_circle(points)
        if circle is None:
            continue
        center, radius = circle
        found.append(Circle2D(center, radius))
    return tuple(found)


def centerlines_for(projection: Projection, features) -> tuple[Centerline | Circle2D, ...]:
    """按已识别的回转特征补中心线。

    判据来自投影结果本身而不是三维数据：圆视图找 Circle2D 出十字，
    矩形视图找相距等于孔径的两条孔壁线取中线 —— 这样中心线必然与
    已回拟合的解析实体对齐，不会因为两套数据源而漂移。
    """
    curves = tuple(projection.visible) + tuple(projection.hidden)
    circles = tuple(c for c in curves if isinstance(c, Circle2D))
    lines = tuple(c for c in curves if isinstance(c, Line2D))
    x_basis, y_basis = view_basis(projection.direction)

    found: list[Centerline] = []
    targets=tuple(f for f in features if isinstance(f,Bore))
    if not targets:
        from drawform.geometry.stepped_shaft import centerline_shafts
        targets=centerline_shafts(features)
    for feature in targets:
        radius = feature.diameter / 2.0
        along_view = abs(_dot(feature.axis_direction, projection.direction.direction))

        if along_view > 1 - 1e-6:
            for circle in _cross_circles(circles, feature):
                reach = _concentric_radius(circles, circle.center) + OVERSHOOT
                cx, cy = circle.center
                found.append(Centerline((cx, cy - reach), (cx, cy + reach)))
                found.append(Centerline((cx - reach, cy), (cx + reach, cy)))
            continue

        u_axis = _dot(feature.axis_direction, x_basis)
        v_axis = _dot(feature.axis_direction, y_basis)
        if abs(u_axis) <= 1e-6:
            for mid, lo, hi in _wall_pairs(lines, "y", 2 * radius):
                found.append(Centerline((mid, lo), (mid, hi)))
        elif abs(v_axis) <= 1e-6:
            for mid, lo, hi in _wall_pairs(lines, "x", 2 * radius):
                found.append(Centerline((lo, mid), (hi, mid)))

    # 分布圆与十字中心线是两类线，不参与「同轴去重」—— 它们的 key 根本不同。
    # 没有孔时 `targets` 是轴的中心线，不能喂给 `group_bores`。
    bores = tuple(f for f in features if isinstance(f, Bore))
    return _dedupe(found) + _pitch_circles(projection, bores)


def _dedupe(lines: list[Centerline]) -> tuple[Centerline, ...]:
    """同轴孔系（如通孔与其沉孔）会推出重合的中心线，只保留一条。"""
    unique: dict[tuple, Centerline] = {}
    for line in lines:
        key = (
            "v" if abs(line.start[0] - line.end[0]) < TOL else "h",
            round(line.start[0] if abs(line.start[0] - line.end[0]) < TOL else line.start[1], 3),
        )
        existing = unique.get(key)
        if existing is None:
            unique[key] = line
            continue
        if key[0] == "v":
            lo = min(existing.start[1], existing.end[1], line.start[1], line.end[1])
            hi = max(existing.start[1], existing.end[1], line.start[1], line.end[1])
            unique[key] = Centerline((existing.start[0], lo), (existing.start[0], hi))
        else:
            lo = min(existing.start[0], existing.end[0], line.start[0], line.end[0])
            hi = max(existing.start[0], existing.end[0], line.start[0], line.end[0])
            unique[key] = Centerline((lo, existing.start[1]), (hi, existing.start[1]))
    return tuple(unique[key] for key in sorted(unique))

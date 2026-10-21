from __future__ import annotations

import math

import numpy as np

from drawform.domain.curves import Arc2D, Circle2D, Line2D, Polyline2D

LINE_TOL = 1e-6
CIRCLE_TOL = 1e-4
CIRCLE_ABS_TOL = 1e-6  # 解析拟合的几何精度，不是制造允许差


def _straightness(points: np.ndarray):
    """点到最佳拟合直线的最大相对偏差，以及沿主方向排在两端的两个点。

    判直线性不能用首尾连线：圆沿投影方向退化成的边会往返经过同一点，弦长为零，
    这类边看起来完全笔直却永远过不了检查。
    """
    centered = points - points.mean(axis=0)
    _, vectors = np.linalg.eigh(centered.T @ centered)
    major = vectors[:, -1]
    along = centered @ major
    span = float(along.max() - along.min())
    if span < 1e-12:
        return 0.0, points[0], points[-1]
    perpendicular = np.linalg.norm(centered - along[:, None] * major, axis=1)
    return (float(perpendicular.max()) / span,
            points[int(along.argmin())], points[int(along.argmax())])


def _fit_circle(points: np.ndarray):
    """Kasa 代数圆拟合，返回 (center, radius, 相对径向偏差)。"""
    x, y = points[:, 0], points[:, 1]
    design = np.column_stack([x, y, np.ones_like(x)])
    rhs = -(x**2 + y**2)
    try:
        solution, *_ = np.linalg.lstsq(design, rhs, rcond=None)
    except np.linalg.LinAlgError:
        return None
    a, b, c = solution
    cx, cy = -a / 2.0, -b / 2.0
    radius_sq = cx * cx + cy * cy - c
    if radius_sq <= 0:
        return None
    radius = math.sqrt(radius_sq)
    deviation = float(np.max(np.abs(np.hypot(x - cx, y - cy) - radius))) / radius
    return (cx, cy), radius, deviation


def refit(curve):
    """把 HLR 离散出的折线还原成解析曲线。

    OCCT 的 HLR 会把大量精确直线与圆弧表示成 B 样条，离散化后就是一堆折线。
    客户拿到折线团既改不了半径，尺寸线也无法关联，因此必须在出图前还原。
    拟合不上的一律原样返回 —— 宁可保留折线，也不硬凑一个错的解析形状。
    """
    if not isinstance(curve, Polyline2D):
        return curve

    points = np.asarray(curve.points, dtype=float)
    if len(points) < 3:
        return Line2D(start=tuple(points[0]), end=tuple(points[-1]))

    deviation, start, end = _straightness(points)
    if deviation <= LINE_TOL:
        return Line2D(start=tuple(start), end=tuple(end))

    fitted = _fit_circle(points)
    if fitted is not None:
        (cx, cy), radius, deviation = fitted
        if deviation <= CIRCLE_TOL and deviation*radius <= CIRCLE_ABS_TOL:
            unwrapped = np.unwrap(np.arctan2(points[:, 1]-cy, points[:, 0]-cx))
            sweep = float(unwrapped[-1]-unwrapped[0])
            steps = np.diff(unwrapped)
            angle_tol = np.finfo(float).eps*max(1.0, float(np.max(np.abs(unwrapped))))*len(points)
            # 往返轨迹不能用只连接首尾的圆弧代替，否则会漏掉折返前的部分。
            if abs(sweep) <= angle_tol or (np.any(steps > angle_tol) and np.any(steps < -angle_tol)):
                return curve
            closed = math.hypot(
                points[0][0] - points[-1][0], points[0][1] - points[-1][1]
            ) <= CIRCLE_TOL * radius
            if closed:
                # 极短开边的端点也可能相对拟合半径很近；整圆还必须实际走满一圈。
                if not math.isclose(abs(sweep), 2*math.pi, rel_tol=CIRCLE_TOL, abs_tol=0):
                    return curve
                return Circle2D(center=(cx, cy), radius=radius)
            # 沿采样路径累计展开角度，不能只取两端点的 atan2：那样 270° 的弧会被
            # 按 −90° 的互补弧表示，画出来凸到零件外面。unwrap 保持首元素不变。
            return Arc2D(
                center=(cx, cy),
                radius=radius,
                start_angle=float(unwrapped[0]),
                end_angle=float(unwrapped[-1]),
            )

    return curve

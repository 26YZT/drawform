from __future__ import annotations

import math
from dataclasses import dataclass

from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepClass3d import BRepClass3d_SolidClassifier
from OCP.BRepExtrema import BRepExtrema_DistShapeShape, BRepExtrema_IsOnEdge
from OCP.Bnd import Bnd_Box
from OCP.BndLib import BndLib_Add3dCurve
from OCP.GeomAbs import GeomAbs_Circle, GeomAbs_Line
from OCP.GCPnts import GCPnts_UniformDeflection
from OCP.HLRAlgo import HLRAlgo_Projector
from OCP.HLRBRep import HLRBRep_Algo, HLRBRep_HLRToShape
from OCP.TopAbs import TopAbs_EDGE, TopAbs_SOLID, TopAbs_ON
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS, TopoDS_Shape
from OCP.gp import gp_Ax2, gp_Ax3, gp_Pln, gp_Dir, gp_Pnt

from drawform.domain.curves import (
    FRONT,
    TOP,
    LEFT,
    Arc2D,
    Circle2D,
    Line2D,
    Point2D,
    Polyline2D,
    ViewDirection,
    view_basis,
    to_view,
)
from drawform.geometry.refit import refit

#: 出图固定用的三个基本视图。提成一个常量的理由不是好看：产品目前**不参与
#: "这个零件该画几个视图、从哪投"这个决策**，而这是制图的第一号判断。
#: 出图说明要如实写下这件事，所以它必须和 pipeline 用的是同一份定义，
#: 不能一处改了三视图、另一处还在说"我们选了三个视图"。
STANDARD_VIEWS = (FRONT, TOP, LEFT)
# 既有离散弦高预算；支撑参考不能掩盖超出此图面近似范围的错误轨迹。
CURVE_DEFLECTION = .02

__all__ = [
    "FRONT", "TOP", "LEFT", "Arc2D", "Circle2D", "Line2D", "Point2D",
    "Polyline2D", "Projection", "STANDARD_VIEWS", "ViewDirection", "project_view",
]


@dataclass(frozen=True)
class MaterialSupport:
    """真实材料支撑点及其投影；不是HLR近似轮廓的重写。"""
    axis: int
    lower_2d: Point2D
    upper_2d: Point2D
    lower_3d: tuple[float, float, float]
    upper_3d: tuple[float, float, float]


@dataclass(frozen=True)
class Projection:
    direction: ViewDirection
    visible: tuple
    hidden: tuple
    material_supports: tuple[MaterialSupport, ...] = ()


def _stroke_distance(point, curves):
    best = math.inf
    for curve in curves:
        points = curve.sample()
        for a, b in zip(points, points[1:]):
            dx, dy = b[0]-a[0], b[1]-a[1]
            square = dx*dx+dy*dy
            t = max(0.0, min(1.0, ((point[0]-a[0])*dx+(point[1]-a[1])*dy)/square)) if square else 0.0
            best = min(best, math.dist(point, (a[0]+t*dx, a[1]+t*dy)))
    return best


def _material_supports(shape, direction, visible, hidden):
    if shape.ShapeType() != TopAbs_SOLID:
        return ()  # 不在这里选取、合并或拆分实体。
    basis = view_basis(direction)
    world_axes = []
    for vector in basis:
        axis = max(range(3), key=lambda i: abs(vector[i]))
        if abs(vector[axis]-1) > 1e-9 or any(abs(vector[i]) > 1e-9 for i in range(3) if i != axis):
            return ()  # P0固定基本视图之外不推测坐标关系。
        world_axes.append(axis)
    curves = (*visible, *hidden)
    points = [point for curve in curves for point in curve.sample()]
    if not points:
        return ()
    exact = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape, exact, False)
    lo, hi = exact.CornerMin(), exact.CornerMax()
    low, high = (lo.X(), lo.Y(), lo.Z()), (hi.X(), hi.Y(), hi.Z())
    errors = []
    for along, axis in enumerate(world_axes):
        a, b = min(p[along] for p in points), max(p[along] for p in points)
        errors.append(max(abs(a-low[axis]), abs(b-high[axis]), abs((b-a)-(high[axis]-low[axis]))))
    if max(errors) <= 1e-6 or max(errors) > CURVE_DEFLECTION:
        return ()  # 不能用支撑点掩盖超出既有弦高预算的错误投影。
    broad = Bnd_Box()
    BRepBndLib.Add_s(shape, broad, False)
    a, b = broad.CornerMin(), broad.CornerMax()
    start, end = (a.X(), a.Y(), a.Z()), (b.X(), b.Y(), b.Z())
    spans = tuple(b-a for a,b in zip(start,end))
    padding = max(1.0, max(spans)*.01)

    def candidates(axis, upper):
        center = [(a+b)/2 for a,b in zip(start,end)]
        center[axis] = end[axis]+padding if upper else start[axis]-padding
        u, v = (axis+1)%3, (axis+2)%3
        normal, horizontal = [0,0,0], [0,0,0]
        normal[axis], horizontal[u] = 1, 1
        plane = gp_Pln(gp_Ax3(gp_Pnt(*center), gp_Dir(*normal), gp_Dir(*horizontal)))
        face = BRepBuilderAPI_MakeFace(plane, -spans[u]/2-padding, spans[u]/2+padding,
                                      -spans[v]/2-padding, spans[v]/2+padding).Face()
        distance = BRepExtrema_DistShapeShape(shape, face, 1e-9)
        if not distance.IsDone():
            return ()
        result = []
        limit = high[axis] if upper else low[axis]
        for index in range(1, distance.NbSolution()+1):
            point = distance.PointOnShape1(index)
            xyz = (point.X(), point.Y(), point.Z())
            if not all(math.isfinite(x) for x in xyz) or abs(xyz[axis]-limit) > 1e-6:
                continue
            if BRepClass3d_SolidClassifier(shape, point, 1e-7).State() != TopAbs_ON:
                continue
            xy = to_view(xyz, *basis)
            if _stroke_distance(xy, curves) <= CURVE_DEFLECTION:
                result.append((xy, xyz))
        return result

    supports = []
    for along, axis in enumerate(world_axes):
        if errors[along] <= 1e-6:
            continue
        lowers, uppers = candidates(axis, False), candidates(axis, True)
        if not lowers or not uppers:
            continue
        lower, upper = min(((a,b) for a in lowers for b in uppers),
                           key=lambda pair: (abs(pair[0][0][1-along]-pair[1][0][1-along]), pair))
        supports.append(MaterialSupport(along, lower[0], upper[0], lower[1], upper[1]))
    return tuple(supports)


def _edges_of(*compounds):
    out = []
    for comp in compounds:
        if comp is None or comp.IsNull():
            continue
        explorer = TopExp_Explorer(comp, TopAbs_EDGE)
        while explorer.More():
            out.append(TopoDS.Edge(explorer.Current()))
            explorer.Next()
    return out


def _discretize(adaptor, deflection: float = CURVE_DEFLECTION) -> tuple[Point2D, ...]:
    sampler = GCPnts_UniformDeflection(
        adaptor, deflection, adaptor.FirstParameter(), adaptor.LastParameter()
    )
    if not sampler.IsDone():
        return ()
    points = {sampler.Parameter(i): (sampler.Value(i).X(), sampler.Value(i).Y())
              for i in range(1, sampler.NbPoints() + 1)}
    # 弦高采样保证轮廓观感，不保证采到真实极值。只补原曲线上的参数点，
    # 不把包围盒数字拼成轮廓点；参数排序保留原轨迹的连接顺序。
    box = Bnd_Box()
    BndLib_Add3dCurve.AddOptimal_s(adaptor, 1e-9, box)
    low, high = box.CornerMin(), box.CornerMax()
    bounds = (low.X(), low.Y(), low.Z(), high.X(), high.Y(), high.Z())
    for axis in (0, 1):
        other = 1-axis
        padding = max(1.0, bounds[other+3]-bounds[other])
        for upper in (False, True):
            limit = bounds[axis+3*upper]
            sampled = (max if upper else min)(p[axis] for p in points.values())
            if abs(sampled-limit) <= 1e-6:
                continue
            a, b = [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]
            a[axis] = b[axis] = limit
            a[other], b[other] = bounds[other]-padding, bounds[other+3]+padding
            guide = BRepBuilderAPI_MakeEdge(gp_Pnt(*a), gp_Pnt(*b)).Edge()
            distance = BRepExtrema_DistShapeShape(adaptor.Edge(), guide, 1e-9)
            if not distance.IsDone():
                continue
            for index in range(1, distance.NbSolution()+1):
                if distance.SupportTypeShape1(index) != BRepExtrema_IsOnEdge:
                    continue  # 端点已经在原采样中。
                parameter, = distance.ParOnEdgeS1(index)
                if not adaptor.FirstParameter() <= parameter <= adaptor.LastParameter():
                    continue
                point = adaptor.Value(parameter)
                value = (point.X(), point.Y())
                if abs(value[axis]-limit) <= 1e-6:
                    points[parameter] = value
    return tuple(point for parameter, point in sorted(points.items()))


def _to_curve(edge):
    """HLR 的返回已位于投影平面（Z=0），故 X/Y 即视图坐标。"""
    adaptor = BRepAdaptor_Curve(edge)
    kind = adaptor.GetType()

    if kind == GeomAbs_Line:
        start = adaptor.Value(adaptor.FirstParameter())
        end = adaptor.Value(adaptor.LastParameter())
        return Line2D(start=(start.X(), start.Y()), end=(end.X(), end.Y()))

    if kind == GeomAbs_Circle:
        circle = adaptor.Circle()
        center = (circle.Location().X(), circle.Location().Y())
        if adaptor.IsClosed():
            return Circle2D(center=center, radius=circle.Radius())
        phi = circle.YAxis().Direction().AngleWithRef(
            gp_Dir(0, 1, 0), circle.Axis().Direction()
        )
        if circle.Axis().Direction().Z() > 0:
            a1 = adaptor.FirstParameter() - phi
            a2 = adaptor.LastParameter() - phi
        else:
            a1 = -(adaptor.LastParameter() - phi) + math.pi
            a2 = -(adaptor.FirstParameter() - phi) + math.pi
        return Arc2D(center=center, radius=circle.Radius(), start_angle=a1, end_angle=a2)

    return refit(Polyline2D(points=_discretize(adaptor)))


def _key(curve):
    """曲线的几何指纹，用于识别重合边。

    直线按端点排序归一，因为同一条边从相邻两个面投影出来方向可能相反。
    """
    if isinstance(curve, Line2D):
        a = (round(curve.start[0], 6), round(curve.start[1], 6))
        b = (round(curve.end[0], 6), round(curve.end[1], 6))
        return ("line",) + min(a, b) + max(a, b)
    if isinstance(curve, Circle2D):
        return ("circle", round(curve.center[0], 6), round(curve.center[1], 6),
                round(curve.radius, 6))
    if isinstance(curve, Arc2D):
        return ("arc", round(curve.center[0], 6), round(curve.center[1], 6),
                round(curve.radius, 6), round(curve.start_angle, 6),
                round(curve.end_angle, 6))
    return ("polyline",) + tuple((round(x, 6), round(y, 6)) for x, y in curve.points)


def _dedupe(curves):
    seen = set()
    unique = []
    for curve in curves:
        key = _key(curve)
        if key in seen:
            continue
        seen.add(key)
        unique.append(curve)
    return unique


def _support_line(curve):
    """直线支撑线的指纹：单位方向（符号归一）与到原点的有向垂距。"""
    (x1, y1), (x2, y2) = curve.start, curve.end
    dx, dy = x2 - x1, y2 - y1
    length = math.hypot(dx, dy)
    if length < 1e-9:
        return None
    ux, uy = dx / length, dy / length
    if uy < -1e-12 or (abs(uy) <= 1e-12 and ux < 0.0):
        ux, uy = -ux, -uy
    return (round(ux, 8), round(uy, 8), round(ux * y1 - uy * x1, 6))


def _coverage_index(curves):
    """按支撑线归集可见直线的区间，用于判断隐藏线是否被整条遮住。"""
    index: dict[tuple, list[tuple[float, float]]] = {}
    for curve in curves:
        if not isinstance(curve, Line2D):
            continue
        key = _support_line(curve)
        if key is None:
            continue
        ux, uy = key[0], key[1]
        t1 = curve.start[0] * ux + curve.start[1] * uy
        t2 = curve.end[0] * ux + curve.end[1] * uy
        index.setdefault(key, []).append((min(t1, t2), max(t1, t2)))
    return index


def _uncovered_gaps(intervals, lo, hi, tol):
    reach = lo
    gaps = []
    for start, end in sorted(intervals):
        if start > reach + tol:
            gaps.append((reach, start))
        reach = max(reach, end)
        if reach >= hi - tol:
            break
    if hi - reach > tol:
        gaps.append((reach, hi))
    return gaps


def _trim_hidden(coverage, curve, tol: float = 1e-6):
    """裁掉隐藏线上被可见线遮住的部分，只留下没被遮住的若干段。

    CAD 系统都是这么画的：整条保留会让虚线压在实线上，图面既脏又误导。
    """
    if not isinstance(curve, Line2D):
        return [curve]
    key = _support_line(curve)
    if key is None or key not in coverage:
        return [curve]

    ux, uy, offset = key
    t1 = curve.start[0] * ux + curve.start[1] * uy
    t2 = curve.end[0] * ux + curve.end[1] * uy
    lo, hi = min(t1, t2), max(t1, t2)

    def point(t):
        return (ux * t - uy * offset, uy * t + ux * offset)

    return [Line2D(start=point(a), end=point(b))
            for a, b in _uncovered_gaps(coverage[key], lo, hi, tol)]


def _clean(visible, hidden):
    """去掉层内重复边，并把隐藏线裁到只剩没被可见线遮住的部分。

    平板顶面与底面的轮廓在俯视图里投影重合，隐藏线原样输出会把实线叠成灰线；
    被若干段共线可见线拼起来遮住的隐藏线同理，只是对不上「整条对整条」。
    """
    visible = _dedupe(visible)
    visible_keys = {_key(c) for c in visible}
    coverage = _coverage_index(visible)

    kept = []
    for curve in _dedupe(hidden):
        if _key(curve) in visible_keys:
            continue
        kept.extend(_trim_hidden(coverage, curve))
    return tuple(visible), tuple(_dedupe(kept))


def project_view(shape: TopoDS_Shape, direction: ViewDirection) -> Projection:
    """正交投影 + 隐藏线消除。

    OCCT 的 HLR 必须先 Update() 再 Hide()，然后才同时读 VCompound 与 HCompound；
    在 Hide() 之前读到的 VCompound 是未消隐的全部轮廓，会把隐藏边当成可见边输出。
    """
    ax2 = gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(*direction.direction), gp_Dir(*direction.horizontal))
    algo = HLRBRep_Algo()
    algo.Add(shape)
    algo.Projector(HLRAlgo_Projector(ax2))
    algo.Update()
    algo.Hide()

    result = HLRBRep_HLRToShape(algo)
    visible = _edges_of(result.VCompound(), result.OutLineVCompound(), result.Rg1LineVCompound())
    hidden = _edges_of(result.HCompound(), result.OutLineHCompound(), result.Rg1LineHCompound())

    visible, hidden = _clean([_to_curve(e) for e in visible],
                             [_to_curve(e) for e in hidden])

    return Projection(direction=direction, visible=visible, hidden=hidden,
                      material_supports=_material_supports(shape, direction, visible, hidden))

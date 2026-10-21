from __future__ import annotations

import math
from dataclasses import dataclass

from OCP.Bnd import Bnd_Box
from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.BRepTools import BRepTools_WireExplorer
from OCP.GCPnts import GCPnts_UniformDeflection
from OCP.GeomAbs import GeomAbs_Plane
from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED, TopAbs_WIRE
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS, TopoDS_Shape
from OCP.gp import gp_Pnt

from drawform.domain.curves import Line2D, ViewDirection, to_view, view_basis
from drawform.geometry.projection import Projection, project_view

AXIS_INDEX = {"x": 0, "y": 1, "z": 2}
HATCH_ANGLE = math.pi / 4.0
HATCH_SPACING = 4.0
DEFLECTION = 0.02


@dataclass(frozen=True)
class Section:
    projection: Projection
    hatch: tuple[Line2D, ...] = ()


def _extents(shape: TopoDS_Shape):
    box = Bnd_Box()
    BRepBndLib.Add_s(shape, box)
    return box.CornerMin().Coord(), box.CornerMax().Coord()


def _near_half_space(mins, maxs, index: int, at: float, sign: float, reach: float):
    """靠观察者一侧、被剖切平面切掉的那半个空间，用一个足够大的盒子表示。"""
    lo = [mins[i] - reach for i in range(3)]
    hi = [maxs[i] + reach for i in range(3)]
    if sign > 0:
        lo[index] = at
    else:
        hi[index] = at
    return BRepPrimAPI_MakeBox(gp_Pnt(*lo),
                               hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]).Shape()


def _component(vector, index: int) -> float:
    return (vector.X(), vector.Y(), vector.Z())[index]


def _edge_polygon(edge, x_basis, y_basis):
    points = []
    adaptor = BRepAdaptor_Curve(edge)
    sampler = GCPnts_UniformDeflection(adaptor, DEFLECTION,
                                       adaptor.FirstParameter(), adaptor.LastParameter())
    if sampler.IsDone():
        for i in range(1, sampler.NbPoints() + 1):
            p = sampler.Value(i)
            points.append(to_view((p.X(), p.Y(), p.Z()), x_basis, y_basis))
    return points


def _cut_face_loops(shape: TopoDS_Shape, index: int, at: float, x_basis, y_basis):
    """取出落在剖切平面上的那些面，把它们的外轮廓与内孔轮廓都转成二维多边形。"""
    loops = []
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    while explorer.More():
        face = TopoDS.Face(explorer.Current())
        surface = BRepAdaptor_Surface(face)
        if surface.GetType() == GeomAbs_Plane:
            plane = surface.Plane()
            along = abs(_component(plane.Axis().Direction(), index))
            located = abs(_component(plane.Location(), index) - at)
            if along > 0.5 and located < 1e-6:
                wires = TopExp_Explorer(face, TopAbs_WIRE)
                while wires.More():
                    loop = []
                    # 拓扑枚举不保证连通顺序；参数采样也不会自动服从边的方向。
                    # 两者都修正，才能闭合真实轮廓，避免把矩形接成自交折线。
                    edges = BRepTools_WireExplorer(TopoDS.Wire(wires.Current()), face)
                    while edges.More():
                        points = _edge_polygon(edges.Current(), x_basis, y_basis)
                        if edges.Orientation() == TopAbs_REVERSED:
                            points.reverse()
                        loop.extend(points)
                        edges.Next()
                    if len(loop) >= 3:
                        loops.append(loop)
                    wires.Next()
        explorer.Next()
    return loops


def _hatch(loops, spacing: float, angle: float = HATCH_ANGLE):
    """用 45° 等距平行线填充这些闭合轮廓围出的区域。

    把所有轮廓的边界一起求交、再按奇偶配对，孔洞自然留白，不需要区分内外环。
    偏移取在网格正中而不是从轮廓起点开始，避免剖面线正好穿过顶点导致交点成对丢失。
    """
    if not loops:
        return ()

    direction = (math.cos(angle), math.sin(angle))
    normal = (-direction[1], direction[0])
    vertices = [point for loop in loops for point in loop]
    projected = [point[0] * normal[0] + point[1] * normal[1] for point in vertices]
    lo, hi = min(projected), max(projected)

    lines = []
    offset = lo + 0.5 * spacing
    while offset <= hi:
        crossings = []
        for loop in loops:
            for i in range(len(loop)):
                a, b = loop[i], loop[(i + 1) % len(loop)]
                da = a[0] * normal[0] + a[1] * normal[1] - offset
                db = b[0] * normal[0] + b[1] * normal[1] - offset
                if da * db >= 0.0:
                    continue
                t = da / (da - db)
                x = a[0] + (b[0] - a[0]) * t
                y = a[1] + (b[1] - a[1]) * t
                crossings.append(x * direction[0] + y * direction[1])
        crossings.sort()
        for i in range(0, len(crossings) - 1, 2):
            start, end = crossings[i], crossings[i + 1]
            if end - start < 1e-6:
                continue
            lines.append(Line2D(
                start=(direction[0] * start + normal[0] * offset,
                       direction[1] * start + normal[1] * offset),
                end=(direction[0] * end + normal[0] * offset,
                     direction[1] * end + normal[1] * offset)))
        offset += spacing
    return tuple(lines)


def section_view(shape: TopoDS_Shape, direction: ViewDirection, axis: str, at: float,
                 hatch_spacing: float = HATCH_SPACING) -> Section:
    """单一剖切平面的全剖视图：切掉靠观察者的一半，再按同一方向投影并给剖切面打剖面线。

    剖切平面必须垂直于视线，所以剖切轴就是视向所在的那根轴。
    """
    index = AXIS_INDEX[axis]
    sign = direction.direction[index]
    if abs(sign) < 0.5:
        raise ValueError(
            f"剖切轴 {axis} 与视线方向 {direction.name} 不平行，"
            "全剖视图的剖切平面必须垂直于视线"
        )

    mins, maxs = _extents(shape)
    reach = 2.0 * max(maxs[i] - mins[i] for i in range(3)) + abs(at)
    removed = _near_half_space(mins, maxs, index, at, sign, reach)
    kept = BRepAlgoAPI_Cut(shape, removed).Shape()

    x_basis, y_basis = view_basis(direction)
    hatch = _hatch(_cut_face_loops(kept, index, at, x_basis, y_basis), hatch_spacing)

    return Section(projection=project_view(kept, direction), hatch=hatch)

"""End shoulders from shared STEP topology, independent of the through-hole probes.

Two overlapping axial intervals do not establish a shoulder. A cylinder must share
an edge with a perpendicular planar face, and that same face must share an edge
with a smaller coaxial *internal* cylinder at the same finite endpoint.
"""
from __future__ import annotations

import math

from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
from OCP.collections import List_TopoDS_Shape
from OCP.GeomAbs import GeomAbs_Circle, GeomAbs_Plane
from OCP.Precision import Precision
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_FORWARD, TopAbs_SOLID, TopAbs_VERTEX
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

from drawform.geometry.features import _canonical, _dot, _end_frames, _xyz


def _shoulder_parameter(point, normal, offset, axis):
    # Angular() is an angular tolerance, not a tolerance on 1-cos(angle).
    if min(math.dist(axis, normal), math.dist(axis, tuple(-v for v in normal))) > Precision.Angular_s():
        return None
    denominator = math.fsum(a*n for a, n in zip(axis, normal))
    return math.fsum((p-o)*n for p, o, n in zip(point, offset, normal)) / denominator


def _end_side(at, span, tolerance):
    sides = [i for i, end in enumerate(span) if abs(end-at) <= tolerance]
    if len(sides) > 1:
        raise ValueError("同一肩面在几何容差内匹配孔的两个端部，不能确定单独入口")
    return sides[0] if sides else None


def _face_adjacency(shape):
    faces = []
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    while explorer.More():
        faces.append(TopoDS.Face(explorer.Current()))
        explorer.Next()
    buckets = {}
    for index, face in enumerate(faces):
        edges = TopExp_Explorer(face, TopAbs_EDGE)
        while edges.More():
            # Orientation is face-local; IsSame verifies identity after hash bucketing.
            edge = edges.Current().Oriented(TopAbs_FORWARD)
            bucket = buckets.setdefault(hash(edge), [])
            found = next((item for item in bucket if item[0].IsSame(edge)), None)
            if found is None:
                bucket.append((edge, {index}))
            else:
                found[1].add(index)
            edges.Next()
    neighbors = [set() for _ in faces]
    for bucket in buckets.values():
        for _edge, owners in bucket:
            for index in owners:
                neighbors[index].update(owners - {index})
    return faces, neighbors, [item for bucket in buckets.values() for item in bucket]


class BoreTopologyError(ValueError):
    """Topology evidence could not be verified; the pipeline must not write a drawing."""


def mouth_region_clear(model, bore, mouth):
    """Check that the measured-radius mouth is clear at model resolution.

    Use a full analytic cylinder in a short outward neighborhood, rather than
    isolated radial probes. A cap or narrowing cone intersects this neighborhood;
    unrelated material farther away is not evidence that the mouth is closed.
    The neighborhood is 32 times the mouth's local face/edge/vertex tolerance
    (a numerical analysis scale, not a manufacturing tolerance). The source shape
    is not modified.
    """
    if bore.axis_span is None or mouth not in bore.axis_span:
        raise BoreTopologyError("沉孔入口不在已核验的有限圆柱端部")
    sign = 1 if mouth == bore.axis_span[1] else -1
    direction = tuple(sign*x for x in bore.canonical_axis())
    start = bore.point_at(mouth)
    side = 1 if sign == 1 else 0
    tolerance = max(Precision.Confusion_s(), bore.end_tolerances[side])
    tool = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(*start), gp_Dir(*direction)),
                                    bore.diameter/2, 32*tolerance).Shape()
    arguments, tools = List_TopoDS_Shape(), List_TopoDS_Shape()
    arguments.Append(model.shape); tools.Append(tool)
    operation = BRepAlgoAPI_Common()
    operation.SetArguments(arguments); operation.SetTools(tools)
    operation.SetNonDestructive(True); operation.SetRunParallel(False)
    operation.Build()
    if not operation.IsDone():
        raise BoreTopologyError("沉孔入口邻域无法核验")
    overlap = operation.Shape()
    if overlap.IsNull() or not BRepCheck_Analyzer(overlap).IsValid():
        raise BoreTopologyError("沉孔入口邻域的相交结果无效，不能视为畅通")
    return not TopExp_Explorer(overlap, TopAbs_SOLID).More()


def end_neighbors(shape, bores, internal_faces):
    try:
        return _end_neighbors(shape, bores, internal_faces)
    except Exception as error:
        raise BoreTopologyError(f"孔端肩面连接无法核验：{type(error).__name__}: {error}") from error


def _end_neighbors(shape, bores, internal_faces):
    """Return lower/upper smaller-bore ids; no geometry is generated or modified."""
    if not bores:
        return {}, {}
    faces, neighbors, edges = _face_adjacency(shape)
    face_bores = {}
    tolerances = {b.feature_id: [Precision.Confusion_s(), Precision.Confusion_s()] for b in bores}
    by_id = {bore.feature_id: bore for bore in bores}
    for face, adaptor, cylinder in internal_faces:
        frame = _end_frames(adaptor, cylinder)
        if frame is None:
            raise ValueError("内圆柱端部缺少有限几何依据")
        ends, direction = frame
        axis = _canonical(direction)
        span = tuple(sorted(_dot(point, axis) for point, _ in ends))
        location = _xyz(cylinder.Location())
        offset = tuple(value - _dot(location, axis) * component
                       for value, component in zip(location, axis))
        tolerance = max(Precision.Confusion_s(), BRep_Tool.Tolerance_s(face))
        matches = [bore.feature_id for bore in bores
                   if bore.axis_span is not None
                   and abs(bore.diameter - 2 * cylinder.Radius()) <= tolerance
                   and math.dist(bore.axis_offset(), offset) <= tolerance
                   and math.dist(bore.canonical_axis(), axis) <= Precision.Angular_s()
                   and max(abs(x-y) for x, y in zip(bore.axis_span, span)) <= tolerance]
        if len(matches) != 1:
            raise ValueError("内圆柱面与孔的几何身份无法唯一对应")
        index = next(i for i, source in enumerate(faces) if source.IsSame(face))
        face_bores[index] = matches[0]
        tolerances[matches[0]] = [max(value, tolerance) for value in tolerances[matches[0]]]
    # A mouth can meet a cone or fillet, not only a plane. Use the actual circular
    # end edge to localize all incident tolerances; distant faces and longitudinal
    # cylinder seams must not enlarge the neighborhood at the opposite end.
    for edge, owners in edges:
        cylinders = owners & face_bores.keys()
        if not cylinders:
            continue
        edge = TopoDS.Edge(edge)
        curve = BRepAdaptor_Curve(edge)
        if curve.GetType() != GeomAbs_Circle:
            continue
        circle = curve.Circle()
        tolerance = max(Precision.Confusion_s(), BRep_Tool.Tolerance_s(edge),
                        *(BRep_Tool.Tolerance_s(faces[i]) for i in owners))
        vertices = TopExp_Explorer(edge, TopAbs_VERTEX)
        while vertices.More():
            tolerance = max(tolerance, BRep_Tool.Tolerance_s(TopoDS.Vertex(vertices.Current())))
            vertices.Next()
        for index in cylinders:
            bore = by_id[face_bores[index]]
            at = _shoulder_parameter(_xyz(circle.Location()), _xyz(circle.Axis().Direction()),
                                     bore.axis_offset(), bore.canonical_axis())
            if at is not None:
                side = _end_side(at, bore.axis_span, tolerance)
                if side is not None:
                    tolerances[bore.feature_id][side] = max(tolerances[bore.feature_id][side], tolerance)
    result = {bore.feature_id: [set(), set()] for bore in bores}
    for index, bore_id in face_bores.items():
        bore = by_id[bore_id]
        axis = bore.canonical_axis()
        for shoulder_index in neighbors[index]:
            face = faces[shoulder_index]
            adaptor = BRepAdaptor_Surface(face)
            if adaptor.GetType() != GeomAbs_Plane:
                continue
            plane = adaptor.Plane()
            at = _shoulder_parameter(_xyz(plane.Location()), _xyz(plane.Axis().Direction()),
                                     bore.axis_offset(), axis)
            if at is None:
                continue
            tolerance = max(Precision.Confusion_s(), BRep_Tool.Tolerance_s(face),
                            BRep_Tool.Tolerance_s(faces[index]))
            side = _end_side(at, bore.axis_span, tolerance)
            if side is None:
                continue
            tolerances[bore_id][side] = max(tolerances[bore_id][side], tolerance)
            for other_index in neighbors[shoulder_index]:
                other_id = face_bores.get(other_index)
                if other_id is None or other_id == bore_id:
                    continue
                other = by_id[other_id]
                tol = max(tolerance, BRep_Tool.Tolerance_s(faces[other_index]))
                if (bore.diameter-other.diameter <= 2*tol
                        or math.dist(bore.axis_offset(), other.axis_offset()) > tol
                        or math.dist(axis, other.canonical_axis()) > Precision.Angular_s()
                        or min(abs(at-value) for value in other.axis_span) > tol):
                    continue
                result[bore_id][side].add(other_id)
    return ({key: tuple(tuple(sorted(ids)) for ids in ends) for key, ends in result.items()},
            {key: tuple(values) for key, values in tolerances.items()})

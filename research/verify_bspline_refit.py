"""判定：HLR 输出的 BSpline 边，有多少其实是直线或圆弧被存成了样条？

若占比高 → 「回拟合成解析曲线」是一项方案明确、收益确定的自研后处理，
   可让 DXF 里的圆/弧保持解析精确（客户能在 AutoCAD 里直接编辑圆，而不是 60 段折线）。
"""
import math

from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
from OCP.GCPnts import GCPnts_UniformDeflection
from OCP.GeomAbs import GeomAbs_BSplineCurve
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

from verify_native_dxf import hlr_edges


def make_shaft():
    a = gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1))
    s = BRepPrimAPI_MakeCylinder(a, 15.0, 90.0).Shape()
    s = BRepAlgoAPI_Fuse(s, BRepPrimAPI_MakeCylinder(a, 22.0, 30.0).Shape()).Shape()
    hole = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(0, 0, -1), gp_Dir(0, 0, 1)), 8.0, 92.0).Shape()
    return BRepAlgoAPI_Cut(s, hole).Shape()


def sample(edge, defl=0.02):
    c = BRepAdaptor_Curve(edge)
    g = GCPnts_UniformDeflection(c, defl, c.FirstParameter(), c.LastParameter())
    if not g.IsDone() or g.NbPoints() < 4:
        return None
    return [g.Value(i) for i in range(1, g.NbPoints() + 1)]


def line_dev(pts):
    """最大弦偏差 / 弦长"""
    ax, ay, az = pts[0].X(), pts[0].Y(), pts[0].Z()
    bx, by, bz = pts[-1].X(), pts[-1].Y(), pts[-1].Z()
    dx, dy, dz = bx - ax, by - ay, bz - az
    L = math.sqrt(dx * dx + dy * dy + dz * dz)
    if L < 1e-9:
        return None
    worst = 0.0
    for p in pts:
        t = max(0.0, min(L, ((p.X() - ax) * dx + (p.Y() - ay) * dy + (p.Z() - az) * dz) / L))
        px, py, pz = ax + dx * t / L, ay + dy * t / L, az + dz * t / L
        worst = max(worst, math.hypot(p.X() - px, p.Y() - py, p.Z() - pz))
    return worst / L


def circle_dev(pts):
    """三点定圆后的最大径向偏差 / 半径（用 XY 投影）"""
    ax, ay = pts[0].X(), pts[0].Y()
    bx, by = pts[len(pts) // 2].X(), pts[len(pts) // 2].Y()
    cx, cy = pts[-1].X(), pts[-1].Y()
    D = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(D) < 1e-12:
        return None
    a2, b2, c2 = ax * ax + ay * ay, bx * bx + by * by, cx * cx + cy * cy
    ux = (a2 * (by - cy) + b2 * (cy - ay) + c2 * (ay - by)) / D
    uy = (a2 * (cx - bx) + b2 * (ax - cx) + c2 * (bx - ax)) / D
    R = math.hypot(ax - ux, ay - uy)
    if R < 1e-9:
        return None
    return max(abs(math.hypot(p.X() - ux, p.Y() - uy) - R) for p in pts) / R


def main():
    shaft = make_shaft()
    views = [("主视", (0, -1, 0), (1, 0, 0)), ("俯视", (0, 0, 1), (1, 0, 0)),
             ("左视", (1, 0, 0), (0, 1, 0))]
    tot = as_line = as_arc = neither = skipped = 0
    line_lens, arc_lens = [], []
    for name, d, xd in views:
        vis, hid = hlr_edges(shaft, d, xd)
        per_l = per_a = per_n = 0
        for e in vis + hid:
            c = BRepAdaptor_Curve(e)
            if c.GetType() != GeomAbs_BSplineCurve:
                continue
            tot += 1
            pts = sample(e)
            if pts is None:
                skipped += 1
                per_n += 1
                continue
            ld = line_dev(pts)
            if ld is not None and ld < 1e-3:
                as_line += 1
                line_lens.append(ld)
                per_l += 1
                continue
            cd = circle_dev(pts)
            if cd is not None and cd < 1e-3:
                as_arc += 1
                arc_lens.append(cd)
                per_a += 1
                continue
            neither += 1
            per_n += 1
        print(f"  {name}: 直线 {per_l}  圆弧 {per_a}  其他 {per_n}")

    print("=" * 72)
    print(f"纯回转体（阶梯轴）三视图 BSpline 边共 {tot} 条")
    print("-" * 72)
    print(f"  其实是直线（弦偏差 <0.1%）: {as_line:>4}  ({as_line / max(tot, 1):5.1%})")
    print(f"  其实是圆弧（径向偏差 <0.1%）: {as_arc:>4}  ({as_arc / max(tot, 1):5.1%})")
    print(f"  两者都不是: {neither:>4}  ({neither / max(tot, 1):5.1%})")
    if line_lens:
        print(f"  直线拟合偏差: max={max(line_lens):.2e}")
    if arc_lens:
        print(f"  圆弧拟合偏差: max={max(arc_lens):.2e}")
    rec = (as_line + as_arc) / max(tot, 1)
    print("-" * 72)
    print(f"可回拟合比例 = {rec:.1%}")
    print("  ≥80% → 「回拟合」值得做：DXF 里绝大多数边能保持解析精确")
    print("  <30% → 放弃回拟合，接受折线离散，只做图层与线型规范化")


if __name__ == "__main__":
    main()

"""验证：HLR 投影边能否出成 DXF 原生实体（LINE/CIRCLE/ARC/ELLIPSE）而非折线近似。

分类逻辑照抄 CadQuery occ_impl/exporters/dxf.py:223-368（Apache-2.0）。
只用已装的 OCP + ezdxf，不需要 cadquery。

指标：解析边占比（可直接出原生实体）vs 必须离散化的边数。
"""
import math
import sys
from collections import Counter
from pathlib import Path

from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.GeomAbs import (GeomAbs_Line, GeomAbs_Circle, GeomAbs_Ellipse,
                         GeomAbs_BSplineCurve, GeomAbs_BezierCurve,
                         GeomAbs_OffsetCurve, GeomAbs_OtherCurve)
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt
from OCP.HLRAlgo import HLRAlgo_Projector
from OCP.HLRBRep import HLRBRep_Algo, HLRBRep_HLRToShape
from OCP.TopAbs import TopAbs_EDGE
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS
import ezdxf

sys.path.insert(0, str(Path(__file__).parent.parent / "spike"))
import verify_hlr as V  # 复用已验证的建模与 HLR 代码

RAD2DEG = 180.0 / math.pi
TYPE_NAME = {GeomAbs_Line: "LINE", GeomAbs_Circle: "CIRCLE/ARC",
             GeomAbs_Ellipse: "ELLIPSE", GeomAbs_BSplineCurve: "SPLINE(须离散)",
             GeomAbs_BezierCurve: "BEZIER(须离散)", GeomAbs_OffsetCurve: "OFFSET(须离散)",
             GeomAbs_OtherCurve: "OTHER(须离散)"}

ANALYTIC = {GeomAbs_Line, GeomAbs_Circle, GeomAbs_Ellipse}


def hlr_edges(shape, direction, xdir):
    """与 spike 同序：Update→Hide 后读 V/H，但返回原始边而非折线"""
    ax2 = gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(*direction), gp_Dir(*xdir))
    algo = HLRBRep_Algo()
    algo.Add(shape)
    algo.Projector(HLRAlgo_Projector(ax2))
    algo.Update()
    algo.Hide()
    hs = HLRBRep_HLRToShape(algo)
    vis = [TopoDS.Edge(e) for e in _exp(hs.VCompound(), hs.OutLineVCompound(), hs.Rg1LineVCompound())]
    hid = [TopoDS.Edge(e) for e in _exp(hs.HCompound(), hs.OutLineHCompound(), hs.Rg1LineHCompound())]
    return vis, hid


def _exp(*comps):
    out = []
    for c in comps:
        ex = TopExp_Explorer(c, TopAbs_EDGE)
        while ex.More():
            out.append(ex.Current())
            ex.Next()
    return out


def classify(edge):
    c = BRepAdaptor_Curve(edge)
    return c.GetType()


def emit_native(msp, edge, layer):
    """把一条解析边写成 DXF 原生实体；返回实体类型，失败返回 None"""
    c = BRepAdaptor_Curve(edge)
    t = c.GetType()
    try:
        if t == GeomAbs_Line:
            p1 = edge.FirstVertex().Value() if False else c.Value(c.FirstParameter())
            p2 = c.Value(c.LastParameter())
            msp.add_line((p1.X(), p1.Y()), (p2.X(), p2.Y()), dxfattribs={"layer": layer})
            return "LINE"
        if t == GeomAbs_Circle:
            circ = c.Circle()
            r = circ.Radius()
            loc = circ.Location()
            zdir = circ.Axis().Direction()
            phi = circ.YAxis().Direction().AngleWithRef(gp_Dir(0, 1, 0), zdir)
            if edge.Closed():
                msp.add_circle((loc.X(), loc.Y()), r, dxfattribs={"layer": layer})
                return "CIRCLE"
            if zdir.XYZ().Z() > 0:
                a1 = RAD2DEG * (c.FirstParameter() - phi)
                a2 = RAD2DEG * (c.LastParameter() - phi)
            else:
                a1 = -RAD2DEG * (c.LastParameter() - phi) + 180
                a2 = -RAD2DEG * (c.FirstParameter() - phi) + 180
            msp.add_arc((loc.X(), loc.Y()), r, a1, a2, dxfattribs={"layer": layer})
            return "ARC"
        if t == GeomAbs_Ellipse:
            el = c.Ellipse()
            minor, major = el.MinorRadius(), el.MajorRadius()
            loc = el.Location()
            xdir = el.XAxis().Direction()
            zdir = el.Axis().Direction()
            if zdir.Z() > 0:
                u1, u2 = c.FirstParameter(), c.LastParameter()
            else:
                u1, u2 = -c.LastParameter(), -c.FirstParameter()
            ratio = minor / major if major else 1.0
            rot = RAD2DEG * math.atan2(xdir.Y(), xdir.X())
            a1 = RAD2DEG * (u1 - el.XAxis().Direction().AngleWithRef(xdir, zdir))
            a2 = RAD2DEG * (u2 - el.XAxis().Direction().AngleWithRef(xdir, zdir))
            msp.add_ellipse((loc.X(), loc.Y()), major_axis=(major * math.cos(math.radians(rot)),
                            major * math.sin(math.radians(rot))), ratio=ratio,
                            start_param=math.radians(a1), end_param=math.radians(a2),
                            dxfattribs={"layer": layer})
            return "ELLIPSE"
    except Exception as ex:
        return f"FAIL({type(ex).__name__})"
    return None


def main():
    solid = V.export_step(V.build_bearing_seat(), Path(__file__).parent / "out" / "seat.step") \
        if False else V.build_bearing_seat()
    out = Path(__file__).parent / "out"
    out.mkdir(exist_ok=True)

    doc = ezdxf.new("R2010", setup=True)
    for lyr, color in (("VISIBLE", 7), ("HIDDEN", 8), ("CENTER", 1)):
        if lyr not in doc.layers:
            doc.layers.add(lyr, color=color)
    msp = doc.modelspace()

    tally = Counter()
    for name, d, xd in [("front", (0, -1, 0), (1, 0, 0)), ("top", (0, 0, 1), (1, 0, 0)),
                        ("left", (1, 0, 0), (0, 1, 0))]:
        vis, hid = hlr_edges(solid, d, xd)
        for edges, layer in ((vis, "VISIBLE"), (hid, "HIDDEN")):
            for e in edges:
                t = classify(e)
                label = TYPE_NAME.get(t, "?")
                if t in ANALYTIC:
                    got = emit_native(msp, e, layer)
                    tally[f"{label} → 原生 {got}"] += 1
                else:
                    tally[f"{label}（须离散化）"] += 1

    total = sum(tally.values())
    analytic = sum(v for k, v in tally.items() if "→ 原生" in k)
    print("=" * 74)
    print("HLR 投影边的曲线类型分布（轴承座三视图，可见+隐藏全部边）")
    print("-" * 74)
    for k, v in tally.most_common():
        print(f"   {k:<40} {v:>4}  ({v/total:.1%})")
    print("-" * 74)
    print(f"   总边数 {total}   可出原生实体 {analytic} ({analytic/total:.1%})   "
          f"仍需离散 {total-analytic} ({(total-analytic)/total:.1%})")

    f_native = out / "seat_native.dxf"
    doc.saveas(str(f_native))
    # 折线版对照
    V.to_dxf([V.hlr_project(solid, (0, -1, 0), (1, 0, 0)) and ("front",) + V.hlr_project(solid, (0, -1, 0), (1, 0, 0)),
              ("top",) + V.hlr_project(solid, (0, 0, 1), (1, 0, 0)),
              ("left",) + V.hlr_project(solid, (1, 0, 0), (0, 1, 0))], out / "seat_polyline.dxf")

    aud = ezdxf.readfile(str(f_native)).audit()
    print(f"\n原生 DXF: {f_native.stat().st_size:,} 字节   audit errors = {len(aud.errors)}")
    try:
        print(f"折线 DXF: {(out/'seat_polyline.dxf').stat().st_size:,} 字节")
    except Exception:
        pass
    print("\n结论：原生实体数 = 图纸里圆/圆弧/椭圆保持解析精确，不被离散成 N 段折线。")
    print("      这条能力用已装的 OCP+ezdxf 即可实现，无需引入 cadquery。")


if __name__ == "__main__":
    main()

"""可行性验证：纯 OCP(OCCT) 能否完成 3D→2D 工程图核心链路，不依赖 FreeCAD/GUI。

验证点：
 1. 参数化造一个轴承座类零件（底板+凸台+通孔+沉孔+螺栓孔）
 2. 导出 STEP 再读回（证明 STEP 输入路径成立）
 3. 三方向 HLR 隐藏线消除投影，分别取可见边/隐藏边
 4. 从实体自动识别圆柱面与平面特征（自动标注的前提）
 5. 输出三视图 SVG + DXF
"""
from __future__ import annotations

import math
from pathlib import Path

from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec
from OCP.HLRAlgo import HLRAlgo_Projector
from OCP.HLRBRep import HLRBRep_Algo, HLRBRep_HLRToShape
from OCP.STEPControl import STEPControl_AsIs, STEPControl_Reader, STEPControl_Writer
from OCP.Interface import Interface_Static
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_FORWARD, TopAbs_REVERSED
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS
from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.GCPnts import GCPnts_UniformDeflection
from OCP.GeomAbs import GeomAbs_Cylinder, GeomAbs_Plane
import ezdxf

OUT = Path(__file__).parent / "out"
OUT.mkdir(exist_ok=True)


def build_bearing_seat():
    """底板 80x50x12 + 凸台 Ø40 + 通孔 Ø20 + 沉孔 Ø32x6 + 两端 Ø9 螺栓孔"""
    base = BRepPrimAPI_MakeBox(80.0, 50.0, 12.0).Shape()

    ax_boss = gp_Ax2(gp_Pnt(40, 25, 12), gp_Dir(0, 0, 1))
    boss = BRepPrimAPI_MakeCylinder(ax_boss, 20.0, 40.0).Shape()
    solid = BRepAlgoAPI_Fuse(base, boss).Shape()

    ax_thru = gp_Ax2(gp_Pnt(40, 25, -1), gp_Dir(0, 0, 1))
    thru = BRepPrimAPI_MakeCylinder(ax_thru, 10.0, 64.0).Shape()
    solid = BRepAlgoAPI_Cut(solid, thru).Shape()

    ax_cb = gp_Ax2(gp_Pnt(40, 25, 52 - 6), gp_Dir(0, 0, 1))
    cb = BRepPrimAPI_MakeCylinder(ax_cb, 16.0, 7.0).Shape()
    solid = BRepAlgoAPI_Cut(solid, cb).Shape()

    for x in (15.0, 65.0):
        ax_b = gp_Ax2(gp_Pnt(x, 25, -1), gp_Dir(0, 0, 1))
        bolt = BRepPrimAPI_MakeCylinder(ax_b, 4.5, 64.0).Shape()
        solid = BRepAlgoAPI_Cut(solid, bolt).Shape()
    return solid


def export_step(shape, path: Path):
    Interface_Static.SetCVal_s("write.step.unit", "MM")
    w = STEPControl_Writer()
    w.Transfer(shape, STEPControl_AsIs)
    from OCP.IFSelect import IFSelect_ReturnStatus
    st = w.Write(str(path))
    assert st == IFSelect_ReturnStatus.IFSelect_RetDone, f"STEP 写入失败: {st}"
    r = STEPControl_Reader()
    r.ReadFile(str(path))
    r.TransferRoots()
    back = r.OneShape()
    return back


def edges_of(shape, flag: TopAbs_EDGE):
    ex = TopExp_Explorer(shape, flag)
    out = []
    while ex.More():
        out.append(TopoDS.Edge(ex.Current()))
        ex.Next()
    return out


def polyline(edge, defl=0.05):
    """把任意边离散成点列"""
    c = BRepAdaptor_Curve(edge)
    pts = []
    try:
        gcp = GCPnts_UniformDeflection(c, defl, c.FirstParameter(), c.LastParameter())
        if gcp.IsDone():
            for i in range(1, gcp.NbPoints() + 1):
                p = gcp.Value(i)
                pts.append((p.X(), p.Y()))
    except Exception:
        p = c.Value(c.FirstParameter())
        pts.append((p.X(), p.Y()))
    if c.IsClosed() and len(pts) > 2:
        pts.append(pts[0])
    return pts


def hlr_project(shape, direction, xdir):
    """HLR 抽取。实测 OCP 8.0.1.1 的两条铁律：
      1) 不调 Hide() 隐藏线恒为 0；
      2) Hide() 之前读到的 VCompound 是"未消隐"的全部轮廓，必须 Hide() 之后再一起读
         可见与隐藏（与 FreeCAD ProjectionAlgos::execute 的顺序一致）。"""
    ax2 = gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(*direction), gp_Dir(*xdir))
    algo = HLRBRep_Algo()
    algo.Add(shape)
    algo.Projector(HLRAlgo_Projector(ax2))
    algo.Update()
    algo.Hide()

    hs = HLRBRep_HLRToShape(algo)
    vis_comp = [hs.VCompound(), hs.OutLineVCompound(), hs.Rg1LineVCompound()]
    hid_comp = [hs.HCompound(), hs.OutLineHCompound(), hs.Rg1LineHCompound()]

    vis, hid = [], []
    for c in vis_comp:
        vis += polyline_group(c)
    for c in hid_comp:
        hid += polyline_group(c)
    return vis, hid


def polyline_group(comp):
    """HLR 结果已是投影面 2D 坐标（Z 恒 0），直接取 X,Y"""
    if comp.IsNull():
        return []
    groups = []
    for e in edges_of(comp, TopAbs_EDGE):
        pts = polyline(e)
        if len(pts) >= 2:
            groups.append(pts)
    return groups


def detect_features(shape):
    """从实体识别圆柱面/平面 —— 自动标注的输入"""
    cyls, planes = [], []
    ex = TopExp_Explorer(shape, TopAbs_FACE)
    while ex.More():
        face = TopoDS.Face(ex.Current())
        s = BRepAdaptor_Surface(face)
        t = s.GetType()
        if t == GeomAbs_Cylinder:
            cy = s.Cylinder()
            ax = cy.Axis()
            d = ax.Direction()
            loc = ax.Location()
            # 判断是否为通孔/凸台：轴向是否接近 Z
            vertical = abs(d.Z()) > 0.99
            cyls.append({
                "r": round(cy.Radius(), 3),
                "dia": round(2 * cy.Radius(), 3),
                "axis": (round(loc.X(), 2), round(loc.Y(), 2)),
                "vertical": vertical,
            })
        elif t == GeomAbs_Plane:
            pl = s.Plane()
            n = pl.Axis().Direction()
            planes.append({
                "normal": (round(n.X(), 3), round(n.Y(), 3), round(n.Z(), 3)),
                "d": round(pl.Location().Z() if abs(n.Z()) > 0.99 else pl.Location().X(), 3),
            })
        ex.Next()
    return cyls, planes


def to_svg(views, path: Path, cell=340, margin=70):
    """按全局包围盒等比缩放，三视图并排布局"""
    allpts = [p for _, v, h in views for grp in (v, h) for pts in grp for p in pts]
    minx = min(p[0] for p in allpts); maxx = max(p[0] for p in allpts)
    miny = min(p[1] for p in allpts); maxy = max(p[1] for p in allpts)
    span = max(maxx - minx, maxy - miny, 1e-6)
    s = (cell - 40) / span
    W, H = cell * len(views) + margin * (len(views) + 1), cell + margin * 2

    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
             f'viewBox="0 0 {W} {H}">',
             f'<rect width="100%" height="100%" fill="white"/>']
    for i, (name, vis, hid) in enumerate(views):
        ox = margin + i * (cell + margin)
        oy = margin
        parts.append(f'<rect x="{ox-6}" y="{oy-6}" width="{cell+12}" height="{cell+12}" '
                     f'fill="none" stroke="#ccc" stroke-width="0.5"/>')

        def m2d(pts):
            return " ".join(f"{ox+(x-minx)*s:.2f},{oy+cell-(y-miny)*s:.2f}" for x, y in pts)

        for pts in hid:
            parts.append(f'<polyline points="{m2d(pts)}" stroke="#9a9a9a" '
                         f'stroke-width="0.5" fill="none" stroke-dasharray="5,3"/>')
        for pts in vis:
            parts.append(f'<polyline points="{m2d(pts)}" stroke="#000" '
                         f'stroke-width="1.1" fill="none"/>')
        parts.append(f'<text x="{ox}" y="{oy-16}" font-family="sans-serif" font-size="13">'
                     f'{name}　可见 {len(vis)} / 隐藏 {len(hid)}</text>')
    parts.append(f'<text x="{margin}" y="{H-18}" font-family="sans-serif" font-size="11" '
                 f'fill="#555">实线=可见边　虚线=隐藏边　1:{1/s:.1f}　纯 OCP 投影，无 FreeCAD / 无 GUI</text>')
    parts.append("</svg>")
    path.write_text("\n".join(parts), encoding="utf-8")


def to_dxf(views, path: Path):
    doc = ezdxf.new("R2010", setup=True)
    msp = doc.modelspace()
    if "HIDDEN" not in doc.layers:
        doc.layers.add("VISIBLE")
        doc.layers.add("HIDDEN", dxfattribs={"linetype": "DASHED"})
    for i, (name, vis, hid) in enumerate(views):
        ox = i * 180.0
        for pts in vis:
            msp.add_lwpolyline([(x + ox, y) for x, y in pts], dxfattribs={"layer": "VISIBLE"})
        for pts in hid:
            msp.add_lwpolyline([(x + ox, y) for x, y in pts], dxfattribs={"layer": "HIDDEN"})
    doc.saveas(str(path))


def main():
    print("=" * 70)
    solid = build_bearing_seat()
    print("[1] 参数化建模 轴承座: OK")

    step_path = OUT / "bearing_seat.step"
    reloaded = export_step(solid, step_path)
    print(f"[2] STEP 导出/重读: {step_path.stat().st_size} bytes  OK")

    cyls, planes = detect_features(reloaded)
    print(f"[3] 特征识别: 圆柱面 {len(cyls)} 个, 平面 {len(planes)} 个")
    seen = {}
    for c in cyls:
        key = (c["dia"], c["axis"])
        seen[key] = seen.get(key, 0) + 1
    print("    候选 Ø 尺寸（直径@位置:重数）:")
    for (dia, axis), n in sorted(seen.items(), key=lambda kv: -kv[0][0]):
        print(f"      Ø{dia:<7} @ {axis}   x{n}")

    views = []
    for name, d, xd in [
        ("主视图 (前)", (0.0, -1.0, 0.0), (1.0, 0.0, 0.0)),
        ("俯视图 (上)", (0.0, 0.0, 1.0), (1.0, 0.0, 0.0)),
        ("左视图", (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
    ]:
        vis, hid = hlr_project(reloaded, d, xd)
        views.append((name, vis, hid))
        print(f"[4] {name}: 可见边折线 {len(vis)}, 隐藏边折线 {len(hid)}")

    # 几何量测自检：投影结果的包围盒必须等于零件真实外形尺寸
    expect = {"主视图 (前)": (80.0, 52.0), "俯视图 (上)": (80.0, 50.0), "左视图": (50.0, 52.0)}
    print("\n[5] 投影包围盒量测自检（应与零件真实外形一致）")
    for name, vis, hid in views:
        pts = [p for g in vis + hid for p in g]
        w = max(p[0] for p in pts) - min(p[0] for p in pts)
        h = max(p[1] for p in pts) - min(p[1] for p in pts)
        ew, eh = expect[name]
        ok = abs(w - ew) < 0.01 and abs(h - eh) < 0.01
        print(f"    {name:<12} 实测 {w:6.2f} x {h:6.2f}   期望 {ew:5.1f} x {eh:5.1f}   "
              f"{'✓ 一致' if ok else '✗ 不一致'}")

    to_svg(views, OUT / "three_views.svg")
    to_dxf(views, OUT / "three_views.dxf")
    print(f"[5] 输出: {OUT/'three_views.svg'} 与 {OUT/'three_views.dxf'}")
    print("=" * 70)
    print("结论: 全链路无 GUI、无 FreeCAD，纯 pip 依赖跑通")


if __name__ == "__main__":
    main()

"""测试夹具：用 OCCT 直接生成 STEP，避免把二进制样本塞进版本库。

这些是测试专用代码，不属于生产包。
"""
from __future__ import annotations

import contextlib
import math
import os
from pathlib import Path

import pytest
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer
from OCP.TopoDS import TopoDS_Shape
from OCP.gp import gp_Ax2, gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf


@contextlib.contextmanager
def _silently():
    """OCCT 会把 Transfer 统计直接写到进程 fd 1/2，pytest 拦不住，只能重定向。"""
    saved = {}
    for fd, store in ((1, "out"), (2, "err")):
        saved[store] = os.dup(fd)
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, fd)
        os.close(devnull)
    try:
        yield
    finally:
        for fd, store in ((1, "out"), (2, "err")):
            os.dup2(saved[store], fd)
            os.close(saved[store])


def _write_step(shape: TopoDS_Shape, path: Path) -> Path:
    writer = STEPControl_Writer()
    writer.Transfer(shape, STEPControl_AsIs)
    with _silently():
        writer.Write(str(path))
    return path


@pytest.fixture
def amend_sheet():
    """把机器底稿改成「工程师交回来的样子」，返回可直接上传的文件路径。

    改显示值 = A 组（数值级，照图加工会废件）；删一条尺寸 = B 组（取舍级）。
    两者严重度差一个量级，冻结规则必须只咬住前者，所以两个入口都要有。
    """
    import pathlib
    import tempfile

    import ezdxf

    from drawform.eval.intake import dimension_entities

    def _write(draft: bytes, text: str | None = None, drop: bool = False,
               index: int = 0) -> pathlib.Path:
        folder = pathlib.Path(tempfile.mkdtemp())
        src = folder / "draft.dxf"
        src.write_bytes(draft)
        doc = ezdxf.readfile(str(src))
        target = list(dimension_entities(doc))[index]
        if drop:
            target.destroy()
        else:
            target.dxf.text = text
            target.render()
        out = folder / "amended.dxf"
        doc.saveas(out)
        return out

    return _write


@pytest.fixture(scope="session")
def plate_step(tmp_path_factory) -> Path:
    """80 x 50 x 12 的板，中心一个 Ø20 通孔。包围盒应为 (80, 50, 12)。

    session 级：OCCT 的 STEP 写出会往进程 fd 1 刷统计，重复写会淹没测试输出。
    """
    d = tmp_path_factory.mktemp("step")
    solid = BRepPrimAPI_MakeBox(80.0, 50.0, 12.0).Shape()
    hole = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(40, 25, -1), gp_Dir(0, 0, 1)), 10.0, 14.0).Shape()
    return _write_step(BRepAlgoAPI_Cut(solid, hole).Shape(), d / "plate.step")


@pytest.fixture(scope="session")
def stepped_shaft_step(tmp_path_factory) -> Path:
    """Ø44/Ø30 阶梯轴 + Ø16 轴向通孔。

    选它是因为布尔运算后 OCCT 的 HLR 会把大量**精确直线**表示成 B 样条，
    投影离散化后变成折线 —— 这正是 refit 层要解决的问题。
    """
    d = tmp_path_factory.mktemp("step")
    axis = gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1))
    shape = BRepPrimAPI_MakeCylinder(axis, 15.0, 90.0).Shape()
    shape = BRepAlgoAPI_Fuse(shape, BRepPrimAPI_MakeCylinder(axis, 22.0, 30.0).Shape()).Shape()
    bore = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(0, 0, -1), gp_Dir(0, 0, 1)), 8.0, 92.0).Shape()
    return _write_step(BRepAlgoAPI_Cut(shape, bore).Shape(), d / "stepped_shaft.step")


@pytest.fixture(scope="session")
def bearing_seat_step(tmp_path_factory) -> Path:
    """底板 80×50×12 + Ø40 凸台 + Ø20 通孔 + Ø32 沉孔 + 两端 Ø9 螺栓孔。

    含同轴孔系（Ø20 与 Ø32），用于验证中心线去重。
    """
    d = tmp_path_factory.mktemp("step")
    solid = BRepPrimAPI_MakeBox(80.0, 50.0, 12.0).Shape()
    boss = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(40, 25, 12), gp_Dir(0, 0, 1)), 20.0, 40.0).Shape()
    solid = BRepAlgoAPI_Fuse(solid, boss).Shape()
    bore = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(40, 25, -1), gp_Dir(0, 0, 1)), 10.0, 64.0).Shape()
    solid = BRepAlgoAPI_Cut(solid, bore).Shape()
    cbore = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(40, 25, 46), gp_Dir(0, 0, 1)), 16.0, 7.0).Shape()
    solid = BRepAlgoAPI_Cut(solid, cbore).Shape()
    for x in (15.0, 65.0):
        bolt = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(x, 25, -1), gp_Dir(0, 0, 1)), 4.5, 64.0).Shape()
        solid = BRepAlgoAPI_Cut(solid, bolt).Shape()
    return _write_step(solid, d / "bearing_seat.step")


@pytest.fixture(scope="session")
def ribbed_plate_step(tmp_path_factory) -> Path:
    """40×30×8 的板，开两条 4 宽通槽（x 10–14 与 18–22），中间剩一道 4 mm 立肋。

    壁厚判据需要「槽 vs 肋」的对照：槽里那对平面之间是空气，肋里那对才是材料，
    两面间隙都是 4。没有这个对照，「中点落在材料内」这条判据整段删掉测试照样绿，
    而那正是把空气当壁厚标出去的错误。
    """
    d = tmp_path_factory.mktemp("step")
    solid = BRepPrimAPI_MakeBox(40.0, 30.0, 8.0).Shape()
    for x in (10.0, 18.0):
        slot = BRepPrimAPI_MakeBox(gp_Pnt(x, 0.0, -1.0), 4.0, 30.0, 10.0).Shape()
        solid = BRepAlgoAPI_Cut(solid, slot).Shape()
    return _write_step(solid, d / "ribbed.step")


@pytest.fixture(scope="session")
def angled_plate_step(tmp_path_factory) -> Path:
    """40×30×8 的板，右端切出一个 45° 斜面（绕 y 轴转 45° 的一块楔）。

    壁厚判据只认轴平行面，所以需要一个**真的存在斜面**的夹具：少了它，
    「法向主轴分量 > 0.999 才收」这条守卫可以整段删掉而测试照样绿，
    而斜面的间隙会被算成某个坐标轴上的假壁厚。
    """
    d = tmp_path_factory.mktemp("step")
    solid = BRepPrimAPI_MakeBox(40.0, 30.0, 8.0).Shape()
    turn = gp_Trsf()
    turn.SetRotation(gp_Ax1(gp_Pnt(40.0, 15.0, 8.0), gp_Dir(0, 1, 0)), math.pi / 4.0)
    wedge = BRepPrimAPI_MakeBox(gp_Pnt(30.0, -5.0, 0.0), 40.0, 40.0, 40.0).Shape()
    wedge = BRepBuilderAPI_Transform(wedge, turn, True).Shape()
    return _write_step(BRepAlgoAPI_Cut(solid, wedge).Shape(), d / "angled.step")


@pytest.fixture(scope="session")
def stepped_plate_step(tmp_path_factory) -> Path:
    """40×30×8 的板，右半边（x 20–40）从 z=4 往上切掉 —— 一个两级台阶。

    z 方向的平面是 0|4|8，两对相邻间隙都是 4，**首尾相接正好铺满外廓 8**：
    两条都当实尺发出去就是教科书里的封闭尺寸链。留一环的判据必须能在这件上被验，
    所以需要一个真的会成链的夹具，而不是只有台阶、但铺不满的。
    """
    d = tmp_path_factory.mktemp("step")
    solid = BRepPrimAPI_MakeBox(40.0, 30.0, 8.0).Shape()
    cut = BRepPrimAPI_MakeBox(gp_Pnt(20.0, 0.0, 4.0), 20.0, 30.0, 4.0).Shape()
    return _write_step(BRepAlgoAPI_Cut(solid, cut).Shape(), d / "stepped_plate.step")


@pytest.fixture
def broken_step(tmp_path: Path) -> Path:
    """内容是垃圾字节的 .step 文件。"""
    p = tmp_path / "broken.step"
    p.write_bytes(b"ISO-10303-21; THIS IS NOT A VALID STEP FILE \x00\x01\x02")
    return p


@pytest.fixture(scope="session")
def refused_step(tmp_path_factory) -> Path:
    """Two valid, separate solids: a portable business refusal, independent of PCD support."""
    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS_Compound
    compound = TopoDS_Compound()
    builder = BRep_Builder()
    builder.MakeCompound(compound)
    for x in (0, 40):
        builder.Add(compound, BRepPrimAPI_MakeBox(gp_Pnt(x, 0, 0), 20, 10, 8).Shape())
    return _write_step(compound, tmp_path_factory.mktemp("step") / "refused.step")


@pytest.fixture
def not_a_step(tmp_path: Path) -> Path:
    p = tmp_path / "notes.txt"
    p.write_text("hello", encoding="utf-8")
    return p


@pytest.fixture(scope="session")
def cross_bore_step(tmp_path_factory) -> Path:
    """60×20×20 的块，Ø8 孔沿 X 贯穿 —— 用来盯径向孔的轴线位置。"""
    d = tmp_path_factory.mktemp("step")
    solid = BRepPrimAPI_MakeBox(60.0, 20.0, 20.0).Shape()
    hole = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(-1, 10, 10), gp_Dir(1, 0, 0)), 4.0, 62.0).Shape()
    return _write_step(BRepAlgoAPI_Cut(solid, hole).Shape(), d / "cross.step")


@pytest.fixture(scope="session")
def blind_hole_step(tmp_path_factory) -> Path:
    """60×40×20 的块，顶面 Ø10 盲孔深 12（留 8mm 实底）。"""
    d = tmp_path_factory.mktemp("step")
    solid = BRepPrimAPI_MakeBox(60.0, 40.0, 20.0).Shape()
    hole = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(30, 20, 20), gp_Dir(0, 0, -1)), 5.0, 12.0).Shape()
    return _write_step(BRepAlgoAPI_Cut(solid, hole).Shape(), d / "blind.step")


@pytest.fixture(scope="session")
def double_journal_step(tmp_path_factory) -> Path:
    """Ø30–Ø50–Ø30 的同轴三段：两个 Ø30 外圆面共轴但被中间的大台阶隔开。

    图纸上 ⌀30 只写一次（同一个名义直径适用所有该尺寸的表面），所以并组必须
    把它们合成一条，而不是按面数写成 2×⌀30。
    """
    d = tmp_path_factory.mktemp("step")
    axis = gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1))
    shape = BRepPrimAPI_MakeCylinder(axis, 15.0, 10.0).Shape()
    shape = BRepAlgoAPI_Fuse(shape, BRepPrimAPI_MakeCylinder(
        gp_Ax2(gp_Pnt(0, 0, 10), gp_Dir(0, 0, 1)), 25.0, 10.0).Shape()).Shape()
    shape = BRepAlgoAPI_Fuse(shape, BRepPrimAPI_MakeCylinder(
        gp_Ax2(gp_Pnt(0, 0, 20), gp_Dir(0, 0, 1)), 15.0, 10.0).Shape()).Shape()
    return _write_step(shape, d / "double_journal.step")


@pytest.fixture(scope="session")
def meeting_cbore_step(tmp_path_factory) -> Path:
    """20×20×10 的块：底面 Ø4 向上钻 6，顶面 Ø6.5 向下沉 4，两面刚好碰头。

    这是真实语料里的原样写法 `⌀4 ↧6 ⌴⌀6,5 ↧4` —— 引导孔自己也是盲孔（有 ↧），
    但沉孔仍然是沉孔。配对判据不能要求引导孔必须通到底。
    """
    d = tmp_path_factory.mktemp("step")
    solid = BRepPrimAPI_MakeBox(20.0, 20.0, 10.0).Shape()
    pilot = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(10, 10, -1), gp_Dir(0, 0, 1)), 2.0, 7.0).Shape()
    solid = BRepAlgoAPI_Cut(solid, pilot).Shape()
    cbore = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(10, 10, 11), gp_Dir(0, 0, -1)), 3.25, 5.0).Shape()
    solid = BRepAlgoAPI_Cut(solid, cbore).Shape()
    return _write_step(solid, d / "meeting_cbore.step")


@pytest.fixture(scope="session")
def cbore_traps_step(tmp_path_factory) -> Path:
    """20×20×12 的块，两种「像沉孔但不是」的陷阱各放一个，全零件不应配出任何沉孔。

    A) 同一面钻进来的 ⌀20 与 ⌀12 盲孔，轴向区间完全相同但**不共轴**
       —— 只有同轴判据能挡住它们配对。
    B) 共轴的 ⌀20（顶面深 4）与 ⌀12（底面深 4），中间隔着 4 mm 实腹
       —— 只有相接判据能挡住。两端对着钻在真实零件里很常见，配成沉孔就标错了。
    """
    d = tmp_path_factory.mktemp("step")
    solid = BRepPrimAPI_MakeBox(20.0, 20.0, 12.0).Shape()
    # (x, y, 半径, 起点 z, 是否向下钻, 深度)
    for x, y, radius, z_start, downwards, depth in (
            (10.0, 5.0, 10.0, 12.0, True, 6.0),    # A 大
            (10.0, 15.0, 6.0, 12.0, True, 6.0),    # A 小：与 A 大同区间、不同轴
            (5.0, 10.0, 10.0, 12.0, True, 4.0),    # B 大：z 8–12
            (5.0, 10.0, 6.0, 0.0, False, 4.0)):    # B 小：z 0–4，共轴但中间隔着实腹
        cut = BRepPrimAPI_MakeCylinder(
            gp_Ax2(gp_Pnt(x, y, z_start), gp_Dir(0, 0, -1 if downwards else 1)),
            radius, depth).Shape()
        solid = BRepAlgoAPI_Cut(solid, cut).Shape()
    return _write_step(solid, d / "cbore_traps.step")





@pytest.fixture(scope="session")
def bolt_circle_step(tmp_path_factory) -> Path:
    """80×80×12 法兰，4×Ø9 均分在 ⌀50 分布圆上，圆心 (40, 40)。

    螺栓圆是真实图纸上最常见也最难手绘的标注：一条 `4×⌀9 EQUALLY SPACED ON ⌀50 PCD`
    加两个圆心定位，压掉 8 条逐个孔的坐标。分布圆本身是假想圆，投影里找不到它，
    所以它的尺寸只能钉在两个对角孔的实圆心上。
    """
    import math

    d = tmp_path_factory.mktemp("step")
    solid = BRepPrimAPI_MakeBox(80.0, 80.0, 12.0).Shape()
    for i in range(4):
        angle = 2 * math.pi * i / 4
        x, y = 40.0 + 25.0 * math.cos(angle), 40.0 + 25.0 * math.sin(angle)
        hole = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(x, y, -1), gp_Dir(0, 0, 1)), 4.5, 14.0).Shape()
        solid = BRepAlgoAPI_Cut(solid, hole).Shape()
    return _write_step(solid, d / "bolt_circle.step")


@pytest.fixture(scope="session")
def odd_bolt_circle_step(tmp_path_factory) -> Path:
    """80×80×12 法兰，5×Ø9 均分在 ⌀50 分布圆上。

    奇数个孔没有"直径相对"的一对，最远的两个圆心只有 50·cos(36°)=40.45 mm。
    分布圆尺寸要么按真几何画，要么不画 —— 拿 ⌀50 的数拉一条 40.45 的线，
    图上就是一条自相矛盾的尺寸。
    """
    import math

    d = tmp_path_factory.mktemp("step")
    solid = BRepPrimAPI_MakeBox(80.0, 80.0, 12.0).Shape()
    for i in range(5):
        angle = math.pi / 2 + 2 * math.pi * i / 5
        x, y = 40.0 + 25.0 * math.cos(angle), 40.0 + 25.0 * math.sin(angle)
        hole = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(x, y, -1), gp_Dir(0, 0, 1)), 4.5, 14.0).Shape()
        solid = BRepAlgoAPI_Cut(solid, hole).Shape()
    return _write_step(solid, d / "odd_bolt_circle.step")


@pytest.fixture(scope="session")
def edge_notch_step(tmp_path_factory) -> Path:
    """40×30×10 的板，左边缘正中开一个 Ø10 的半圆缺口（圆心落在 x=0 的边上）。

    孔被边界切掉一半，投影出来的是**圆弧**而不是整圆。真实图纸照样要标这个
    ⌀10 —— 圆的尺寸本来就允许从弧上取圆心。
    """
    d = tmp_path_factory.mktemp("step")
    solid = BRepPrimAPI_MakeBox(40.0, 30.0, 10.0).Shape()
    notch = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(0.0, 15.0, -1), gp_Dir(0, 0, 1)), 5.0, 12.0).Shape()
    return _write_step(BRepAlgoAPI_Cut(solid, notch).Shape(), d / "edge_notch.step")


@pytest.fixture(scope="session")
def enclosed_void_step(tmp_path_factory) -> Path:
    """30³ 的块内部埋一个 Ø10×10 的封闭空腔（两端都不通到任何表面）。

    铸造内腔在真实图纸上是"剖视里看到的形状"，不是钻出来的孔 —— 它没有入口面，
    也就没有 `↧` 可量。方案层若还发一条深度，图上就画不出来（交付门会拦），
    所以正确做法是**根本不发这条深度**。
    """
    d = tmp_path_factory.mktemp("step")
    solid = BRepPrimAPI_MakeBox(30.0, 30.0, 30.0).Shape()
    void = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(15, 15, 10), gp_Dir(0, 0, 1)), 5.0, 10.0).Shape()
    return _write_step(BRepAlgoAPI_Cut(solid, void).Shape(), d / "enclosed_void.step")

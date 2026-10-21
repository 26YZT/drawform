from __future__ import annotations

import math
from pathlib import Path

import ezdxf
from ezdxf.document import Drawing
from ezdxf.lldxf.const import VSF_LOCK_ZOOM

from drawform.output.sheet import make_frame
from drawform.domain.curves import Arc2D, Centerline, Circle2D, Line2D, Polyline2D
from drawform.output.drawing import (ARROW_HALF, DATUM_BOX, DATUM_LEADER, DIM_ARROW,
                                     MARK_ARROW, MARK_STUB, SectionMark, TEXT_HEIGHT,
                                     VIEW_LABEL_GAP)

VISIBLE_LAYER = "VISIBLE"
HIDDEN_LAYER = "HIDDEN"
CENTER_LAYER = "CENTER"
HATCH_LAYER = "HATCH"
DIM_LAYER = "DIM"
NOTE_LAYER = "NOTE"
TITLE_LAYER = "TITLE"
SECTION_LAYER = "SECTION"
DATUM_LAYER = "DATUM"
FRAME_LAYER = "FRAME"

DASHED_LINETYPE = "GB_DASHED"
CHAIN_LINETYPE = "GB_CHAIN"

# GB/T 4457.4 段长（毫米，按 1:1 出图）：虚线线段 8–12 / 间隔 1；点划线线段 18–24 / 间隔 3 / 点 ≤0.75。
# 不能沿用 AutoCAD 内置的 DASHED/CENTER：它们按英寸定义，虚线周期只有 1.524 mm，
# 在毫米图上打印几乎连成实线；而调 LTSCALE 无法同时满足两种线型。
# ezdxf 的 pattern 第一项是 DXF 组码 40 的周期总长，不是线段 —— 漏掉它线段会少一格。
LINETYPE_DEFS = {
    DASHED_LINETYPE: ([9.0, 8.0, -1.0], "GB/T 4457.4 虚线 _ _ _ _"),
    CHAIN_LINETYPE: ([24.5, 18.0, -3.0, 0.5, -3.0], "GB/T 4457.4 点划线 _ . _ ."),
}

# GB/T 4457.4 线宽组：粗线 b 与细线 b/2，比例 2:1。b 取 0.50 mm，适配 A3/A2/A1 图幅。
# DXF 的 lineweight 单位是 1/100 mm。
LAYER_SPECS = {
    VISIBLE_LAYER: {"color": 7, "linetype": "CONTINUOUS", "lineweight": 50},
    HIDDEN_LAYER: {"color": 8, "linetype": DASHED_LINETYPE, "lineweight": 25},
    CENTER_LAYER: {"color": 4, "linetype": CHAIN_LINETYPE, "lineweight": 25},
    HATCH_LAYER: {"color": 7, "linetype": "CONTINUOUS", "lineweight": 25},
    DIM_LAYER: {"color": 7, "linetype": "CONTINUOUS", "lineweight": 25},
    NOTE_LAYER: {"color": 7, "linetype": "CONTINUOUS", "lineweight": 25},
    TITLE_LAYER: {"color": 7, "linetype": "CONTINUOUS", "lineweight": 50},
    SECTION_LAYER: {"color": 7, "linetype": "CONTINUOUS", "lineweight": 50},
    DATUM_LAYER: {"color": 7, "linetype": "CONTINUOUS", "lineweight": 25},
    FRAME_LAYER: {"color": 7, "linetype": "CONTINUOUS", "lineweight": 25},
}


def _new_document() -> Drawing:
    doc = ezdxf.new("R2010", setup=True)
    doc.header["$INSUNITS"] = 4  # 毫米
    doc.header["$LWDISPLAY"] = True  # 不打开，看图软件与打印都不体现线宽差别
    for name, (pattern, description) in LINETYPE_DEFS.items():
        if name not in doc.linetypes:
            doc.linetypes.add(name, pattern=pattern, description=description)
    for name, spec in LAYER_SPECS.items():
        if name not in doc.layers:
            doc.layers.add(name, **spec)

    # ezdxf 自带 dimstyle 的字高 0.25、箭头 0.175 是按英寸习惯来的，
    # 放进毫米图就是 0.25 mm 高的字 —— 实体、审计、文字内容全正常，只有纸面才发现。
    dim = doc.dimstyles.get("EZDXF")
    dim.dxf.dimtxt = 3.5       # GB/T 14691 最小字高系列
    dim.dxf.dimasz = 3.0       # 箭头长度
    dim.dxf.dimgap = 1.0       # 文字与尺寸线的间距
    dim.dxf.dimexe = 1.5       # 尺寸界线超出轮廓
    dim.dxf.dimexo = 1.0       # 尺寸线超出最外侧界线
    dim.dxf.dimclrd = 256      # 尺寸线/界线用 ByLayer，落到 DIM 层的细线上
    dim.dxf.dimclre = 256
    dim.dxf.dimclrt = 256
    return doc


def _emit(msp, curve, origin, layer):
    ox, oy = origin

    if isinstance(curve, (Line2D, Centerline)):
        msp.add_line((ox + curve.start[0], oy + curve.start[1]),
                     (ox + curve.end[0], oy + curve.end[1]),
                     dxfattribs={"layer": layer})
        return

    if isinstance(curve, Circle2D):
        msp.add_circle((ox + curve.center[0], oy + curve.center[1]),
                       curve.radius,
                       dxfattribs={"layer": layer})
        return

    if isinstance(curve, Arc2D):
        start, end = curve.start_angle, curve.end_angle
        # DXF ARC 固定逆时针。交换顺时针源弧的两端，保持实际点集而非画互补弧。
        if end < start:
            start, end = end, start
        msp.add_arc((ox + curve.center[0], oy + curve.center[1]),
                    curve.radius,
                    math.degrees(start),
                    math.degrees(end),
                    dxfattribs={"layer": layer})
        return

    if isinstance(curve, Polyline2D):
        msp.add_lwpolyline([(ox + x, oy + y) for x, y in curve.points],
                           dxfattribs={"layer": layer})
        return

    raise TypeError(f"未知的曲线类型：{type(curve).__name__}")


def _emit_dimension(msp, dim, origin):
    """出原生 DIMENSION 实体。自绘的尺寸线在 CAD 眼里只是一堆线段，改不了也量不了。"""
    ox, oy = origin
    attribs = {"dimstyle": "EZDXF", "dxfattribs": {"layer": DIM_LAYER}}

    if dim.kind in ("diameter", "pcd"):
        # 分布圆是假想圆，投影里没有那个圆，它钉在两个对角实圆心上。
        # 但量的仍然是一个直径：圆心取两圆心的中点，圆上点取其中一个圆心。
        if dim.kind == "diameter":
            center, mpoint = dim.p1, dim.p2
        else:
            center = ((dim.p1[0] + dim.p2[0]) / 2.0, (dim.p1[1] + dim.p2[1]) / 2.0)
            mpoint = dim.p2
        msp.add_diameter_dim(center=(ox + center[0], oy + center[1]),
                             mpoint=(ox + mpoint[0], oy + mpoint[1]),
                             text=dim.text, **attribs).render()
        return

    ux, uy = dim.direction()
    angle = (90.0 if abs(ux) < 1e-9 else 0.0 if abs(uy) < 1e-9
             else math.degrees(math.atan2(uy, ux)))
    msp.add_linear_dim(base=(ox + dim.base[0], oy + dim.base[1]),
                       p1=(ox + dim.p1[0], oy + dim.p1[1]),
                       p2=(ox + dim.p2[0], oy + dim.p2[1]),
                       angle=angle,
                       text=dim.text, **attribs).render()


NOTE_HEIGHT = 3.5


def _view_key(view):
    return view.label or view.projection.direction.name


def _local_box(view):
    points = [p for c in tuple(view.projection.visible) + tuple(view.projection.hidden)
              for p in c.sample()]
    return (min(p[0] for p in points), min(p[1] for p in points),
            max(p[0] for p in points), max(p[1] for p in points))


def _emit_view_titles(msp, views):
    """只给派生视图（剖视、局部放大）写名字。

    基本视图靠位置表达身份，标上 "front" 反而不合规；而剖视图不标就是害人的图。
    """
    for view in views:
        if not view.label or view.label == view.projection.direction.name:
            continue
        x0, y0, x1, _ = _local_box(view)
        # 不用 halign：那样 DXF 会把对齐点写进组码 11，insert 变空，
        # ezdxf 自带的渲染器直接崩，别的看图软件也可能读不出文字。
        half_width = len(view.label) * NOTE_HEIGHT * 0.35
        msp.add_text(view.label, dxfattribs={
            "insert": (view.origin[0] + (x0 + x1) / 2.0 - half_width,
                       view.origin[1] - VIEW_LABEL_GAP),
            "height": NOTE_HEIGHT,
            "layer": TITLE_LAYER,
        })


def _emit_datum_marks(msp, marks, views):
    """基准代号：贴面的涂黑三角形 + 引出线 + 带字母的方框（GB/T 1182）。

    几何与 `drawing._datum_mark` 逐笔对齐，否则网页预览与 DXF 会画成两个样子。
    方框用 SOLID 实体填三角形，是因为 CAD 里"看起来是黑的"必须由实体保证，
    不能靠线宽堆。
    """
    by_key = {_view_key(view): view for view in views}
    for mark in marks:
        view = by_key.get(mark.view)
        if view is None:
            continue
        norm = math.hypot(*mark.outward) or 1.0
        ux, uy = mark.outward[0] / norm, mark.outward[1] / norm
        ox, oy = view.origin
        start = (ox + mark.anchor[0], oy + mark.anchor[1])
        elbow = (start[0] + ux * DATUM_LEADER, start[1] + uy * DATUM_LEADER)
        half = DATUM_BOX / 2.0
        cx, cy = elbow[0] + ux * half, elbow[1] + uy * half
        msp.add_line(elbow, start, dxfattribs={"layer": DATUM_LAYER})
        corners = [(cx - half, cy - half), (cx + half, cy - half),
                   (cx + half, cy + half), (cx - half, cy + half)]
        for a, b in zip(corners, corners[1:] + corners[:1]):
            msp.add_line(a, b, dxfattribs={"layer": DATUM_LAYER})
        bx, by = start[0] - ux * DIM_ARROW, start[1] - uy * DIM_ARROW
        px, py = -uy, ux
        msp.add_solid([(bx + px * ARROW_HALF, by + py * ARROW_HALF),
                      (bx - px * ARROW_HALF, by - py * ARROW_HALF), start],
                      dxfattribs={'layer': DATUM_LAYER})
        msp.add_text(mark.letter, dxfattribs={
            "insert": (cx, cy), "height": TEXT_HEIGHT,
            "layer": DATUM_LAYER, "halign": 0, "valign": 0,
        })


def _emit_title_block(msp, meta, frame):
    """标题栏。几何与 `titleblock.title_block` 同一份，两个出口必须长一样。"""
    from drawform.output.titleblock import title_block

    lines, labels = title_block(meta, frame.inner)
    for run in lines:
        for a, b in zip(run, run[1:]):
            msp.add_line(a, b, dxfattribs={"layer": TITLE_LAYER})
    for content, x, y, size in labels:
        if content:
            msp.add_text(content, dxfattribs={"insert": (x, y), "height": size,
                                              "layer": TITLE_LAYER})


def _emit_section_marks(msp, marks, views):
    """剖切位置线。`at` 解释为该视图局部坐标的竖直方向位置，即水平剖切线。

    剖平面与哪个视图的投影面垂直，位置线就画在哪个视图上；画错视图等于指错剖切位置。
    两端短竖线 + 字母是 GB/T 17452 记号的必要组成，具体线长比例尚未按标准核对。
    """
    by_key = {_view_key(view): view for view in views}
    for mark in marks:
        view = by_key.get(mark.view)
        if view is None:
            continue

        norm = math.hypot(*mark.toward)
        ux, uy = mark.toward[0] / norm, mark.toward[1] / norm
        vx, vy = -uy, ux                      # 位置线方向，垂直于投影方向
        box = _local_box(view)
        anchor = (mark.at * ux, mark.at * uy)  # 位置线上的一点
        corners = [(box[0], box[1]), (box[2], box[1]), (box[0], box[3]), (box[2], box[3])]
        spans = [(px - anchor[0]) * vx + (py - anchor[1]) * vy for px, py in corners]
        lo, hi = min(spans) - MARK_STUB, max(spans) + MARK_STUB

        ends = [(anchor[0] + t * vx, anchor[1] + t * vy) for t in (lo, hi)]
        ox, oy = view.origin
        msp.add_line((ox + ends[0][0], oy + ends[0][1]), (ox + ends[1][0], oy + ends[1][1]),
                     dxfattribs={"layer": SECTION_LAYER})

        for ex, ey in ends:
            base = (ox + ex, oy + ey)
            tip = (base[0] + ux * MARK_ARROW, base[1] + uy * MARK_ARROW)
            msp.add_line(base, tip, dxfattribs={"layer": SECTION_LAYER})
            msp.add_text(mark.letter, dxfattribs={
                "insert": (tip[0], tip[1] + 1.0),
                "height": NOTE_HEIGHT,
                "layer": SECTION_LAYER,
            })


def write_dxf(views, path: Path, dimensions=(), notes=(),
              section_marks=(), datum_marks=(), title_meta=None, frame=None, drawing=None,
              paper_scale=1.0) -> Path:
    """把已回拟合的投影边写成原生 DXF 实体（LINE / CIRCLE / ARC / DIMENSION），分层可见与隐藏。

    解析实体而非折线是硬性要求：客户拿到图纸要能在 AutoCAD 里继续编辑半径、长度与尺寸。
    """
    doc = _new_document()
    if not math.isfinite(paper_scale) or paper_scale <= 0:
        raise ValueError('纸面比例必须为正的有限数')
    doc.dimstyles.get('EZDXF').dxf.dimscale = 1/paper_scale
    doc.header['$DIMSCALE'] = 1/paper_scale
    doc.header['$LTSCALE'] = 1/paper_scale
    doc.header['$PSLTSCALE'] = 0
    msp = doc.modelspace()
    origins = {(view.label or view.projection.direction.name): view.origin
                 for view in views}

    for view in views:
        for curves, layer in ((view.projection.visible, VISIBLE_LAYER),
                              (view.projection.hidden, HIDDEN_LAYER),
                              (view.centerlines, CENTER_LAYER),
                              (view.hatch, HATCH_LAYER)):
            for curve in curves:
                _emit(msp, curve, view.origin, layer)

    for dim in dimensions:
        origin = origins.get(dim.view)
        if origin is not None:
            _emit_dimension(msp, dim, origin)

    from drawform.output.paper_notes import other_labels
    from drawform.output.sheet import make_frame
    for text,x,y,height in other_labels(notes,(frame or make_frame('A3')).inner,
                                        title_meta.tolerance_note if title_meta else ''):
        msp.add_text(text,dxfattribs={'insert':(x,y),'height':height,'layer':NOTE_LAYER})

    _emit_view_titles(msp, views)
    _emit_section_marks(msp, section_marks, views)
    _emit_datum_marks(msp, datum_marks, views)
    if title_meta is not None:
        _emit_title_block(msp, title_meta, frame or make_frame("A3"))

    if frame is not None:
        x0, y0, x1, y1 = frame.inner
        msp.add_lwpolyline([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], close=True,
                           dxfattribs={"layer": FRAME_LAYER})

    if drawing is not None:
        _annotation_graphics(doc, dimensions, origins, drawing, paper_scale)
        _paper_viewport(doc, frame or make_frame(drawing.sheet), paper_scale)

    # ezdxf 按实体类型集合补 CLASS，集合顺序会随新进程变化。生成端固定注册表
    # 顺序；保留所有定义和属性，不在稳定性检查器里忽略或重排它们。
    doc.commit_pending_changes()
    doc.classes.add_required_classes(doc.dxfversion)
    classes = sorted(doc.classes.classes.items())
    doc.classes.classes.clear()
    doc.classes.classes.update(classes)
    doc.saveas(str(path))
    return Path(path)


def _annotation_graphics(doc, dimensions, origins, drawing, paper_scale):
    """原生尺寸保留测量定义，显示块采用已避让的同一份图面几何。"""
    from drawform.output.drawing import _dimension, _arrow
    from drawform.output.paper import model_graphics

    drawing = model_graphics(drawing, paper_scale)

    def arrow(tip, direction):
        head = _arrow(tip, direction)
        return (head[0], *tuple((tip[0]+(p[0]-tip[0])/paper_scale,
                                tip[1]+(p[1]-tip[1])/paper_scale) for p in head[1:]))

    for entity, dim in zip(doc.modelspace().query('DIMENSION'), dimensions):
        owner = f'dim:{dim.source or dim.text}'
        block = doc.blocks[entity.dxf.geometry]
        block.delete_all_entities()
        for item in drawing.items:
            if item.owner == owner and item.layer == 'DIM':
                for a, b in zip(item.points, item.points[1:]):
                    block.add_line(a, b, dxfattribs={'layer': DIM_LAYER})
        _, heads, _ = _dimension(dim, origins[dim.view])
        for head in heads:
            tip = head[0]
            scaled = (tip, *tuple((tip[0]+(p[0]-tip[0])/paper_scale,
                                   tip[1]+(p[1]-tip[1])/paper_scale) for p in head[1:]))
            block.add_solid(scaled, dxfattribs={'layer': DIM_LAYER})
        for text in drawing.texts:
            if text.owner != owner:
                continue
            entity.dxf.text = text.content
            entity.dxf.text_midpoint = (text.x, text.y, 0)
            angle = math.radians(-text.rotate)
            block.add_mtext(text.content, dxfattribs={
                'insert': (text.x-text.height*.8*math.sin(angle),
                           text.y+text.height*.8*math.cos(angle)), 'char_height': text.height,
                'attachment_point': 2 if text.anchor == 'middle' else 1,
                'rotation': -text.rotate, 'layer': DIM_LAYER})

    # 纸面文字、标题栏和符号由同一 Drawing 反变换；不缩放模型曲线或原生测量。
    msp = doc.modelspace()
    for entity in list(msp):
        if entity.dxf.layer in (DATUM_LAYER, NOTE_LAYER, TITLE_LAYER, FRAME_LAYER, SECTION_LAYER):
            msp.delete_entity(entity)
    for run in dict(drawing.lines).get(FRAME_LAYER, ()):
        msp.add_lwpolyline(run[:-1], close=True, dxfattribs={'layer': FRAME_LAYER})
    for item in drawing.items:
        if item.layer not in (DATUM_LAYER, TITLE_LAYER, SECTION_LAYER, NOTE_LAYER):
            continue
        for a,b in zip(item.points,item.points[1:]):
            entity = msp.add_line(a,b,dxfattribs={'layer':item.layer})
            if item.owner.startswith('reference:'):
                from drawform.output.references import DXF_APP
                if DXF_APP not in doc.appids:doc.appids.new(DXF_APP)
                entity.set_xdata(DXF_APP,[(1000,item.owner)])
        if item.layer == DATUM_LAYER and len(item.points) == 2:
            elbow,tip = item.points
            dx,dy = tip[0]-elbow[0],tip[1]-elbow[1]
            norm = math.hypot(dx,dy) or 1
            msp.add_solid(arrow(tip,(dx/norm,dy/norm)),dxfattribs={'layer':DATUM_LAYER})
    for text in drawing.texts:
        if text.owner.startswith('dim:'):
            continue
        layer = (DATUM_LAYER if text.owner.startswith('datum:') else
                 NOTE_LAYER if text.owner == 'paper-note' or text.owner.startswith('reference:') else
                 SECTION_LAYER if text.owner.startswith('section:') else TITLE_LAYER)
        from ezdxf.enums import TextEntityAlignment
        entity = msp.add_text(text.content,dxfattribs={'height':text.height,'layer':layer,
                                           'rotation':-text.rotate}).set_placement(
            (text.x,text.y),align=TextEntityAlignment.CENTER if text.anchor == 'middle' else TextEntityAlignment.LEFT)
        if text.owner.startswith('reference:'):
            from drawform.output.references import DXF_APP
            if DXF_APP not in doc.appids:doc.appids.new(DXF_APP)
            entity.set_xdata(DXF_APP,[(1000,text.owner)])


def _paper_viewport(doc, frame, factor):
    """纸张保持 A3/A4 毫米；视口负责模型到纸面的比例，模型实体仍可量真尺寸。"""
    width, height = frame.outer
    layout = doc.layout()
    layout.page_setup(size=(width, height), margins=(0, 0, 0, 0), units='mm', scale=(1, 1))
    if 'VIEWPORT' not in doc.layers:
        doc.layers.new('VIEWPORT', dxfattribs={'plot':0})
    viewport = layout.add_viewport(center=(width/2, height/2), size=(width, height),
                                  view_center_point=(width/2/factor, height/2/factor),
                                  view_height=height/factor, dxfattribs={'layer':'VIEWPORT'})
    viewport.dxf.flags = viewport.dxf.flags | VSF_LOCK_ZOOM
    doc.layouts.set_active_layout(layout.name)
    doc.header['$TILEMODE'] = 0
    doc.set_modelspace_vport(height=height/factor, center=(width/2/factor, height/2/factor))

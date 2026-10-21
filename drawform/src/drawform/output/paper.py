"""模型空间保持真实毫米，纸面按核对过的比例系列表达。"""
from dataclasses import dataclass, replace
import math

from drawform.domain.curves import Arc2D, Centerline, Circle2D, Line2D, Polyline2D
from drawform.geometry.centerlines import OVERSHOOT
from drawform.geometry.layout import DEFAULT_GAP, layout_views
from drawform.output.drawing import Drawing, SheetOverflow, TEXT_HEIGHT, sheet_views
from drawform.output.sheet import Frame, TITLE_CLEAR, center_on_frame, content_box, make_frame
from drawform.output.titleblock import BLOCK_H


def scale_label(factor):
    return f'{factor:g}:1' if factor >= 1 else f'1:{1/factor:g}'


def _preferred(limit):
    """GB/T 14690 表1、ISO 5455 §5.1 两份正文一致的 1/2/5 × 10ⁿ 系列。"""
    if not math.isfinite(limit) or limit <= 0:
        raise ValueError('纸面比例须来自正的有限几何范围')
    power = 10.0 ** math.floor(math.log10(limit))
    return max(value*power for value in (1.0, 2.0, 5.0) if value*power <= limit*(1+1e-12))


def _box(projection):
    points = [p for curve in (*projection.visible, *projection.hidden) for p in curve.sample()]
    return min(p[0] for p in points), min(p[1] for p in points), max(p[0] for p in points), max(p[1] for p in points)


def choose_scale(views, sheet, dimensions=(), datum_marks=(), mode='first_angle',
                 notes=(), tolerance_note=''):
    """普通件保留原值；大件缩小、小件放大。70% 是本项目标注留白策略。"""
    frame = make_frame(sheet)
    from drawform.output.paper_notes import required_height, NotesOverflow
    note_height = required_height(notes, frame.inner, tolerance_note)
    boxes = [_box(v.projection) for v in views]
    points = [(p[0]+v.origin[0], p[1]+v.origin[1])
              for v in views for c in (*v.projection.visible, *v.projection.hidden) for p in c.sample()]
    horizontal_gaps = max(1, len(views)-2)
    width = max(p[0] for p in points)-min(p[0] for p in points)-horizontal_gaps*DEFAULT_GAP
    height = max(p[1] for p in points)-min(p[1] for p in points)-DEFAULT_GAP
    usable_width = frame.inner[2]-frame.inner[0]
    readability_height = frame.inner[3]-frame.inner[1]-BLOCK_H-TITLE_CLEAR
    usable_height = frame.inner[3]-frame.inner[1]-note_height-TITLE_CLEAR
    if .7*usable_height <= DEFAULT_GAP:
        raise NotesOverflow('全部技术注记已占满图幅，未缩字或删除内容')
    limit = min((.7*usable_width-horizontal_gaps*DEFAULT_GAP)/max(width, 1e-9),
                (.7*usable_height-DEFAULT_GAP)/max(height, 1e-9))
    largest = max(max(b[2]-b[0], b[3]-b[1]) for b in boxes)
    factor = (_preferred(limit) if limit < 1 else
              max(1.0, _preferred(min(50.0, limit))) if largest < readability_height/10 else 1.0)
    if factor == 1 and note_height == BLOCK_H:
        return factor
    # 纸上最大视图至少占可用高度的 1/10：与小件放大阈值对称的本项目策略，
    # 不是标准规定的数值。不能为了塞进标注把原本清楚的轮廓缩成黑点。
    minimum = 1.0 if factor > 1 else readability_height/(10*largest)
    # 这是落纸前的有限比例规划，不重跑引擎或交付门。字高与标注带仍按纸面毫米。
    while factor >= minimum:
        layout = prepare_layout(views, dimensions, datum_marks, (), sheet, mode, factor,
                                notes=notes, tolerance_note=tolerance_note)
        bounds = content_box(layout.views, layout.dimensions, layout.datums)
        area = (layout.content_frame or frame).inner
        margin = 2*TEXT_HEIGHT
        if (bounds[0] >= area[0]+margin and bounds[1] >= area[1]+margin
                and bounds[2] <= area[2]-margin and bounds[3] <= area[3]-margin):
            return factor
        factor = _preferred(factor*(1-1e-10))
    raise SheetOverflow(f'{sheet} 当前标注带布局超过可用图框；继续缩小会影响轮廓可读性。'
                        '需人工调整布局或分图，未缩字或删除尺寸')


def _point(point, factor):
    return point[0]*factor, point[1]*factor


def _curve(curve, factor):
    if isinstance(curve, (Line2D, Centerline)):
        return replace(curve, start=_point(curve.start, factor), end=_point(curve.end, factor))
    if isinstance(curve, (Circle2D, Arc2D)):
        return replace(curve, center=_point(curve.center, factor), radius=curve.radius*factor)
    if isinstance(curve, Polyline2D):
        return replace(curve, points=tuple(_point(p, factor) for p in curve.points))
    raise TypeError(f'未知的纸面曲线：{type(curve).__name__}')


def _centerline(line, factor):
    """中心线随图放大，但**超出量**不跟着放大 —— OVERSHOOT 是纸面尺寸，不是模型尺寸。

    分布圆是圆而不是线段（螺栓圆孔系），没有端点可外伸，按普通曲线缩放即可。
    """
    if not isinstance(line, Centerline):
        return _curve(line, factor)
    length = math.dist(line.start, line.end)
    if length < 1e-9:
        return _curve(line, factor)
    direction = ((line.end[0]-line.start[0])/length, (line.end[1]-line.start[1])/length)
    correction = OVERSHOOT*(factor-1)
    return replace(line, start=tuple(line.start[i]*factor+direction[i]*correction for i in (0, 1)),
                   end=tuple(line.end[i]*factor-direction[i]*correction for i in (0, 1)))


def _dimensions(dimensions, projections, factor):
    result = []
    for dim in dimensions:
        base = None
        if dim.base is not None:
            box = _box(projections[dim.view])
            ux, uy = dim.direction()
            if abs(ux) > 1e-9 and abs(uy) > 1e-9:
                nx, ny = -uy, ux
                offset = (dim.base[0]-dim.p1[0])*nx+(dim.base[1]-dim.p1[1])*ny
                base = (dim.base[0]*factor+nx*offset*(1-factor),
                        dim.base[1]*factor+ny*offset*(1-factor))
            elif abs(uy) < 1e-9:
                edge = min((box[1], box[3]), key=lambda v: abs(dim.base[1]-v))
                base = (dim.base[0]*factor, edge*factor+dim.base[1]-edge)
            else:
                edge = min((box[0], box[2]), key=lambda v: abs(dim.base[0]-v))
                base = (edge*factor+dim.base[0]-edge, dim.base[1]*factor)
        result.append(replace(dim, p1=_point(dim.p1, factor), p2=_point(dim.p2, factor), base=base,
                              shared_witnesses=tuple((_point(a, factor), _point(b, factor))
                                                     for a, b in dim.shared_witnesses)))
    return tuple(result)


def _datums(marks, factor):
    return tuple(replace(m, anchor=_point(m.anchor, factor), reach=m.reach*factor) for m in marks)


def _clear_datum_sides(dimensions, marks, projections):
    """Move a blocked dimension fan to the opposite side, without moving its witnesses."""
    from drawform.output.drawing import _dimension, datum_anchor_slide, DATUM_LEADER
    dimensions, marks = list(dimensions), list(marks)
    changed = set()
    for index, mark in enumerate(marks):
        lines = [line for d in dimensions if d.view == mark.view for line in _dimension(d,(0,0))[0]]
        if datum_anchor_slide(mark, lines) is not None:
            continue
        axis = 0 if abs(mark.outward[0]) > .99 else 1 if abs(mark.outward[1]) > .99 else None
        if axis is None:
            continue
        box = _box(projections[mark.view]);side = 1 if mark.outward[axis] > 0 else -1
        edge = box[axis+2] if side > 0 else box[axis]
        opposite = box[axis] if side > 0 else box[axis+2]
        candidate = list(dimensions)
        selected = []
        for i,d in enumerate(dimensions):
            if d.view != mark.view or d.base is None or abs(d.direction()[axis]) > 1e-9:
                continue
            distance = side*(d.base[axis]-edge)
            if distance <= 0:
                continue
            base = list(d.base);base[axis] = opposite-side*distance
            candidate[i] = replace(d,base=tuple(base));selected.append(i)
        newmark = replace(mark,standoff=DATUM_LEADER)
        # The opposite side may already own a lane. Never overlay distinct
        # dimension lines just to free the datum side.
        conflict = False
        for i in selected:
            a = candidate[i]
            alo,ahi = sorted((a.p1[1-axis],a.p2[1-axis]))
            for j,b in enumerate(candidate):
                if (j in selected or b.view != mark.view or b.base is None
                        or abs(b.direction()[axis]) > 1e-9
                        or abs(a.base[axis]-b.base[axis]) > 1e-6):
                    continue
                blo,bhi = sorted((b.p1[1-axis],b.p2[1-axis]))
                if min(ahi,bhi)-max(alo,blo) > 1e-6:
                    conflict = True
        if conflict:
            continue
        lines = [line for d in candidate if d.view == mark.view for line in _dimension(d,(0,0))[0]]
        if selected and datum_anchor_slide(newmark,lines) is not None:
            dimensions = candidate;marks[index] = newmark
            changed.update(dimensions[i].source for i in selected)
    return tuple(dimensions),tuple(marks),changed


@dataclass(frozen=True)
class PaperLayout:
    factor: float
    model_views: tuple
    model_dimensions: tuple
    views: tuple
    dimensions: tuple
    datums: tuple
    sections: tuple
    reference_dimensions: tuple | None
    reference_datums: tuple
    content_frame: Frame | None


def _annotation_bounds(view, dimensions, datums, sections):
    """视图的原始标注占用，几何和字框复用共同 Drawing 的定义。"""
    from drawform.output.annotation_layout import annotation_box
    from drawform.output.drawing import (_dimension, _datum_mark, _section_mark,
                                         Text, VIEW_LABEL_GAP)
    key = view.label or view.projection.direction.name
    local = replace(view, origin=(0, 0))
    dims = [d for d in dimensions if d.view == key]
    marks = [m for m in datums if m.view == key]
    x0, y0, x1, y1 = content_box((local,), dims, marks)
    points = [(x0, y0), (x1, y1)]
    groups = [_dimension(d, (0, 0)) for d in dims]
    groups.extend(_datum_mark(m, local) for m in marks)
    for mark in sections:
        if mark.view == key:
            lines, texts = _section_mark(mark, local)
            groups.append((lines, (), texts))
    if view.label and view.label != view.projection.direction.name:
        box = _box(view.projection)
        groups.append(((), (), (Text(view.label, (box[0]+box[2])/2,
                                     box[1]-VIEW_LABEL_GAP, 'middle'),)))
    for lines, heads, texts in groups:
        points.extend(p for line in lines for p in line)
        points.extend(p for head in heads for p in head)
        for text in texts:
            a, b, c, d = annotation_box(text)
            points.extend(((a, b), (c, d)))
    return min(x for x,y in points), min(y for x,y in points), max(x for x,y in points), max(y for x,y in points)


def _space_annotations(views, dimensions, datums, sections, mode):
    """原轮廓间距作下限；标注外沿另留一行字高的项目留白，不改投影对齐。"""
    by = {v.label or v.projection.direction.name: v for v in views}
    if not all(key in by for key in ('front', 'top', 'left')):
        return tuple(views)
    boxes = {key: _annotation_bounds(view, dimensions, datums, sections) for key,view in by.items()}
    front, left, top = (by[key] for key in ('front', 'left', 'top'))
    f, l, t = (boxes[key] for key in ('front', 'left', 'top'))
    if mode == 'first_angle':
        left = replace(left, origin=(max(left.origin[0], front.origin[0]+f[2]+TEXT_HEIGHT-l[0]), left.origin[1]))
        bottom = min(front.origin[1]+f[1], left.origin[1]+l[1])
        top = replace(top, origin=(top.origin[0], min(top.origin[1], bottom-TEXT_HEIGHT-t[3])))
    else:
        left = replace(left, origin=(min(left.origin[0], front.origin[0]+f[0]-TEXT_HEIGHT-l[2]), left.origin[1]))
        upper = max(front.origin[1]+f[3], left.origin[1]+l[3])
        top = replace(top, origin=(top.origin[0], max(top.origin[1], upper+TEXT_HEIGHT-t[1])))
    by.update(front=front, left=left, top=top)
    right = max(by[key].origin[0]+boxes[key][2] for key in ('front', 'top', 'left'))
    for view in views:
        key = view.label or view.projection.direction.name
        if key in ('front', 'top', 'left'):
            continue
        view = replace(view, origin=(max(view.origin[0], right+TEXT_HEIGHT-boxes[key][0]), view.origin[1]))
        by[key] = view
        right = max(right, view.origin[0]+boxes[key][2])
    return tuple(by[v.label or v.projection.direction.name] for v in views)


def prepare_layout(views, dimensions, datum_marks, section_marks, sheet, mode, factor,
                   layout_dimensions=None, layout_marks=(), notes=(), tolerance_note=''):
    """纸面副本可缩放；返回的模型尺寸原值、数量和测量端点逐字保留。"""
    originals = {v.label or v.projection.direction.name: v for v in views}
    projections = {key: v.projection for key, v in originals.items()}
    paper_dimensions = _dimensions(dimensions, projections, factor)
    reference = None if layout_dimensions is None else _dimensions(layout_dimensions, projections, factor)
    if factor == 1:
        placed = tuple(views)
        content_frame = None
    else:
        scaled = {key: replace(v.projection,
                               visible=tuple(_curve(c, factor) for c in v.projection.visible),
                               hidden=tuple(_curve(c, factor) for c in v.projection.hidden),
                               material_supports=tuple(replace(s, lower_2d=_point(s.lower_2d,factor),
                                                               upper_2d=_point(s.upper_2d,factor))
                                                       for s in v.projection.material_supports))
                  for key, v in originals.items()}
        basic = layout_views(tuple(scaled[key] for key in ('front', 'top', 'left')), mode=mode)
        placed = []
        for view in basic:
            original = originals[view.projection.direction.name]
            placed.append(replace(view, label=original.label,
                                  centerlines=tuple(_centerline(c, factor) for c in original.centerlines),
                                  hatch=tuple(_curve(c, factor) for c in original.hatch)))
        for key, original in originals.items():
            if key in ('front', 'top', 'left'):
                continue
            box = _box(scaled[key])
            right = max(_box(v.projection)[2]+v.origin[0] for v in placed)
            front_y = next(v.origin[1] for v in placed if v.projection.direction.name == 'front')
            placed.append(replace(original, projection=scaled[key], origin=(right+DEFAULT_GAP-box[0], front_y-box[1]),
                                  centerlines=tuple(_centerline(c, factor) for c in original.centerlines),
                                  hatch=tuple(_curve(c, factor) for c in original.hatch)))
        frame = make_frame(sheet)
        x0, y0, x1, y1 = frame.inner
        content_frame = replace(frame, inner=(x0, y0+BLOCK_H+TITLE_CLEAR, x1, y1))
    from drawform.output.paper_notes import required_height
    frame = make_frame(sheet)
    note_height = required_height(notes, frame.inner, tolerance_note)
    if content_frame is not None or note_height > BLOCK_H:
        x0,y0,x1,y1 = frame.inner
        content_frame = replace(frame, inner=(x0,y0+note_height+TITLE_CLEAR,x1,y1))
    reference_marks = _datums(layout_marks if layout_dimensions is not None else datum_marks, factor)
    sizing_dimensions = paper_dimensions if reference is None else reference
    paper_projections = {key: (view.projection if factor == 1 else scaled[key]) for key,view in originals.items()}
    sizing_dimensions, reference_marks, swapped = _clear_datum_sides(sizing_dimensions,reference_marks,paper_projections)
    if reference is None:
        paper_dimensions = sizing_dimensions
        paper_marks = reference_marks
    else:
        reference = sizing_dimensions
        bases = {d.source:d.base for d in reference if d.source in swapped}
        paper_dimensions = tuple(replace(d,base=bases[d.source]) if d.source in bases else d for d in paper_dimensions)
        adjusted = {m.letter:m.standoff for m in reference_marks}
        paper_marks = tuple(replace(m,standoff=adjusted.get(m.letter,m.standoff)) for m in _datums(datum_marks,factor))
    paper_sections = tuple(replace(m, at=m.at*factor) for m in section_marks)
    placed = _space_annotations(placed, sizing_dimensions, reference_marks, paper_sections, mode)
    placed = (sheet_views(placed, sheet, sizing_dimensions) if content_frame is None else
              center_on_frame(placed, content_frame, sizing_dimensions, reference_marks))
    model_views = tuple(replace(originals[v.label or v.projection.direction.name],
                                origin=_point(v.origin, 1/factor),
                                centerlines=tuple(_curve(c, 1/factor) for c in v.centerlines),
                                hatch=tuple(_curve(c, 1/factor) for c in v.hatch)) for v in placed)
    model_dimensions = tuple(replace(original, base=None if paper.base is None else _point(paper.base, 1/factor))
                             for original, paper in zip(dimensions, paper_dimensions))
    return PaperLayout(factor, model_views, model_dimensions, tuple(placed), paper_dimensions,
                       paper_marks,
                       paper_sections,
                       reference, reference_marks if layout_dimensions is not None else (), content_frame)


def model_graphics(drawing, factor):
    """DXF 的显示块按纸面反变换，原生曲线和 DIMENSION 仍使用真实毫米。"""
    inverse = 1/factor
    return Drawing(drawing.sheet,
                   tuple((layer, tuple(tuple(_point(p, inverse) for p in run) for run in runs))
                         for layer, runs in drawing.lines),
                   tuple(tuple(_point(p, inverse) for p in arrow) for arrow in drawing.arrows),
                   tuple(replace(t, x=t.x*inverse, y=t.y*inverse, height=t.height*inverse) for t in drawing.texts),
                   tuple(replace(item, points=tuple(_point(p, inverse) for p in item.points)) for item in drawing.items))

"""一张工程图落到纸面上的全部内容。SVG 与 PDF 各自序列化它，不再各算一遍几何。

为什么必须先有这一层：网页端交付的是 SVG，客户打印与归档要 PDF。两份各画一次
就等于两张图 —— 尺寸线位置、字头方向、箭头长短都会在两个出口之间漂移，
而这类漂移在车间里表现为"网页上的图和纸上的图对不上"。

坐标约定：图纸毫米、y 向上、原点在图纸左下角。翻转 y 是每个序列化器自己的事。
剖切符号与视图名的算法和 `output/dxf.py` 对齐：同一张图从两个出口出去必须长一样。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

from drawform.output.sheet import center_on_frame, make_frame
from drawform.output.titleblock import title_block

#: 尺寸要素。与 DXF 侧的 dimstyle 取同一组值，否则 DXF 与网页不是同一张图。
DIM_ARROW = 3.0        # 箭头长度（dimasz）
ARROW_HALF = 0.6       # 箭头半宽
EXT_OVERSHOOT = 1.5    # 尺寸界线超出尺寸线（dimexe）
EXT_GAP = 0.5          # 尺寸界线起点离轮廓的空隙
TEXT_GAP = 1.0         # 文字与尺寸线（dimgap）
CHAR_WIDTH = 0.7       # isocp 的字宽/字高比，用它估引出线要留多长
TEXT_HEIGHT = 3.5      # GB/T 14691 最小字高
NOTE_LINE_GAP = 6.0    # 技术要求行距
VIEW_LABEL_GAP = 6.0   # 视图名离轮廓的距离
MARK_STUB = 3.0        # 剖切位置线超出轮廓的量
MARK_ARROW = 6.0       # 剖切方向线的长度
DATUM_BOX = 7.0        # 基准方框边长：字高的两倍（GB/T 1182 的方框按 2d 画）
DATUM_LEADER = 3.0     # 三角形到方框的引出线长度

#: 层 → (线宽 mm, 线型)。线型用 GB/T 4457.4 的毫米段长写：正数是线段、负数是空档，
#: 与 DXF 的 linetype pattern 同一种写法，None 是连续线。顺序即绘制顺序。
#: 不能沿用 AutoCAD 内置的 DASHED/CENTER：它们按英寸定义，虚线周期只有 1.524 mm，
#: 毫米图上打印几乎连成实线。
STYLES = {
    "FRAME": (0.50, None),
    "VISIBLE": (0.50, None),
    "HIDDEN": (0.25, (8.0, -1.0)),
    "HATCH": (0.18, None),
    # 基准方框与引出线按 GB/T 1182 用细实线画；贴面的三角形是实心的，走 arrows。
    "DATUM": (0.25, None),
    "TITLE": (0.25, None),
    "CENTER": (0.25, (18.0, -3.0, 0.75, -3.0)),
    "SECTION": (0.50, None),
    "DIM": (0.25, None),
}


@dataclass(frozen=True)
class Text:
    content: str
    x: float
    y: float
    anchor: str = "start"
    rotate: float = 0.0
    #: 这块字是谁画的（"dim:(feature,dof)" / "datum:A" / "view:front" …）。
    #: 没有归属就没法判冲突：一条尺寸自己的尺寸界线压着自己的字是正常的，
    #: 别人压过来才是缺陷。少了这个字段，碰撞检测只能靠猜。
    owner: str = ""
    #: 尺寸字高默认 3.5 mm；标题栏使用各格声明的字高，外部图纸保留原文件字高。
    height: float = TEXT_HEIGHT


@dataclass(frozen=True)
class DrawingItem:
    """一笔有归属的图面元素：谁画的、落在哪层、走的是哪些点。"""

    owner: str
    layer: str
    points: tuple[tuple[float, float], ...]


@dataclass(frozen=True)
class SectionMark:
    """剖切符号。`toward` 是该视图局部坐标里的投影方向，位置线与它垂直。"""

    view: str
    at: float
    letter: str
    toward: tuple[float, float]


@dataclass(frozen=True)
class DatumMark:
    """基准代号（GB/T 1182）：贴在基准面上的涂黑三角形 + 引出线 + 带字母的方框。

    `anchor` 是视图局部坐标下**真实投影轮廓上**的一点，`outward` 是离开零件的方向。
    """

    view: str
    anchor: tuple[float, float]
    outward: tuple[float, float]
    letter: str
    #: 引出线长度。默认贴着轮廓，但那一圈往往已经被尺寸带占了 ——
    #: 落位层会按该侧最外一条尺寸带把它推出去，否则方框直接压在尺寸线上。
    standoff: float = DATUM_LEADER
    #: 这条基准面沿切向还能让代号滑多远（面长的一半，视图局部坐标）。
    #: 基准标注的是一个**平面**，贴在面上任何一点都同样成立；滑出这个范围就不成立了，
    #: 那时宁可留下一条被报出来的冲突，也不把代号挂到别的面上去。
    reach: float = 0.0


@dataclass(frozen=True)
class Drawing:
    """层名 → 折线集合，加实心箭头与文字。"""

    sheet: str
    lines: tuple[tuple[str, tuple[tuple[tuple[float, float], ...], ...]], ...]
    arrows: tuple[tuple[tuple[float, float], ...], ...]
    texts: tuple[Text, ...]
    #: 带归属的全套元素。`lines` 是按层分组的渲染视图，`items` 才是可追溯的那一份；
    #: 冲突检测读 items，序列化仍读 lines。
    items: tuple[DrawingItem, ...] = ()


def sheet_views(placed, sheet: str = "A3", dimensions=()):
    """渲染真正落到纸上的视图（已经搬进图框）。

    居中发生在这里。不把它暴露出去，调用方要算尺寸落在纸上的位置就只能靠猜。
    """
    return center_on_frame(placed, make_frame(sheet), dimensions)


def _arrow(tip, toward):
    """实心箭头。`toward` 是从箭尾指向箭尖的单位向量，箭尖落在 `tip`。"""
    tx, ty = toward
    base = (tip[0] - tx * DIM_ARROW, tip[1] - ty * DIM_ARROW)
    px, py = -ty, tx
    return (tip, (base[0] + px * ARROW_HALF, base[1] + py * ARROW_HALF),
            (base[0] - px * ARROW_HALF, base[1] - py * ARROW_HALF))


def _dimension(dim, origin):
    """一条尺寸 → (折线, 箭头, 文字)，坐标已经加上视图 origin。

    `base` 是落位层算好的尺寸线位置：有 base 的是线性/定位/深度尺寸，
    没有的是直径与分布圆 —— 后者从圆上一点沿半径引出，不占尺寸线。
    """
    ox, oy = origin
    p1 = (dim.p1[0] + ox, dim.p1[1] + oy)
    p2 = (dim.p2[0] + ox, dim.p2[1] + oy)
    lines: list[tuple[tuple[float, float], ...]] = []
    heads: list[tuple[tuple[float, float], ...]] = []
    labels: list[Text] = []

    if dim.base is None:
        span = math.hypot(p2[0] - p1[0], p2[1] - p1[1]) or 1.0
        ux, uy = (p2[0] - p1[0]) / span, (p2[1] - p1[1]) / span
        tail = (p2[0] + ux * DIM_ARROW, p2[1] + uy * DIM_ARROW)
        room = TEXT_GAP + CHAR_WIDTH * TEXT_HEIGHT * len(dim.text)
        elbow = (tail[0] + ux * DIM_ARROW, tail[1] + uy * DIM_ARROW)
        side = 1 if ux >= 0 else -1
        end = (elbow[0] + side * room, elbow[1])
        lines.append((p2, tail, elbow, end))
        heads.append(_arrow(p2, (ux, uy)))
        labels.append(Text(dim.text, (elbow[0]+end[0])/2,
                           elbow[1] + TEXT_GAP + TEXT_HEIGHT/2, "middle"))
        return lines, heads, labels

    bx, by = dim.base[0] + ox, dim.base[1] + oy
    witnesses = [(p1,p2)] + [((a[0]+ox,a[1]+oy),(b[0]+ox,b[1]+oy))
                             for a,b in dim.shared_witnesses]
    dx, dy = p2[0]-p1[0], p2[1]-p1[1]
    ux, uy = dim.direction()
    if abs(ux) > 1e-9 and abs(uy) > 1e-9:
        nx, ny = -uy, ux
        offset = (bx-p1[0])*nx+(by-p1[1])*ny
        q1 = (p1[0]+nx*offset, p1[1]+ny*offset)
        second = (bx-p2[0])*nx+(by-p2[1])*ny
        q2 = (p2[0]+nx*second, p2[1]+ny*second)
        lines.append((q1, q2))
        side = math.copysign(1, offset)
        for index, outward in ((0, -1), (1, 1)):
            point = max((pair[index] for pair in witnesses),
                        key=lambda p: abs((p[0]-p1[0])*nx+(p[1]-p1[1])*ny-offset))
            distance = offset-(point[0]-p1[0])*nx-(point[1]-p1[1])*ny
            end = (point[0]+nx*distance, point[1]+ny*distance)
            lines.append(((point[0]+side*nx*EXT_GAP, point[1]+side*ny*EXT_GAP),
                          (end[0]+side*nx*EXT_OVERSHOOT, end[1]+side*ny*EXT_OVERSHOOT)))
            heads.append(_arrow(end, (outward*ux, outward*uy)))
        angle = math.degrees(math.atan2(uy, ux))
        if angle > 90:
            angle -= 180
        elif angle < -90:
            angle += 180
        room = TEXT_GAP+TEXT_HEIGHT/2
        labels.append(Text(dim.text, (q1[0]+q2[0])/2-math.sin(math.radians(angle))*room,
                           (q1[1]+q2[1])/2+math.cos(math.radians(angle))*room,
                           'middle', -angle))
        return lines, heads, labels
    if abs(uy) < 1e-9:
        side = 1.0 if by >= p1[1] else -1.0
        along = math.copysign(1.0, p2[0] - p1[0])
        lines.append(((p1[0], by), (p2[0], by)))
        ends = [max((pair[i] for pair in witnesses), key=lambda p: abs(p[1]-by)) for i in (0,1)]
        for point, outward in zip(ends, (-along,along)):
            lines.append(((point[0], point[1] - side * EXT_GAP),
                          (point[0], by + side * EXT_OVERSHOOT)))
            heads.append(_arrow((point[0], by), (outward, 0.0)))
        labels.append(Text(dim.text, (p1[0] + p2[0]) / 2.0, by + TEXT_GAP, "middle"))
    else:
        side = 1.0 if bx >= p1[0] else -1.0
        along = math.copysign(1.0, p2[1] - p1[1])
        lines.append(((bx, p1[1]), (bx, p2[1])))
        ends = [max((pair[i] for pair in witnesses), key=lambda p: abs(p[0]-bx)) for i in (0,1)]
        for point, outward in zip(ends, (-along,along)):
            lines.append(((point[0] + side * EXT_GAP, point[1]),
                          (bx + side * EXT_OVERSHOOT, point[1])))
            heads.append(_arrow((bx, point[1]), (0.0, outward)))
        # 字头朝左（GB/T 4458.1）：横排会把六位数摊成 15 mm 宽，压满相邻偏移带。
        room = TEXT_GAP + TEXT_HEIGHT / 2.0
        labels.append(Text(dim.text, bx + side * room, (p1[1] + p2[1]) / 2.0,
                           "middle", -90.0))

    return lines, heads, labels


def _view_key(view) -> str:
    return view.label or view.projection.direction.name


def _local_box(view):
    points = [p for c in tuple(view.projection.visible) + tuple(view.projection.hidden)
              for p in c.sample()]
    return (min(p[0] for p in points), min(p[1] for p in points),
            max(p[0] for p in points), max(p[1] for p in points))


def _section_mark(mark, view):
    """剖切位置线 + 两端的方向线 + 字母。`at` 是视图局部坐标里剖平面的位置。

    剖平面与哪个视图的投影面垂直，位置线就画在哪个视图上；画错视图等于指错剖切位置。
    """
    ux, uy = mark.toward
    norm = math.hypot(ux, uy) or 1.0
    ux, uy = ux / norm, uy / norm
    vx, vy = -uy, ux
    box = _local_box(view)
    anchor = (mark.at * ux, mark.at * uy)
    corners = [(box[0], box[1]), (box[2], box[1]), (box[0], box[3]), (box[2], box[3])]
    spans = [(cx - anchor[0]) * vx + (cy - anchor[1]) * vy for cx, cy in corners]
    ends = [(anchor[0] + t * vx, anchor[1] + t * vy)
            for t in (min(spans) - MARK_STUB, max(spans) + MARK_STUB)]

    ox, oy = view.origin
    lines = [((ox + ends[0][0], oy + ends[0][1]), (ox + ends[1][0], oy + ends[1][1]))]
    labels = []
    for ex, ey in ends:
        base = (ox + ex, oy + ey)
        lines.append((base, (base[0] + ux * MARK_ARROW, base[1] + uy * MARK_ARROW)))
        labels.append(Text(mark.letter, base[0] + ux * MARK_ARROW,
                           base[1] + uy * MARK_ARROW + 1.0, "middle"))
    return lines, labels


def datum_points(anchor, outward, standoff: float = DATUM_LEADER):
    """基准代号在视图局部坐标里占到的点：三角形尖、引出线折点、方框四角。

    方框正放（不随引出线旋转），字母才立得起来；本层只处理水平/垂直引出，
    所以四角直接按中心 ± 半边长取。
    居中用这一份、渲染也用这一份 —— 两处各写一遍"方框多大、往外伸出多少"，
    迟早算出两个尺寸，图就会把代号裁掉。
    """
    ux, uy = outward
    norm = math.hypot(ux, uy) or 1.0
    ux, uy = ux / norm, uy / norm
    elbow = (anchor[0] + ux * standoff, anchor[1] + uy * standoff)
    half = DATUM_BOX / 2.0
    centre = (elbow[0] + ux * half, elbow[1] + uy * half)
    corners = ((centre[0] - half, centre[1] - half), (centre[0] + half, centre[1] - half),
               (centre[0] + half, centre[1] + half), (centre[0] - half, centre[1] + half))
    return anchor, elbow, centre, corners


#: 代号沿基准面滑动的步长（mm）。比方框小，才可能在密集的尺寸带之间找到缝。
DATUM_SLIDE = 2.0


def _datum_group(anchor, outward, standoff: float = DATUM_LEADER):
    """代号在视图局部坐标里占到的折线：引出线一条 + 方框四条边围成的一圈。"""
    anchor_p, elbow, _centre, corners = datum_points(anchor, outward, standoff)
    return (elbow, anchor_p), corners + (corners[0],)


def datum_anchor_slide(mark: DatumMark, lanes) -> float | None:
    """引出线/方框与尺寸线相交时，沿**基准面自己的切向**滑多远能避开。

    返回 `0.0` 表示本来就不冲突（不冲突就不动 —— 否则每次出图都重排一遍位置）；
    返回 `None` 表示在这条棱的范围内躲不开，那就留在原地并被 `find_collisions` 报出来。
    躲不开的冲突比"干净"的假象有用：悄悄把代号挂到别的面上，图上就撒了谎。
    """
    from drawform.output.collide import segments_cross

    ux, uy = mark.outward
    norm = math.hypot(ux, uy) or 1.0
    tx, ty = -uy / norm, ux / norm

    def covers_point(run, point):
        # A zero-length dimension still owns a real attachment point. Ordinary
        # segment crossing excludes degenerate segments; the datum must not
        # move onto that point and trap its later annotation leader.
        for a, b in zip(run, run[1:]):
            dx, dy = b[0]-a[0], b[1]-a[1]
            square = dx*dx+dy*dy
            t = max(0.0, min(1.0, ((point[0]-a[0])*dx+(point[1]-a[1])*dy)/square)) if square else 0.0
            if math.dist(point, (a[0]+t*dx, a[1]+t*dy)) <= 1e-6:
                return True
        return False

    def clear(delta: float) -> bool:
        leader, box = _datum_group((mark.anchor[0] + tx * delta,
                                    mark.anchor[1] + ty * delta),
                                   mark.outward, mark.standoff)
        return not any(segments_cross(run, lane)
                       or any(math.dist(a, b) <= 1e-6 and covers_point(run, a)
                              for a, b in zip(lane, lane[1:]))
                       for run in (leader, box) for lane in lanes)

    if clear(0.0):
        return 0.0
    # reach 为 0 时下面的区间天然是空的：不写"点基准不许滑"这条特例，
    # 少一个与判据无关的分支（变异测试证明它删掉行为不变）。
    for step in range(1, int(mark.reach / DATUM_SLIDE) + 1):
        for sign in (1.0, -1.0):
            delta = sign * step * DATUM_SLIDE
            if clear(delta):
                return delta
    # A scaled finite face need not end on the search grid. Its exact endpoint
    # can be the only position clear of a nested dimension fan.
    for delta in (mark.reach, -mark.reach):
        if delta and clear(delta):
            return delta
    return None


def _datum_mark(mark, view):
    """涂黑三角形贴在基准面上，引出线接方框，字母写在框里。

    三角形尖朝零件：它指的就是"尺寸从这条线量起"，尖画反等于指向别处。
    """
    ox, oy = view.origin
    ux, uy = mark.outward
    norm = math.hypot(ux, uy) or 1.0
    ux, uy = ux / norm, uy / norm
    anchor, elbow, centre, corners = datum_points(mark.anchor, mark.outward,
                                                  mark.standoff)
    start = (ox + anchor[0], oy + anchor[1])
    box = tuple((ox + cx, oy + cy) for cx, cy in corners)
    box = box + (box[0],)
    lines = [box, ((ox + elbow[0], oy + elbow[1]), start)]
    heads = [_arrow(start, (-ux, -uy))]
    return lines, heads, [Text(mark.letter, ox + centre[0], oy + centre[1]-TEXT_HEIGHT/3, "middle")]


def build_drawing(placed, sheet: str = "A3", dimensions=(), notes=(),
                  section_marks=(), datum_marks=(), title_meta=None,
                  view_labels: bool = True, text_overrides=None,
                  layout_dimensions=None, layout_reference=None, changed_owners=(),
                  placed_on_sheet=False) -> Drawing:
    """把排版好的视图、尺寸与符号算成一张图的全部内容。"""
    if not placed_on_sheet:
        placed = sheet_views(placed, sheet, dimensions if layout_dimensions is None else layout_dimensions)
    x0, y0, x1, y1 = make_frame(sheet).inner
    layers: dict[str, list[tuple[tuple[float, float], ...]]] = {
        "FRAME": [((x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0))]}
    arrows: list[tuple[tuple[float, float], ...]] = []
    texts: list[Text] = []
    items: list[DrawingItem] = []

    def add(layer, points, owner=""):
        run = tuple(points)
        layers.setdefault(layer, []).append(run)
        items.append(DrawingItem(owner=owner, layer=layer, points=run))

    for view in placed:
        ox, oy = view.origin
        key = _view_key(view)
        for curves, layer in ((view.projection.visible, "VISIBLE"),
                              (view.projection.hidden, "HIDDEN"),
                              (view.centerlines, "CENTER"),
                              (view.hatch, "HATCH")):
            for curve in curves:
                add(layer, ((p[0] + ox, p[1] + oy) for p in curve.sample()),
                    f"view:{key}")

    by_key = {_view_key(view): view for view in placed}
    for dim in dimensions:
        if dim.view not in by_key:
            raise KeyError(
                f"尺寸 {dim.text} 指向视图 {dim.view!r}，排版的视图里只有 "
                f"{sorted(by_key)} —— 这条尺寸会画到别处去")
        owner = f"dim:{dim.source or dim.text}"
        lines, heads, labels = _dimension(dim, by_key[dim.view].origin)
        for line in lines:
            add("DIM", line, owner)
        arrows.extend(heads)
        texts.extend(replace(label, owner=owner) for label in labels)

    for mark in section_marks:
        if mark.view not in by_key:
            raise KeyError(f"剖切符号指向视图 {mark.view!r}，但它没被排版")
        owner = f"section:{mark.letter}"
        lines, labels = _section_mark(mark, by_key[mark.view])
        for line in lines:
            add("SECTION", line, owner)
        texts.extend(replace(label, owner=owner) for label in labels)

    for mark in datum_marks:
        if mark.view not in by_key:
            raise KeyError(f"基准代号 {mark.letter} 指向视图 {mark.view!r}，但它没被排版")
        owner = f"datum:{mark.letter}"
        if layout_reference is not None and owner not in changed_owners:
            previous = [item for item in layout_reference.items if item.owner == owner and item.layer == 'DATUM']
            if previous:
                for item in previous:
                    add('DATUM', item.points, owner)
                    if len(item.points) == 2:
                        elbow, tip = item.points
                        dx, dy = tip[0]-elbow[0], tip[1]-elbow[1]
                        norm = math.hypot(dx,dy) or 1
                        arrows.append(_arrow(tip,(dx/norm,dy/norm)))
                texts.extend(text for text in layout_reference.texts if text.owner == owner)
                continue
        view = by_key[mark.view]
        # 尺寸线已经画完了，这里能拿到该视图全部真实段位。滑动的判据与 `find_collisions`
        # 的 leader/line 必须是同一份，否则代号会去躲一条检测器不看的线。
        lane_items = (layout_reference.items if layout_reference is not None
                      and owner not in changed_owners else items)
        lanes = [tuple((p[0] - view.origin[0], p[1] - view.origin[1]) for p in item.points)
                 for item in lane_items if item.layer == "DIM"]
        delta = datum_anchor_slide(mark, lanes)
        if delta:
            norm = math.hypot(*mark.outward) or 1.0
            tx, ty = -mark.outward[1] / norm, mark.outward[0] / norm
            mark = replace(mark, anchor=(mark.anchor[0] + tx * delta,
                                        mark.anchor[1] + ty * delta))
        lines, heads, labels = _datum_mark(mark, by_key[mark.view])
        for line in lines:
            add("DATUM", line, owner)
        arrows.extend(heads)
        texts.extend(replace(label, owner=owner) for label in labels)

    if title_meta is not None:
        # 标题栏锚在图框右下角，不跟着内容跑：它是这张纸的一部分，不是这个零件的一部分。
        lines, labels = title_block(title_meta, make_frame(sheet).inner)
        for line in lines:
            add("TITLE", line, "titleblock")
        for content, tx, ty, size in labels:
            if content:
                texts.append(Text(content, tx, ty, "start", owner="titleblock", height=size))

    if view_labels:
        for view in placed:
            if not view.label or view.label == view.projection.direction.name:
                continue        # 基本视图靠位置表身份，标上 "front" 反而不合规
            box = _local_box(view)
            texts.append(Text(view.label, view.origin[0] + (box[0] + box[2]) / 2.0,
                              view.origin[1] + box[1] - VIEW_LABEL_GAP, "middle"))

    if title_meta is not None and title_meta.tolerance_note:
        # 标题栏那一格已经写了未注公差，左下角再抄一遍会让人以为是两条不同要求。
        notes = [n for n in notes if n != title_meta.tolerance_note]

    from drawform.output.paper_notes import other_labels
    for note, nx, ny, size in other_labels(notes, make_frame(sheet).inner,
                                          title_meta.tolerance_note if title_meta else ''):
        texts.append(Text(note, nx, ny, owner='paper-note', height=size))

    # 延迟导入：collide 要从这里取 Text/Drawing 算包围盒，模块级互相引用会绕成环。
    from drawform.output.collide import avoid_lines, separate_labels

    movable = [i for i, t in enumerate(texts) if t.owner not in ("", "titleblock", "paper-note")
               and not t.owner.startswith('datum:')]
    if movable:
        # 先躲线，再分字：躲完可能又挤出新的叠字，反过来则会留下穿过线的字。
        for index, clear in zip(movable, avoid_lines([texts[i] for i in movable], items)):
            texts[index] = clear
        for index, settled in zip(movable, separate_labels([texts[i] for i in movable])):
            texts[index] = settled

    if layout_reference is not None:
        original = {}
        for text in layout_reference.texts:
            original.setdefault(text.owner, []).append(text)
        offsets = {}
        for index, text in enumerate(texts):
            if (not text.owner or text.owner in changed_owners
                    or text.owner in ("titleblock", "paper-note") or text.owner not in original):
                continue
            offset = offsets.get(text.owner, 0)
            previous = original[text.owner][offset]
            texts[index] = replace(text, x=previous.x, y=previous.y,
                                   anchor=previous.anchor, rotate=previous.rotate)
            offsets[text.owner] = offset + 1

    from drawform.output.annotation_layout import settle_annotations
    fixed = set()
    if layout_reference is not None:
        current_dimensions = {f'dim:{dim.source or dim.text}' for dim in dimensions}
        fixed = {t.owner for t in layout_reference.texts if t.owner not in changed_owners
                 and (not t.owner.startswith('dim:') or t.owner in current_dimensions)}
        items = [item for item in items if not (item.layer == 'DIM' and item.owner in fixed)]
        items.extend(item for item in layout_reference.items if item.layer == 'DIM' and item.owner in fixed)
    from drawform.output.paper_notes import required_height
    from drawform.output.titleblock import BLOCK_H
    from drawform.output.sheet import TITLE_CLEAR
    annotation_frame = make_frame(sheet)
    note_height = required_height(notes, annotation_frame.inner, title_meta.tolerance_note if title_meta else '')
    if note_height > BLOCK_H:
        x0,y0,x1,y1 = annotation_frame.inner
        annotation_frame = replace(annotation_frame, inner=(x0,y0+note_height+TITLE_CLEAR,x1,y1))
    texts, items = settle_annotations(texts, items, dimensions,
                                      {key: view.origin for key, view in by_key.items()},
                                      annotation_frame, fixed)
    layers['DIM'] = [item.points for item in items if item.layer == 'DIM']

    # 回答只改已落位文字的内容；公差后缀不能带着其他标注重新挪位。
    if text_overrides:
        texts = [replace(text, content=text_overrides.get(text.owner, text.content))
                 for text in texts]

    drawing = Drawing(sheet=sheet,
                      lines=tuple((layer, tuple(runs))
                                  for layer, runs in layers.items() if runs),
                      arrows=tuple(arrows),
                      texts=tuple(texts),
                      items=tuple(items))
    _require_inside_frame(drawing, make_frame(sheet))
    return drawing


class SheetOverflow(Exception):
    """内容画不进这张幅面。宁可在这里停下，也别交出一张被裁掉一半的图。"""


def _require_inside_frame(drawing, frame):
    x0, y0, x1, y1 = frame.inner

    def outside_frame(px, py) -> bool:
        return not (x0 <= px <= x1 and y0 <= py <= y1)

    outside = []
    for layer, runs in drawing.lines:
        if layer == "FRAME":
            continue
        outside += [f"{layer} 层" for run in runs for (px, py) in run
                    if outside_frame(px, py)]
    outside += [f"文字「{t.content}」" for t in drawing.texts
                if outside_frame(t.x, t.y)]
    outside += ['箭头' for head in drawing.arrows for px,py in head if outside_frame(px,py)]
    if outside:
        raise SheetOverflow(
            f"{frame.sheet} 幅面放不下：{len(outside)} 处落到图框之外"
            f"（{sorted(set(outside))[:3]}）。换大幅面或减少视图。")

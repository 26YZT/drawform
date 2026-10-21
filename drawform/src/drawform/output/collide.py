"""图面元素互斥检测：谁和谁不许叠在一起。

为什么规则要收得很窄 —— 图纸上大量"相交"是正常画法：尺寸界线必须穿过零件轮廓才能
把两端引出来，箭头必须压在尺寸线上。把这些也报成冲突，检测器就废了，因为没人会去
清一万个假阳性。所以这里只报三类真缺陷：

1. `text/text`  两个不同归属的文字框互相压字 —— 读不出是哪个数；
2. `text/line`  文字被**别人**的线穿过（含被零件轮廓穿过：字必须落在空处）；
3. `leader/line` 基准/剖切符号的引出线穿过尺寸线 —— GB/T 不许符号线与尺寸线相交。

同归属的元素一律放过：一条尺寸自己的界线挨着自己的字是天经地义的。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import math

from drawform.output.drawing import CHAR_WIDTH, TEXT_HEIGHT, Drawing, Text

#: 允许与**别的线**相交的层。尺寸界线必须穿过零件轮廓才能把两端引出来，
#: 那是画法不是缺陷 —— 把这也报成冲突，检测器就会被扔掉。
_EXEMPT_LAYERS = frozenset({"VISIBLE", "HIDDEN", "CENTER", "HATCH", "FRAME"})
#: 文字不许被任何东西穿过（图框除外）：字压在轮廓或剖面线上，车间里读不出来。
_TEXT_EXEMPT_LAYERS = frozenset({"FRAME"})
_SYMBOL_LAYERS = frozenset({"DATUM", "SECTION"})


#: 分开之后仍要留的缝：正好相切在纸上就是字贴字。
LABEL_CLEARANCE = 0.5
#: 一条字最多允许离开自己尺寸多远。挪过头等于把字摘下来挂到别处，那时读不出这个数
#: 属于哪条尺寸 —— 宁可留下一条被报出来的冲突，也不要一张"干净"但读不懂的图。
LABEL_MAX_SHIFT = 8.0
_RELAX_PASSES = 12
#: 躲线时沿自己的轴试探的步长（mm）。比字高小，才可能在密集的线阵里找到缝。
LABEL_AVOID_STEP = 1.5


@dataclass(frozen=True)
class Collision:
    kind: str
    left: str
    right: str
    detail: str = ""


def _text_direction(text):
    angle = math.radians(-text.rotate)
    # 直角取精确值，避免 cos(pi/2) 的尾差改变紧邻字框的判定。
    return tuple(0.0 if abs(v) < 1e-12 else v for v in (math.cos(angle), math.sin(angle)))


def text_box(text: Text) -> tuple[float, float, float, float]:
    """文字占的矩形。字高固定，字宽按 GB/T 17450 的 0.7 倍字高估。

    旋转 90°（垂直尺寸的字头朝左）时宽高互换。这是**近似**：真实字形各有宽窄，
    但可读性判据要的是"挤不挤"，不是排版级精确 —— 报出来的重叠面积差一成就够用了。
    """
    width = CHAR_WIDTH * text.height * len(text.content)
    height = text.height
    if text.rotate:
        ux, uy = map(abs, _text_direction(text))
        width, height = width*ux+height*uy, width*uy+height*ux
    if text.anchor == "middle":
        x0, y0 = text.x - width / 2.0, text.y - height / 2.0
    elif text.anchor == "end":
        x0, y0 = text.x - width, text.y - height / 2.0
    else:
        x0, y0 = text.x, text.y - height / 2.0
    return (x0, y0, x0 + width, y0 + height)


def _boxes_overlap(a, b, gap: float = 0.0) -> bool:
    return not (a[2] + gap <= b[0] or b[2] + gap <= a[0]
                or a[3] + gap <= b[1] or b[3] + gap <= a[1])


def _centre(box) -> tuple[float, float]:
    return ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)


def separate_labels(texts) -> tuple[Text, ...]:
    """把互相压字的文字推开，除了位置什么都不改。

    两条规则都是制图习惯，不是几何最优化：
    - **每条字只沿自己的走向让**：横排的字沿尺寸线左右滑，字头朝左（垂直尺寸）的字上下滑。
      把横排的字抬离尺寸线，它就看上去属于旁边那条尺寸了 —— 哪怕位移更小也不能那样做。
    - 只动**不同归属**的文字：一条尺寸自己的两段字叠着是画法，不是缺陷。
    位移上限 `LABEL_MAX_SHIFT`，超了就留下一条被报出来的冲突。
    """
    moved = list(texts)
    boxes = [text_box(t) for t in moved]
    origin = [(t.x, t.y) for t in moved]

    for _ in range(_RELAX_PASSES):
        dirty = False
        for i in range(len(moved)):
            for j in range(i + 1, len(moved)):
                if moved[i].owner == moved[j].owner:
                    continue
                a, b = boxes[i], boxes[j]
                if not _boxes_overlap(a, b, LABEL_CLEARANCE):
                    continue
                overlap = (min(a[2], b[2]) - max(a[0], b[0]) + LABEL_CLEARANCE,
                           min(a[3], b[3]) - max(a[1], b[1]) + LABEL_CLEARANCE)
                for k, other in ((i, j), (j, i)):
                    direction = _text_direction(moved[k])
                    here, there = (sum(p*v for p,v in zip(_centre(box), direction))
                                   for box in (boxes[k], boxes[other]))
                    # 中心完全相等时不需要额外裁定：k 先动过之后盒子就分开了，
                    # 下一轮 `here < there` 自然给出方向。
                    distance = min(overlap[axis]/abs(v) for axis,v in enumerate(direction) if v)
                    delta = -distance/2.0 if here < there else distance/2.0
                    if _shift(moved, boxes, origin, k, direction, delta):
                        dirty = True
        if not dirty:
            break
    return tuple(moved)


def _shift(moved, boxes, origin, index, direction, delta) -> bool:
    point = (moved[index].x+direction[0]*delta, moved[index].y+direction[1]*delta)
    if math.dist(point, origin[index]) > LABEL_MAX_SHIFT:
        return False
    moved[index] = replace(moved[index], x=point[0], y=point[1])
    boxes[index] = text_box(moved[index])
    return True


def _crosses(p, q, r, s, tol: float = 1e-9) -> bool:
    """两条线段是否**真正**相交（共享端点不算）。"""

    def side(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    d1, d2 = side(r, s, p), side(r, s, q)
    d3, d4 = side(p, q, r), side(p, q, s)
    if ((d1 > tol and d2 < -tol) or (d1 < -tol and d2 > tol)) and \
       ((d3 > tol and d4 < -tol) or (d3 < -tol and d4 > tol)):
        return True
    # 共线贴边：只有真的穿过去才算，单纯端点相接不算
    def on(a, b, c):
        return (abs(d1) <= tol and min(r[0], s[0]) - tol <= a[0] <= max(r[0], s[0]) + tol
                and min(r[1], s[1]) - tol <= a[1] <= max(r[1], s[1]) + tol)
    if abs(d1) <= tol and on(p, q, r):
        return min(p[0], q[0]) <= r[0] <= max(p[0], q[0]) and \
               min(p[1], q[1]) <= r[1] <= max(p[1], q[1])
    return False


def _segments(run):
    return zip(run, run[1:])


def segments_cross(left, right) -> bool:
    """两条折线是否**真正**相交（共享端点不算）。

    与 `find_collisions` 的 `leader/line` 用同一份判据 —— 避让与判线必须是同一把尺子，
    否则字/符号会去躲一条检测器根本不看的线，或者反过来。
    """
    return any(_crosses(p, q, r, s) for p, q in _segments(left)
               for r, s in _segments(right))


def blocking_lines(items):
    """会把字压坏的那些线。

    躲线（`avoid_lines`）与判线（`find_collisions`）必须用**同一份**筛选，
    否则字会去躲一条检测器根本不看的线，或者反过来。
    """
    return [item for item in items
            if item.layer not in _TEXT_EXEMPT_LAYERS and len(item.points) >= 2]


def item_box(item) -> tuple[float, float, float, float]:
    """一条折线的外接矩形，用于**粗筛**。

    没有这一步时，每个候选字位都要与全部线段逐一做相交测试，一张带剖面线的图
    能把整条流水线拖慢一倍（实测单件 499 ms → 948 ms）。外接矩形先排除掉
    根本碰不到的线，判据一条没变，代价从 O(字位 × 全段) 降到 O(字位 × 候选线)。
    """
    xs = [p[0] for p in item.points]
    ys = [p[1] for p in item.points]
    return (min(xs), min(ys), max(xs), max(ys))


def _crosses_any(box, lines, owner) -> bool:
    for item, span in lines:
        if item.owner == owner or not _boxes_overlap(box, span):
            continue
        if any(_box_crossed_by(box, p, q) for p, q in _segments(item.points)):
            return True
    return False


def avoid_lines(texts, items) -> tuple[Text, ...]:
    """字被**别人**的线穿过时，沿自己的走向滑到空处。

    只沿自己的轴（横排左右滑、字头朝左的上下滑）—— 抬离尺寸线就等于把字挂到别处。
    滑不出去就留在原地：位置不动，冲突照样被 `find_collisions` 报出来，
    绝不悄悄挪一步假装干净。
    """
    lines = [(item, item_box(item)) for item in blocking_lines(items)]
    steps = int(LABEL_MAX_SHIFT / LABEL_AVOID_STEP)

    moved = []
    for text in texts:
        ux, uy = _text_direction(text)
        settled = text
        if _crosses_any(text_box(text), lines, text.owner):
            for step in range(1, steps + 1):
                for sign in (1.0, -1.0):
                    delta = sign * step * LABEL_AVOID_STEP
                    candidate = replace(text, x=text.x+delta*ux, y=text.y+delta*uy)
                    if not _crosses_any(text_box(candidate), lines, text.owner):
                        settled = candidate
                        break
                if settled is not text:
                    break
        moved.append(settled)
    return tuple(moved)


def find_collisions(drawing: Drawing) -> tuple[Collision, ...]:
    """列出这张图上不该发生的重叠。空元组 = 图面干净。"""
    found: list[Collision] = []
    texts = [(t, text_box(t)) for t in drawing.texts]

    for i, (a, box_a) in enumerate(texts):
        for b, box_b in texts[i + 1:]:
            if a.owner == b.owner:
                continue
            if _boxes_overlap(box_a, box_b):
                found.append(Collision("text/text", a.owner, b.owner,
                                       f"{a.content!r} × {b.content!r}"))

    lines = [(item, item_box(item)) for item in blocking_lines(drawing.items)]
    for text, box in texts:
        for item, span in lines:
            if item.owner == text.owner or not _boxes_overlap(box, span):
                continue
            for p, q in _segments(item.points):
                if _box_crossed_by(box, p, q):
                    found.append(Collision("text/line", text.owner, item.owner,
                                           f"{text.content!r} 被 {item.layer} 线穿过"))
                    break

    symbols = [item for item in drawing.items
               if item.layer in _SYMBOL_LAYERS and len(item.points) >= 2]
    dims = [item for item in drawing.items
            if item.layer == "DIM" and len(item.points) >= 2]
    for symbol in symbols:
        for dim in dims:
            if dim.owner == symbol.owner:
                continue
            if any(_crosses(p, q, r, s) for p, q in _segments(symbol.points)
                   for r, s in _segments(dim.points)):
                found.append(Collision("leader/line", symbol.owner, dim.owner,
                                       "符号引出线穿过尺寸线"))
    return tuple(found)


def _box_crossed_by(box, p, q) -> bool:
    x0, y0, x1, y1 = box
    corners = ((x0, y0), (x1, y0), (x1, y1), (x0, y1))
    if all(_side(p, q, c) > 0 for c in corners) or all(_side(p, q, c) < 0 for c in corners):
        return False
    for a, b in zip(corners, corners[1:] + corners[:1]):
        if _crosses(p, q, a, b):
            return True
    return _inside((p[0], p[1]), box) or _inside((q[0], q[1]), box)


def _side(p, q, c) -> float:
    return (q[0] - p[0]) * (c[1] - p[1]) - (q[1] - p[1]) * (c[0] - p[0])


def _inside(point, box) -> bool:
    return box[0] <= point[0] <= box[2] and box[1] <= point[1] <= box[3]

"""密集标注的最后落位：移动文字时补引线，测量端点和尺寸数值保持原样。"""
from dataclasses import replace
import heapq
import math

from drawform.output.collide import (_boxes_overlap, _box_crossed_by, _crosses_any,
                                    blocking_lines, item_box, text_box, _text_direction)
from drawform.output.drawing import CHAR_WIDTH, DrawingItem, SheetOverflow, _dimension


def annotation_box(text):
    """字位按基线给出；保守包含字面及既有碰撞字框，防止字的上半部穿线。"""
    x0, y0, x1, y1 = text_box(text)
    width = CHAR_WIDTH*text.height*len(text.content)
    left = -width/2 if text.anchor == 'middle' else -width if text.anchor == 'end' else 0
    ux, uy = _text_direction(text)
    corners = [(text.x+x*ux-y*uy, text.y+x*uy+y*ux)
               for x in (left, left+width) for y in (-text.height/2, text.height)]
    return (min(x0, *(p[0] for p in corners)), min(y0, *(p[1] for p in corners)),
            max(x1, *(p[0] for p in corners)), max(y1, *(p[1] for p in corners)))


def _route_clear(route, boxes):
    for a, c in zip(route, route[1:]):
        left, right = sorted((a[0], c[0]))
        bottom, top = sorted((a[1], c[1]))
        for box in boxes:
            if box[2] < left or box[0] > right or box[3] < bottom or box[1] > top:
                continue
            # 正交线沿字框边界经过也不是空白通道；一般相交判据不计共线边。
            if a[0] == c[0] or a[1] == c[1]:
                return False
            if _box_crossed_by(box, a, c):
                return False
    return True


def _visibility_route(anchor, ends, boxes, frame):
    """文字外沿组成有限正交网格，按长度和转弯代价寻找连续路径。"""
    x0, y0, x1, y1 = frame.inner
    ends = tuple(p for p in ends if frame.contains((*p, *p)))
    if not ends or not frame.contains((*anchor, *anchor)):
        return None
    xs = sorted({anchor[0], *(p[0] for p in ends), x0, x1,
                 *(x for b in boxes for x in (b[0]-.5, b[2]+.5) if x0 <= x <= x1)})
    ys = sorted({anchor[1], *(p[1] for p in ends), y0, y1,
                 *(y for b in boxes for y in (b[1]-.5, b[3]+.5) if y0 <= y <= y1)})
    start = (xs.index(anchor[0]), ys.index(anchor[1]), 0)
    targets = {(xs.index(x), ys.index(y)) for x, y in ends}
    free, edges = {}, {}

    def clear_point(i, j):
        key = (i, j)
        if key not in free:
            free[key] = not any(b[0] <= xs[i] <= b[2] and b[1] <= ys[j] <= b[3]
                                for b in boxes)
        return free[key]

    def estimate(i, j):
        return min(abs(xs[i]-x)+abs(ys[j]-y) for x, y in ends)

    if not clear_point(*start[:2]):
        return None
    costs, previous = {start: 0.0}, {}
    queue = [(estimate(*start[:2]), 0.0, start)]
    while queue:
        _score, cost, state = heapq.heappop(queue)
        if cost != costs[state]:
            continue
        i, j, direction = state
        if (i, j) in targets:
            run = []
            while True:
                run.append((xs[state[0]], ys[state[1]]))
                if state == start:
                    break
                state = previous[state]
            run.reverse()
            # 连续同向的小网格边合成一条线，保留真实拐点。
            result = [run[0]]
            for a, b, c in zip(run, run[1:], run[2:]):
                if (a[0] == b[0] == c[0]) or (a[1] == b[1] == c[1]):
                    continue
                result.append(b)
            if len(run) > 1:
                result.append(run[-1])
            return tuple(result)
        for di, dj, axis in ((-1,0,1),(1,0,1),(0,-1,2),(0,1,2)):
            ni, nj = i+di, j+dj
            if not (0 <= ni < len(xs) and 0 <= nj < len(ys)) or not clear_point(ni, nj):
                continue
            edge = tuple(sorted(((i,j), (ni,nj))))
            if edge not in edges:
                edges[edge] = _route_clear(((xs[i],ys[j]), (xs[ni],ys[nj])), boxes)
            if not edges[edge]:
                continue
            next_state = (ni, nj, axis)
            candidate = cost+abs(xs[ni]-xs[i])+abs(ys[nj]-ys[j])
            if direction and direction != axis:
                candidate += 3.0
            if candidate < costs.get(next_state, math.inf):
                costs[next_state] = candidate
                previous[next_state] = state
                heapq.heappush(queue, (candidate+estimate(ni,nj), candidate, next_state))
    return None


def leader_route(anchor, box, obstacles, frame):
    """可从字框四侧接入；左侧被相邻文字占据时不能把整条尺寸误判为放不下。"""
    cx, cy = (box[0]+box[2])/2, (box[1]+box[3])/2
    ends = ((box[0]-1.5,cy), (box[2]+1.5,cy), (cx,box[1]-1.5), (cx,box[3]+1.5))
    for end in sorted(ends, key=lambda p: math.dist(anchor,p)):
        routes = [(anchor,end), (anchor,(anchor[0],end[1]),end),
                  (anchor,(end[0],anchor[1]),end)]
        for offset in range(6,121,6):
            for sign in (1,-1):
                x,y = anchor[0]+sign*offset, anchor[1]+sign*offset
                routes.extend(((anchor,(anchor[0],y),(end[0],y),end),
                               (anchor,(x,anchor[1]),(x,end[1]),end)))
        for route in routes:
            if (all(frame.contains((x,y,x,y)) for x,y in route)
                    and _route_clear(route, [box,*obstacles])):
                return route
    return _visibility_route(anchor, ends, [box,*obstacles], frame)


def _attachment(dim, origin):
    ox,oy = origin
    if dim.base is None:
        tip = (ox+dim.p2[0],oy+dim.p2[1])
        dx,dy = dim.p2[0]-dim.p1[0],dim.p2[1]-dim.p1[1]
        norm = math.hypot(dx,dy) or 1
        return tip,(tip[0]+dx/norm*3,tip[1]+dy/norm*3)
    lines, _heads, _texts = _dimension(dim, origin)
    a, b = lines[0]
    return None,((a[0]+b[0])/2,(a[1]+b[1])/2)


def _attachment_choices(dim, origin, anchor):
    """Keep radial arrows fixed; linear labels may connect along their real line."""
    if dim.base is None:
        return (anchor,)
    lines, _heads, _texts = _dimension(dim, origin)
    a, b = lines[0]
    if math.dist(a, b) <= 1e-6:
        return (anchor,)
    return (anchor, *(tuple(a[i]+fraction*(b[i]-a[i]) for i in (0, 1))
                      for fraction in (.25, .75, .125, .875, .375, .625)))


def settle_annotations(texts, items, dimensions, origins, frame, fixed=()):
    initial = tuple(texts)
    texts, items = list(texts), list(items)
    by_owner = {f'dim:{d.source or d.text}': d for d in dimensions}
    fixed = set(fixed)
    # 以距离排序，先找附近空白；搜索边界就是当前图框，不靠无限挪动藏掉冲突。
    offsets = sorted(((x, y) for x in range(-120, 121, 6) for y in range(-120, 121, 6)
                      if x or y), key=lambda p: (p[0]*p[0]+p[1]*p[1], abs(p[1]), p))
    relocated = []
    lines = [(item, item_box(item)) for item in blocking_lines(items)]
    for index, text in enumerate(texts):
        if text.owner not in by_owner or text.owner in fixed:
            continue
        others = [t for i, t in enumerate(texts) if i != index and t.owner != text.owner]
        boxes = [annotation_box(t) for t in others]
        dim = by_owner[text.owner]
        _tip, anchor = _attachment(dim, origins[dim.view])

        def clear(candidate):
            box = annotation_box(candidate)
            if candidate is not text and (box[0]-.5 <= anchor[0] <= box[2]+.5
                                           and box[1]-.5 <= anchor[1] <= box[3]+.5):
                return False
            return (frame.contains(box) and not any(_boxes_overlap(box, b, .8) for b in boxes)
                    and not _crosses_any(box, lines, '' if dim.base is None else text.owner))

        if clear(text):
            continue
        for dx, dy in offsets:
            candidate = replace(text, x=text.x+dx, y=text.y+dy)
            if clear(candidate):
                texts[index] = candidate
                relocated.append(index)
                break
        else:
            raise SheetOverflow(f'{frame.sheet} 标注“{text.content}”找不到无堆叠的字位，未隐藏或改写尺寸')

    # 先把全部字移开，再接引线；否则旧字挡住箭头出口，会误判所有路径都走不通。
    for index in relocated:
        text = texts[index]
        dim = by_owner[text.owner]
        tip, anchor = _attachment(dim,origins[dim.view])
        boxes = [annotation_box(t) for i,t in enumerate(texts) if i != index and t.owner != text.owner]
        # A relocated dimension leader must also keep clear of datum/section
        # symbols already placed; checking only text can reintroduce crossings.
        for item in items:
            if item.layer in ('DATUM', 'SECTION'):
                x0,y0,x1,y1 = item_box(item)
                boxes.append((x0-.2,y0-.2,x1+.2,y1+.2))
        box = annotation_box(text)
        if tip is not None and not _route_clear((tip, anchor), boxes):
            raise SheetOverflow(f'{frame.sheet} 标注“{text.content}”箭头出口被文字阻挡，未穿字连线')
        anchors = _attachment_choices(dim, origins[dim.view], anchor)

        def connect(candidate_box):
            for point in anchors:
                # A trapped midpoint cannot be rescued by relocating text. A
                # different attachment must still be on the same finite native
                # dimension line and pass every existing obstacle check.
                if any(b[0] <= point[0] <= b[2] and b[1] <= point[1] <= b[3] for b in boxes):
                    continue
                route = leader_route(point, candidate_box, boxes, frame)
                if route is not None:
                    return route
            return None

        run = connect(box)
        if run is None:
            # An empty slot on the far side of a text wall may be unreachable.
            # Keep the already settled other labels; search the same finite area
            # for a slot with both free text space and a continuous leader.
            current_lines = [(item, item_box(item)) for item in blocking_lines(items)]
            for dx, dy in ((0, 0), *offsets):
                candidate = replace(initial[index], x=initial[index].x+dx, y=initial[index].y+dy)
                candidate_box = annotation_box(candidate)
                if (not frame.contains(candidate_box)
                        or any(_boxes_overlap(candidate_box, b, .8) for b in boxes)
                        or (candidate_box[0]-.5 <= anchor[0] <= candidate_box[2]+.5
                            and candidate_box[1]-.5 <= anchor[1] <= candidate_box[3]+.5)
                        or _crosses_any(candidate_box, current_lines, '' if dim.base is None else text.owner)):
                    continue
                run = connect(candidate_box)
                if run is not None:
                    text = texts[index] = candidate
                    break
        if run is None:
            raise SheetOverflow(f'{frame.sheet} 标注“{text.content}”引线被文字阻挡，未把文字与尺寸脱开')
        if dim.base is None:
            items = [item for item in items if item.owner != text.owner]
            run = (tip, *run)
        items.append(DrawingItem(text.owner, 'DIM', run))
    return texts, items

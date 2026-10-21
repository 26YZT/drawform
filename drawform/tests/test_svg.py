"""SVG 渲染器：浏览器里看到的必须就是那张图，不是 ezdxf 的近似预览。

注意 ElementTree 解析带 xmlns 的 SVG 时标签会变成 `{namespace}g`，
所以这里统一用 `_local()` 取标签名 —— 上一版测试就是因为漏了这一步，
把"渲染器没输出"这个假结论写得像真的。
"""
from collections import Counter
from dataclasses import replace
import re
from xml.etree import ElementTree as ET

import pytest

from drawform.dimensioning.place import OFFSET, place_scheme
from drawform.dimensioning.scheme import derive_scheme
from drawform.geometry.centerlines import centerlines_for
from drawform.geometry.features import extract_features
from drawform.geometry.layout import layout_views
from drawform.geometry.loader import import_step
from drawform.geometry.projection import FRONT, LEFT, TOP, project_view
from drawform.output.drawing import sheet_views
from drawform.output.sheet import make_frame
from drawform.output.svg import STYLES, escape, write_svg


def _points(markup):
    """图上落地的所有坐标点（SVG 纸面坐标，y 向下）。"""
    return [(float(x), float(y))
            for match in re.finditer(r"(-?\d+\.\d{3}),(-?\d+\.\d{3})", markup)
            for x, y in [match.groups()]]


def _local(element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def _groups(root):
    return {g.get("id"): g for g in root.iter() if _local(g) == "g"}


def _placed(bearing_seat_step, with_centerlines=True):
    model = import_step(bearing_seat_step)
    features = extract_features(model.shape, model.bbox)
    projections = {p.direction.name: p
                   for p in (project_view(model.shape, d) for d in (FRONT, TOP, LEFT))}
    placed = list(layout_views(tuple(projections.values())))
    if with_centerlines:
        placed = [replace(v, centerlines=centerlines_for(v.projection, features))
                  for v in placed]
    return model, features, projections, placed


def _sheet(tmp_path, bearing_seat_step, name="sheet.svg"):
    """走一遍真实管线：投影 → 排版 → 方案 → 落位 → 渲染。返回图与它的输入。"""
    model, features, projections, placed = _placed(bearing_seat_step)
    dims = place_scheme(derive_scheme(model, features), projections)
    markup = write_svg(placed, tmp_path / name, dimensions=dims).read_text(encoding="utf-8")
    assert dims, "夹具没落位任何尺寸，这条测试测不到东西"
    return placed, dims, markup


@pytest.fixture
def rendered(tmp_path, bearing_seat_step):
    """一张真实的图：视图 + 尺寸，不是手搭的样例标记。"""
    _placed_, _dims, markup = _sheet(tmp_path, bearing_seat_step)
    return ET.fromstring(markup), markup


def test_sheet_is_a_real_paper_size(rendered):
    root, _ = rendered
    assert root.get("width") == "420mm" and root.get("height") == "297mm"
    assert root.get("viewBox") == "0 0 420 297"


def test_gb_t_line_weights_survive_into_the_markup(rendered):
    """粗实线 0.50、细线 0.25 —— 线宽比 2:1 必须写在标记里，不能靠看图软件猜。"""
    groups, _ = _groups(rendered[0]), rendered[1]
    assert groups["VISIBLE"].get("stroke-width") == "0.5"
    assert groups["HIDDEN"].get("stroke-width") == "0.25"
    assert groups["CENTER"].get("stroke-width") == "0.25"


def test_dash_patterns_are_the_mm_gbt_ones_not_inch_linetypes(rendered):
    """虚线 8/1、点划线 18/3/点/3。AutoCAD 内置 DASHED/CENTER 是英寸口径，毫米图上会连成实线。"""
    _, markup = rendered
    assert 'stroke-dasharray="8 1"' in markup
    assert 'stroke-dasharray="18 3 0.75 3"' in markup


def test_every_projected_curve_is_drawn(bearing_seat_step, tmp_path):
    """画出来的条数必须等于投影出来的条数 —— 不许静默丢边。

    阈值不写魔法数字：上一版这里拍了个 ">40" 结果测的是我自己的猜测，
    真实数量只有 24，报出来的却是"渲染器漏画"。
    """
    _model, _features, _projections, placed = _placed(bearing_seat_step)
    root = ET.parse(write_svg(placed, tmp_path / "count.svg")).getroot()

    drawn = len(list(_groups(root)["VISIBLE"].iter())) - 1
    expected = sum(len(v.projection.visible) for v in placed)

    assert expected, "夹具本身没投影出可见轮廓，那这条测试测不到东西"
    assert drawn == expected


def test_lettering_height_is_the_gbt_minimum(rendered):
    """字高 3.5 mm。此前踩过 dimtxt=0.25 的英寸默认值 —— 那是 0.25 mm 高的字。"""
    _, markup = rendered
    assert 'font-size="3.5"' in markup


def test_text_is_escaped_not_injected(tmp_path, bearing_seat_step):
    """图上的文字来自模型与客户模板，尖括号与 & 必须转义。"""
    _model, _features, _projections, placed = _placed(bearing_seat_step)
    markup = write_svg(placed, tmp_path / "esc.svg",
                       notes=("<script> & co",)).read_text(encoding="utf-8")

    assert "<script>" not in markup
    assert "&lt;script&gt; &amp; co" in markup
    assert escape("<a>&b") == "&lt;a&gt;&amp;b"


def test_y_axis_is_flipped_onto_paper_coordinates(tmp_path, bearing_seat_step):
    """图纸 y 向上、SVG y 向下。不翻转的话上下视图会互换位置。"""
    _model, _features, _projections, placed = _placed(bearing_seat_step,
                                                      with_centerlines=False)
    out = write_svg(placed, tmp_path / "flip.svg")
    markup = out.read_text(encoding="utf-8")

    ys = [float(v.split(",")[1]) for v in
          markup.split('<g id="VISIBLE"')[1].split('points="')[1].split('"')[0].split()]
    assert max(ys) <= 297.0 and min(ys) >= 0.0, "有轮廓落到纸面之外"


def test_nothing_is_drawn_outside_the_frame(tmp_path, bearing_seat_step):
    """排版层从 (0,0) 起排，第一角俯视图的 origin 直接是负的 —— 渲染器不搬进图框，
    画就压在纸外面。实测过：浏览器里是一张空白，坐标最远到 y=344（纸高 297）。
    """
    _placed_, _dims, markup = _sheet(tmp_path, bearing_seat_step, "frame.svg")
    x0, y0, x1, y1 = make_frame("A3").inner

    off = [(x, y) for x, y in _points(markup)
           if not (x0 <= x <= x1 and y0 <= y <= y1)]
    assert off == [], f"{len(off)} 个点落在图框之外"


def test_centerlines_are_drawn_on_their_own_view(tmp_path, bearing_seat_step):
    """中心线属于视图：漏掉 view.origin，几张视图的中心线会全部堆到图纸原点。

    part_0042 实测就是这个形状 —— 轮廓旁边没有中心线，图框外多出一堆杂线。
    浏览器里肉眼一眼看得见，测试却差点放过去。
    """
    _model, _features, _projections, placed = _placed(bearing_seat_step)
    markup = write_svg(placed, tmp_path / "cl.svg").read_text(encoding="utf-8")

    def span(layer):
        body = markup.split(f'<g id="{layer}"')[1].split("</g>")[0]
        xs = [x for x, _ in _points(body)]
        return min(xs), max(xs)

    left, right = span("VISIBLE")
    cl_left, cl_right = span("CENTER")
    overhang = 5.0          # GB/T 4457.4：中心线超出轮廓 2–5 mm
    assert cl_left >= left - overhang and cl_right <= right + overhang, \
        f"中心线横向落在 {cl_left:.0f}..{cl_right:.0f}，轮廓在 {left:.0f}..{right:.0f}"


def _snap(value: float) -> float:
    return round(value, 3)


def _polylines(markup, layer):
    """某一层上画出来的折线（纸面坐标）。"""
    body = markup.split(f'<g id="{layer}"')[1].split("</g>")[0]
    return [[(_snap(float(x)), _snap(float(y)))
             for x, y in (point.split(",") for point in run.split())]
            for run in re.findall(r'points="([^"]+)"', body)]


def _view_of(placed, key, dimensions=()):
    return next(v for v in sheet_views(placed, dimensions=dimensions)
                if (v.label or v.projection.direction.name) == key)


def test_every_placed_dimension_is_written_on_the_sheet(tmp_path, bearing_seat_step):
    """落位了不等于画出来了。渲染器不接 dimensions，客户拿到的就是这张没有数字的图。"""
    _placed_, dims, markup = _sheet(tmp_path, bearing_seat_step, "dims.svg")

    for text, count in Counter(dim.text for dim in dims).items():
        assert markup.count(f">{escape(text)}<") == count, f"「{text}」没画到图上"


def test_linear_dimension_line_sits_on_the_base_declared_by_placement(tmp_path, bearing_seat_step):
    """尺寸线的位置由落位层算好的偏移带决定，渲染器不能再自己算一套 ——
    两边各算一次，DXF 与网页就会画出两张不一样的图。
    """
    placed, dims, markup = _sheet(tmp_path, bearing_seat_step, "linear.svg")
    dim = next(d for d in dims if d.kind == "linear"
               and abs(d.p1[1] - d.p2[1]) < 1e-9)
    view = _view_of(placed, dim.view, dims)
    height = 297.0
    line_y = _snap(height - (dim.base[1] + view.origin[1]))

    expected = [(_snap(dim.p1[0] + view.origin[0]), line_y),
                (_snap(dim.p2[0] + view.origin[0]), line_y)]
    assert expected in _polylines(markup, "DIM"), \
        f"没画出 {expected}，DIM 层只有 {len(_polylines(markup, 'DIM'))} 条线"


def _texts(markup):
    root = ET.fromstring(markup)
    return [(t.text or "", t.get("x"), t.get("y"), t.get("transform") or "")
            for t in root.iter() if _local(t) == "text"]


def test_vertical_dimension_lettering_reads_upwards(tmp_path, bearing_seat_step):
    """垂直尺寸的字按 GB/T 4458.1 字头朝左注写。

    横排会把 6 位数压成约 15 mm 宽，而相邻偏移带只隔 8 mm —— part_0042 左边
    五条定位尺寸实测糊成一串"43.671|18.671"，车间里读不出来。
    """
    placed, dims, markup = _sheet(tmp_path, bearing_seat_step, "vertical.svg")
    vertical = [d for d in dims if d.base is not None
                and abs(d.p1[0] - d.p2[0]) < 1e-9]
    assert vertical, "夹具没有竖直方向的尺寸，这条测试测不到东西"
    dim = next(d for d in vertical
               if [x.text for x in dims].count(d.text) == 1)

    entries = [(x, tr) for content, x, _y, tr in _texts(markup) if content == dim.text]
    assert len(entries) == 1, f"「{dim.text}」在图上出现 {len(entries)} 次"
    text_x, transform = entries[0]
    assert transform.startswith("rotate(-90"), f"字头方向不对：{transform!r}"

    view = _view_of(placed, dim.view, dims)
    line_x = float(text_x)
    lane_x = view.origin[0] + dim.base[0]
    assert abs(line_x - lane_x) <= OFFSET, \
        f"字离尺寸线 {abs(line_x - lane_x):.1f} mm，压到相邻偏移带上了"


def test_technical_requirements_read_top_down(tmp_path, bearing_seat_step):
    """技术要求写在图框左下角，但顺序必须按阅读顺序自上而下 ——
    倒着排会把「一般公差」压在「视图按第一角画法」下面，读起来像两条不相干的注记。
    """
    _model, _features, _projections, placed = _placed(bearing_seat_step)
    markup = write_svg(placed, tmp_path / "notes.svg",
                       notes=("第一行", "第二行", "第三行")
                       ).read_text(encoding="utf-8")

    rows = {content: float(y) for content, _x, y, _t in _texts(markup)
            if content.startswith("第")}
    assert rows["第一行"] < rows["第二行"] < rows["第三行"], \
        f"SVG 的 y 向下，自上而下读就是数值递增：{rows}"


def test_a_sheet_too_small_is_refused_not_clipped(tmp_path, bearing_seat_step):
    """装不下必须停下来。默默裁掉一半的图会照常出文件、照常通过所有别的检查，
    然后在车间里才发现少了一个视图。
    """
    from drawform.output.drawing import SheetOverflow

    model = import_step(bearing_seat_step)
    features = extract_features(model.shape, model.bbox)
    projections = [project_view(model.shape, d) for d in (FRONT, TOP, LEFT)]
    # 视图间距是排版层的真实参数：摊到 500 mm 宽，A4 的可用区只有 262 mm。
    placed = layout_views(tuple(projections), gap=200.0)

    with pytest.raises(SheetOverflow) as caught:
        write_svg(placed, tmp_path / "small.svg", sheet="A4")

    assert "A4" in str(caught.value)

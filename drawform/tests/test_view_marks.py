"""视图标注：剖视图必须让读者看得出它是剖的、从哪剖、往哪看。

没有 A-A 和剖切位置线的剖视图是有害的 —— 它画得像一张普通视图，
读者会按外形去理解内部结构。
"""

import ezdxf
import pytest

from drawform.geometry.centerlines import centerlines_for
from drawform.geometry.layout import PlacedView, layout_views
from drawform.geometry.loader import import_step
from drawform.geometry.projection import FRONT, TOP, LEFT, project_view
from drawform.geometry.section import section_view
from drawform.output.drawing import SectionMark
from drawform.output.dxf import write_dxf


def _views_with_section(model):
    projections = tuple(project_view(model.shape, d) for d in (FRONT, TOP, LEFT))
    placed = list(layout_views(projections))
    section = section_view(model.shape, FRONT, axis="y", at=25.0)
    placed.append(PlacedView(projection=section.projection, origin=(300.0, 0.0),
                             hatch=section.hatch, label="A-A"))
    return placed


def test_section_view_is_named_on_the_sheet(bearing_seat_step, tmp_path):
    model = import_step(bearing_seat_step)
    out = tmp_path / "marks.dxf"

    write_dxf(_views_with_section(model), out)

    doc = ezdxf.readfile(str(out))
    texts = [e.dxf.text for e in doc.modelspace().query("TEXT")]

    assert "A-A" in texts, f"剖视图没有名字，图上只有：{texts}"


def test_view_name_is_centered_under_its_own_view(bearing_seat_step, tmp_path):
    """名字必须落在自己那张视图的正下方，落到隔壁视图上比不标更糟。"""
    model = import_step(bearing_seat_step)
    views = _views_with_section(model)
    out = tmp_path / "pos.dxf"

    write_dxf(views, out)

    doc = ezdxf.readfile(str(out))
    label = next(e for e in doc.modelspace().query('TEXT[layer=="TITLE"]')
                 if e.dxf.text == "A-A")
    section = views[-1]
    xs = [p[0] for c in tuple(section.projection.visible) + tuple(section.projection.hidden)
          for p in c.sample()]
    center = (min(xs) + max(xs)) / 2 + section.origin[0]

    xs = sorted(x + section.origin[0] for x in xs)
    left, right = xs[0], xs[-1]
    others = [(min(p[0] for c in tuple(v.projection.visible) + tuple(v.projection.hidden)
                   for p in c.sample()) + v.origin[0],
               max(p[0] for c in tuple(v.projection.visible) + tuple(v.projection.hidden)
                   for p in c.sample()) + v.origin[0])
              for v in views[:-1]]

    assert left <= label.dxf.insert.x <= right, "视图名落到自己的范围之外了"
    assert all(abs(label.dxf.insert.x - center) < abs(label.dxf.insert.x - (o[0] + o[1]) / 2)
               for o in others), "视图名离别的视图更近，会被读成别人的名字"
    assert label.dxf.insert.y < section.origin[1], "视图名应在视图下方"


def test_cutting_plane_is_traced_on_the_view_it_is_perpendicular_to(bearing_seat_step, tmp_path):
    """剖平面 y=25 垂直于俯视图的投影面，所以位置线画在俯视图上。

    画在主视图上是错的：剖平面与主视图投影面平行，在主视图里根本不成线。
    """
    model = import_step(bearing_seat_step)
    out = tmp_path / "trace.dxf"

    write_dxf(_views_with_section(model), out,
              section_marks=[SectionMark("top", 25.0, "A", (0.0, 1.0))])

    doc = ezdxf.readfile(str(out))
    marks = list(doc.modelspace().query('LINE[layer=="SECTION"]'))

    assert marks, "剖切位置线一条都没画"
    assert any("A" == e.dxf.text for e in doc.modelspace().query('TEXT[layer=="SECTION"]')), \
        "剖切符号没有字母"


def test_marks_do_not_break_the_audit(bearing_seat_step, tmp_path):
    model = import_step(bearing_seat_step)
    out = tmp_path / "audit.dxf"

    write_dxf(_views_with_section(model), out,
              section_marks=[SectionMark("top", 25.0, "A", (0.0, 1.0))])

    assert len(ezdxf.readfile(str(out)).audit().errors) == 0


def test_cutting_symbol_shows_which_way_the_section_is_viewed(bearing_seat_step, tmp_path):
    """只有位置线没有方向箭头，读者不知道留哪一半、往哪边看 —— 图仍然会被误读。"""
    import math

    model = import_step(bearing_seat_step)
    out = tmp_path / "arrows.dxf"

    write_dxf(_views_with_section(model), out,
              section_marks=[SectionMark("top", 25.0, "A", (0.0, 1.0))])

    doc = ezdxf.readfile(str(out))
    lines = list(doc.modelspace().query('LINE[layer=="SECTION"]'))
    arrows = [l for l in lines
              if abs(l.dxf.start.x - l.dxf.end.x) < 1e-9 and l.dxf.end.y > l.dxf.start.y]
    assert len(arrows) >= 2, f"两端各应有一个投影方向箭头，实际 {len(arrows)} 个"
    assert all(l.dxf.end.y - l.dxf.start.y >= 4.0 for l in arrows), "箭头太短，指不出方向"


def test_trace_is_perpendicular_to_the_view_direction(bearing_seat_step, tmp_path):
    """位置线与方向向量必须垂直：方向 (0,1) 时位置线是水平的。"""
    model = import_step(bearing_seat_step)
    views = _views_with_section(model)
    top = next(v for v in views if v.projection.direction.name == "top")
    out = tmp_path / "perp.dxf"

    write_dxf(views, out, section_marks=[SectionMark("top", 25.0, "A", (0.0, 1.0))])

    doc = ezdxf.readfile(str(out))
    lines = list(doc.modelspace().query('LINE[layer=="SECTION"]'))
    traces = [l for l in lines if abs(l.dxf.start.y - l.dxf.end.y) < 1e-9
              and abs(l.dxf.start.x - l.dxf.end.x) > 10.0]

    assert traces, "找不到水平的位置线"
    assert traces[0].dxf.start.y == pytest.approx(top.origin[1] + 25.0, abs=1e-6), \
        "位置线没落在剖切平面上"


def test_arrows_sit_on_the_view_being_marked_not_at_the_origin(bearing_seat_step, tmp_path):
    """箭头必须落在被标注的那张视图上。

    早先箭头用了视图的局部坐标没加 origin，方向和长度都对，位置却整体偏到原点，
    只查方向/长度的断言抓不到。
    """
    model = import_step(bearing_seat_step)
    views = _views_with_section(model)
    top = next(v for v in views if v.projection.direction.name == "top")
    out = tmp_path / "where.dxf"

    write_dxf(views, out, section_marks=[SectionMark("top", 25.0, "A", (0.0, 1.0))])

    doc = ezdxf.readfile(str(out))
    arrows = [e for e in doc.modelspace().query('LINE[layer=="SECTION"]')
              if abs(e.dxf.start.x - e.dxf.end.x) < 1e-9]

    # 必须查 y：俯视图按投影对齐规则 origin.x 恰为 0，只查 x 的话丢掉 origin 也看不出来
    ys = [p[1] for c in tuple(top.projection.visible) + tuple(top.projection.hidden)
          for p in c.sample()]
    assert top.origin[1] != 0.0, "夹具的俯视图 origin 在 y 上没偏移，这条断言会空转"
    below, above = min(ys) + top.origin[1], max(ys) + top.origin[1]
    for arrow in arrows:
        assert below - 10 <= arrow.dxf.start.y <= above + 10, \
            (f"箭头落在 y={arrow.dxf.start.y:.1f}，而被标注的视图纵跨 "
             f"{below:.1f}..{above:.1f}（origin={top.origin[1]:.1f}）")

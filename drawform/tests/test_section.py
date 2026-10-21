import pytest

from drawform.domain.curves import Line2D
from drawform.geometry.loader import import_step
from drawform.geometry.projection import FRONT, project_view
from drawform.geometry.section import section_view


def _vertical_xs(curves):
    return {round(c.start[0], 3) for c in curves
            if isinstance(c, Line2D) and abs(c.start[0] - c.end[0]) < 1e-6}


def test_cut_plane_turns_hidden_bore_walls_into_visible_outlines(bearing_seat_step):
    """剖视图存在的理由：沿孔轴剖开后，孔壁从虚线变成粗实线。

    轴承座的 Ø20 通孔与两个 Ø9 螺栓孔轴心都在 Y=25，沿 Y=25 剖切应同时剖到三个孔。
    """
    model = import_step(bearing_seat_step)

    section = section_view(model.shape, FRONT, axis="y", at=25.0)

    xs = _vertical_xs(section.projection.visible)
    assert {30.0, 50.0} <= xs, f"Ø20 孔壁未成为可见轮廓，实际 {sorted(xs)}"
    assert {10.5, 19.5, 60.5, 69.5} <= xs, f"Ø9 螺栓孔壁未成为可见轮廓，实际 {sorted(xs)}"


def test_no_bore_wall_stays_hidden_after_the_cut(bearing_seat_step):
    """剖切要解决的正是「内部结构只能靠虚线表达」：孔壁不该再以虚线出现。

    实测剖开后仍剩 2 条隐藏线，那是凸台后方被挡住的远端面上沿（z=12 处），
    属于真实遮挡，不是缺陷 —— 因此断言针对竖直孔壁而不是「隐藏线清零」。
    """
    model = import_step(bearing_seat_step)

    section = section_view(model.shape, FRONT, axis="y", at=25.0)

    walls = [c for c in section.projection.hidden
             if isinstance(c, Line2D) and abs(c.start[0] - c.end[0]) < 1e-6]
    assert walls == [], f"剖切后仍有孔壁以虚线表达：{walls}"


def test_partially_covered_hidden_line_is_trimmed(bearing_seat_step):
    """隐藏线只被遮住一半时，应该裁掉被遮住的那半，而不是整条留着压住实线。

    剖切把可见上沿打断成几段，远端面那条整段上沿因此不再与任何一条可见线一一对应；
    它只有凸台以外的那两截是真的被实线压住的。
    """
    model = import_step(bearing_seat_step)

    section = section_view(model.shape, FRONT, axis="y", at=25.0)

    top_edges = [c for c in section.projection.hidden
                 if isinstance(c, Line2D)
                 and abs(c.start[1] - 12.0) < 1e-6 and abs(c.end[1] - 12.0) < 1e-6]

    assert len(top_edges) == 1, f"z=12 处应有 1 条被裁短的隐藏上沿，实际 {top_edges}"
    assert sorted([top_edges[0].start[0], top_edges[0].end[0]]) == pytest.approx([20.0, 60.0])


def test_cut_face_is_hatched_at_45_degrees(bearing_seat_step):
    """GB/T 4457.4：剖切面用与主要轮廓成 45° 的等距细实线填充，没有剖面线就不是剖视图。"""
    model = import_step(bearing_seat_step)

    section = section_view(model.shape, FRONT, axis="y", at=25.0)

    assert section.hatch, "剖切面没有生成剖面线"
    for line in section.hatch:
        dx = abs(line.end[0] - line.start[0])
        dy = abs(line.end[1] - line.start[1])
        assert dx == pytest.approx(dy, abs=1e-6), f"剖面线不与轮廓成 45°：{line}"
        assert dx > 1e-6, "剖面线出现零长度段"


def test_hatch_spacing_is_uniform(bearing_seat_step):
    model = import_step(bearing_seat_step)

    section = section_view(model.shape, FRONT, axis="y", at=25.0)

    # 45° 线的法向坐标即 (y - x)/√2，用它给剖面线排序后相邻间距应一致
    offsets = sorted({round((l.start[1] - l.start[0]) / 2 ** 0.5, 6) for l in section.hatch})
    gaps = [b - a for a, b in zip(offsets, offsets[1:])]
    assert len(gaps) >= 2, f"剖面线不足三条，无法检查间距：{len(section.hatch)} 条"
    assert max(gaps) - min(gaps) < 1e-6, f"剖面线间距不等：{[round(g, 4) for g in gaps]}"


def test_hatch_does_not_cross_the_cut_bore(bearing_seat_step):
    """Ø20 通孔与 Ø32 沉孔处是空的，剖面线必须留白 —— 穿过去就等于把孔画成了实心。"""
    model = import_step(bearing_seat_step)

    section = section_view(model.shape, FRONT, axis="y", at=25.0)

    assert section.hatch, "没有剖面线时本条断言会空转，先确认已生成"
    voids = (("主孔", 30.0, 50.0, 0.0, 46.0), ("沉孔", 24.0, 56.0, 46.0, 52.0))
    for name, x_lo, x_hi, z_lo, z_hi in voids:
        inside = [l for l in section.hatch
                  if x_lo < (l.start[0] + l.end[0]) / 2 < x_hi
                  and z_lo < (l.start[1] + l.end[1]) / 2 < z_hi]
        assert not inside, f"剖面线穿过{name}（{x_lo}<{x}<{x_hi}, {z_lo}<{z}<{z_hi}）：{inside[:2]}"


def test_section_view_differs_from_the_unsectioned_view(bearing_seat_step):
    """防呆：若剖切实际没生效，输出的边数会与不剖时一致。"""
    model = import_step(bearing_seat_step)

    section = section_view(model.shape, FRONT, axis="y", at=25.0)
    plain = project_view(model.shape, FRONT)

    assert len(section.projection.visible) > len(plain.visible), (
        "剖切后可见边没有增加，剖切未生效"
    )

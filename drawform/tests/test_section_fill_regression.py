"""剖面线必须覆盖材料全区，不能用非空、斜率和间距冒充完整填充。"""
import json
import math
import xml.etree.ElementTree as ET

import ezdxf
import pytest
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

from drawform.domain.curves import FRONT, TOP, LEFT
from drawform.geometry.section import section_view
from drawform.pipeline import render_part
from tests.conftest import _write_step


def _filled_area(section, spacing):
    assert section.hatch, '没有剖面线时面积/孔洞检查不能空转'
    # 等距平行线的长度积分应接近独立解析面积；允许离散线距的边界误差。
    return sum(math.dist(line.start, line.end) for line in section.hatch)*spacing


def _distance_to_segment(point, start, end):
    dx, dy = end[0]-start[0], end[1]-start[1]
    t = max(0, min(1, ((point[0]-start[0])*dx+(point[1]-start[1])*dy)/(dx*dx+dy*dy)))
    return math.dist(point, (start[0]+t*dx, start[1]+t*dy))


@pytest.mark.parametrize('origin', [(0, 0, 0), (123, -44, 20)])
@pytest.mark.parametrize('spacing', [4, 40])
@pytest.mark.parametrize('direction,axis,at,area', [
    (FRONT, 'y', 250, 800*150), (TOP, 'z', 75, 800*500), (LEFT, 'x', 400, 500*150),
])
def test_rectangular_cut_is_filled_completely_for_each_direction_and_spacing(origin, spacing, direction, axis, at, area):
    shape = BRepPrimAPI_MakeBox(gp_Pnt(*origin), 800, 500, 150).Shape()
    section = section_view(shape, direction, axis, at+origin['xyz'.index(axis)], hatch_spacing=spacing)
    assert _filled_area(section, spacing) == pytest.approx(area, rel=.03)


def test_annular_section_fills_the_ring_and_keeps_the_whole_bore_blank():
    axis = gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1))
    shape = BRepAlgoAPI_Cut(BRepPrimAPI_MakeCylinder(axis, 20, 40).Shape(),
                           BRepPrimAPI_MakeCylinder(axis, 8, 40).Shape()).Shape()
    section = section_view(shape, TOP, 'z', 20, hatch_spacing=.5)
    assert _filled_area(section, .5) == pytest.approx(math.pi*(20**2-8**2), rel=.02)
    for line in section.hatch:
        assert _distance_to_segment((0, 0), line.start, line.end) >= 8-.03
        assert max(math.hypot(*line.start), math.hypot(*line.end)) <= 20+.03


def test_three_closed_holes_remain_blank_in_a_fully_hatched_plate():
    shape = BRepPrimAPI_MakeBox(80, 50, 12).Shape()
    centers = [(15, 25), (40, 25), (65, 25)]
    for x, y in centers:
        tool = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(x, y, -1), gp_Dir(0, 0, 1)), 5, 14).Shape()
        shape = BRepAlgoAPI_Cut(shape, tool).Shape()
    section = section_view(shape, TOP, 'z', 6, hatch_spacing=.5)
    assert _filled_area(section, .5) == pytest.approx(80*50-3*math.pi*25, rel=.01)
    assert all(_distance_to_segment(center, line.start, line.end) >= 5-.03
               for center in centers for line in section.hatch)


def test_two_separate_cut_faces_fill_both_arms_without_bridging_the_gap():
    block = BRepPrimAPI_MakeBox(80, 20, 40).Shape()
    opening = BRepPrimAPI_MakeBox(gp_Pnt(20, -1, -1), 40, 22, 31).Shape()
    shape = BRepAlgoAPI_Cut(block, opening).Shape()  # 上方横梁连接两臂，仍为一个实体
    section = section_view(shape, TOP, 'z', 10, hatch_spacing=.5)
    assert _filled_area(section, .5) == pytest.approx(2*20*20, rel=.02)
    for line in section.hatch:
        assert max(line.start[0], line.end[0]) <= 20+1e-6 or min(line.start[0], line.end[0]) >= 60-1e-6
    for side in (lambda line: max(line.start[0], line.end[0]) < 40,
                 lambda line: min(line.start[0], line.end[0]) > 40):
        assert sum(math.dist(line.start, line.end) for line in section.hatch if side(line))*.5 == pytest.approx(400, rel=.02)


def test_scaled_section_output_fills_the_whole_paper_area_and_preserves_native_dimensions(tmp_path):
    step = _write_step(BRepPrimAPI_MakeBox(800, 500, 150).Shape(), tmp_path/'large.step')
    part = render_part(step, tmp_path, section_at=250, with_questions=True)
    files = dict(part.files)
    data = json.loads(files['json'].read_text())
    factor = data['drawing_scale']
    assert 0 < factor < 1
    assert sorted(c['value'] for c in data['constraints']) == [150, 500, 800]
    svg = ET.parse(files['svg']).getroot()
    runs = [tuple(tuple(map(float, p.split(','))) for p in node.get('points').split())
            for node in svg.find("{*}g[@id='HATCH']").findall('{*}polyline')]
    assert runs
    assert sum(math.dist(run[0], run[-1]) for run in runs)*4 == pytest.approx(800*150*factor**2, rel=.03)
    assert any(t.text == 'A-A' for t in svg.findall('{*}text'))
    dxf = ezdxf.readfile(files['dxf'])
    assert not dxf.audit().errors
    for dim, entity in zip(data['dimensions'], dxf.modelspace().query('DIMENSION')):
        assert entity.get_measurement() == pytest.approx(dim['value'], abs=1e-6)

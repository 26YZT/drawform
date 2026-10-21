"""纸面比例必须改变可读大小，同时保留模型空间里的真实测量。"""
import json
import math
from pathlib import Path
import xml.etree.ElementTree as ET

import ezdxf
import pytest
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.gp import gp_Pnt

from drawform.pipeline import render_part
from drawform.delivery import DeliveryBlocked
from tests.conftest import _write_step


@pytest.mark.parametrize('origin', [(0, 0, 0), (123, -44, 20)])
def test_a_small_part_is_enlarged_on_paper_without_scaling_native_measurements(tmp_path, origin):
    step = _write_step(BRepPrimAPI_MakeBox(gp_Pnt(*origin), 5, 3, 2).Shape(), tmp_path/'small.step')
    output = tmp_path/'drawing'
    output.mkdir()
    part = render_part(step, output, with_questions=True)
    files = dict(part.files)
    data = json.loads(files['json'].read_text())
    scale = data.get('drawing_scale', 1.0)
    assert scale > 1, '小件不能继续按 1:1 缩在 A3 中间'
    assert sorted(c['value'] for c in data['constraints']) == [2, 3, 5]
    svg = ET.parse(files['svg']).getroot()
    visible = svg.find("{*}g[@id='VISIBLE']")
    points = [tuple(map(float, point.split(','))) for line in visible.findall('{*}polyline')
              for point in line.get('points').split()]
    assert max(x for x, _ in points) - min(x for x, _ in points) >= 25
    assert next(t for t in svg.findall('{*}text') if t.text == '未经制图审核').get('font-size') == '3.5'
    doc = ezdxf.readfile(files['dxf'])
    assert not doc.audit().errors
    for dim, entity in zip(data['dimensions'], doc.modelspace().query('DIMENSION')):
        assert entity.get_measurement() == pytest.approx(dim['value'], abs=1e-8)
    viewports = [v for v in doc.layout().query('VIEWPORT') if v.dxf.id != 1]
    assert len(viewports) == 1 and viewports[0].get_scale() == pytest.approx(scale)
    for block in (doc.blocks[e.dxf.geometry] for e in doc.modelspace().query('DIMENSION')):
        assert all(t.dxf.char_height*scale == pytest.approx(3.5) for t in block.query('MTEXT'))
    assert any(t.text == data['scale_label'] for t in svg.findall('{*}text'))


def test_a_large_valid_single_solid_fits_a3_without_altering_dimension_values(tmp_path):
    step = _write_step(BRepPrimAPI_MakeBox(800, 500, 150).Shape(), tmp_path/'large.step')
    output = tmp_path/'drawing'
    output.mkdir()
    part = render_part(step, output, with_questions=True)
    files = dict(part.files)
    data = json.loads(files['json'].read_text())
    assert 0 < data.get('drawing_scale', 1.0) < 1
    assert sorted(c['value'] for c in data['constraints']) == [150, 500, 800]
    doc = ezdxf.readfile(files['dxf'])
    assert not doc.audit().errors
    for dim, entity in zip(data['dimensions'], doc.modelspace().query('DIMENSION')):
        assert entity.get_measurement() == pytest.approx(dim['value'], abs=1e-7)
        assert math.dist(dim['p1'], dim['p2']) == pytest.approx(dim['value'], abs=1e-7)
    assert data['scale_label'].startswith('1:')


def test_nist_ctc01_keeps_all_diameters_but_refuses_unreadably_dense_a3_layout(tmp_path):
    step = Path(__file__).parent/'fixtures/nist_ctc01_ap242.stp'
    from drawform.geometry.references import ReferenceRequired
    with pytest.raises(ReferenceRequired) as waiting:
        render_part(step, tmp_path, with_questions=True)
    assert waiting.value.failed == ('reference_required',)
    assert not list(tmp_path.iterdir()), '未确认参考前不能生成交付图'
    snapshot = waiting.value.snapshot
    assert snapshot['questions'] and all(q['options'] for q in snapshot['questions'])
    # 仅用于回归纸面门的自动候选选择，不代表真实工程确认或人工审图。
    reference = {'basis_sha': snapshot['basis_sha'], 'answers': [
        {'question_id': q['id'], 'option_id': q['options'][0]['id']}
        for q in snapshot['questions']]}
    with pytest.raises(DeliveryBlocked) as error:
        render_part(step, tmp_path, with_questions=True, reference_in=reference)
    # 确认真实几何候选后仍须通过纸面门，原超幅与无图件保护保持。
    assert error.value.verdict.failed == ('sheet_overflow',)
    assert '轮廓可读性' in error.value.verdict.reasons[0]
    assert not list(tmp_path.iterdir()), '不能留下删除尺寸或无限缩小后勉强出图的文件'


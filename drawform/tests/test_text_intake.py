"""Strict partial text facts and dynamic questions, without a CAD/runtime call."""
import copy
import hashlib

import pytest

from drawform.workbench.plate_spec import SpecError
from drawform.workbench.text_intake import analyze_text, refresh_questions


MULTI = ('矩形板，长120毫米，宽80毫米。以板中心为原点，两个通孔。'
         '孔1：X=-25，Y=5，直径8毫米；孔2：X=25，Y=-5，直径12毫米。')


def parameters(data):
    return {q['parameter'] for q in data['questions']}


@pytest.mark.parametrize('text,expected', [
    ('矩形板长100毫米宽60毫米，中心通孔直径20毫米', {'thickness_mm'}),
    ('矩形板长100毫米宽60毫米厚8毫米，中心通孔', {'holes.0.diameter_mm'}),
    ('矩形板宽60毫米厚8毫米，无孔', {'length_mm'}),
    ('矩形板长100毫米宽60毫米厚8毫米', {'hole_mode'}),
    ('垫圈外径60毫米厚8毫米', {'inner_diameter_mm'}),
    ('实心圆盘外径40毫米', {'thickness_mm'}),
    (MULTI, {'thickness_mm'}),
    (MULTI.replace('以板中心为原点，', ''), {'thickness_mm', 'hole_origin'}),
    (MULTI.replace('X=-25', 'X=').replace('Y=-5', 'Y='),
     {'thickness_mm', 'holes.0.x_mm', 'holes.1.y_mm'}),
    ('矩形板长100宽60厚8中心通孔直径20', {'units'}),
    ('矩形板长100宽60中心通孔孔径20，单位毫米', {'thickness_mm'}),
])
def test_only_actual_missing_fields_are_questions(text, expected):
    data = analyze_text(text)
    assert parameters(data) == expected, data
    assert data['source_sha256'] == hashlib.sha256(text.encode()).hexdigest()
    norm = data['normalized_text']
    assert data['normalized_text_sha256'] == hashlib.sha256(norm.encode()).hexdigest()
    for fact in data['facts']:
        evidence = fact['evidence']
        assert evidence['type'] == 'text_span' and evidence['span_basis'] == 'normalized_text'
        start, end = evidence['span']
        assert evidence['text'] == norm[start:end]


@pytest.mark.parametrize('text', [
    '做一块长100毫米、宽60毫米、厚8毫米的板，中间开一个直径20毫米的通孔。',
    'plate length100 width60 thickness8 mm with center through hole diameter20 mm',
    '100×60×8 mm 的板，中心通孔 Ø20 mm',
    MULTI.replace('宽80毫米', '宽80毫米厚8毫米'),
    '垫圈外径60毫米内径20毫米厚8毫米',
    '实心圆盘外径40毫米厚6毫米',
])
def test_existing_named_complete_examples_can_be_analyzed(text):
    data = analyze_text(text)
    assert data['questions'] == [], data
    assert data['units'] == 'mm'


def test_dynamic_mode_and_unit_answers_preserve_existing_facts_and_values():
    data = analyze_text('矩形板长100宽60厚8')
    facts = data['facts']; snapshot = copy.deepcopy(facts)
    assert parameters(data) == {'units', 'hole_mode'}
    data['units'] = 'mm'
    assert refresh_questions(data) is data
    assert parameters(data) == {'hole_mode'}
    data['hole_mode'] = 'center_through'
    refresh_questions(data)
    assert data['holes'] == [{'x_mm': 0., 'y_mm': 0., 'diameter_mm': None}]
    assert parameters(data) == {'holes.0.diameter_mm'}
    data['holes'][0]['diameter_mm'] = 20
    refresh_questions(data)
    assert data['questions'] == []
    assert data['facts'] is facts and data['facts'] == snapshot
    assert data['parameters'] == {'length_mm': 100., 'width_mm': 60., 'thickness_mm': 8.}
    assert data['through_confirmed'] and data['coordinate_origin_confirmed']


def test_origin_and_zero_negative_coordinate_answers_only_remove_resolved_questions():
    data = analyze_text(MULTI.replace('以板中心为原点，', '').replace('X=-25', 'X=0'))
    assert data['holes'][0]['x_mm'] == 0 and data['holes'][1]['y_mm'] == -5
    data['coordinate_origin_confirmed'] = True
    refresh_questions(data)
    assert parameters(data) == {'thickness_mm'}
    data['parameters']['thickness_mm'] = 8
    refresh_questions(data)
    assert data['questions'] == []


@pytest.mark.parametrize('text', [
    '矩形板长100毫米宽60毫米中心通孔直径20毫米 ADD A BOSS',
    '矩形板长100毫米宽60毫米中心盲孔直径20毫米',
    '矩形板长100毫米宽60毫米中心螺纹孔直径20毫米',
    '矩形板长100毫米宽60毫米厚8+2毫米无孔',
    '矩形板长100毫米长120毫米宽60毫米无孔',
    MULTI.replace('X=-25', 'X=-25+3'),
    '长100毫米宽60毫米厚8毫米无孔',
    '矩形板100 60 8毫米无孔',
    MULTI.replace('毫米', 'cm'),
    MULTI.replace('以板中心为原点', '以左下角为原点'),
    MULTI.replace('孔2：X=25，Y=-5，直径12毫米。', ''),
    MULTI.replace('孔2：', '孔1：'),
    MULTI.replace('孔2：', '孔3：'),
    '垫圈外径60毫米内径0毫米',
    '实心圆盘外径40毫米内径20毫米',
    MULTI + ' 对称均布',
    '矩形板长40毫米宽100毫米中心通孔直径80毫米',
])
def test_illegal_requirements_are_rejected_instead_of_becoming_questions(text):
    with pytest.raises(SpecError):
        analyze_text(text)

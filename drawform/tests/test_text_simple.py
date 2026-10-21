"""Complete finite plate text produces existing native schema2, never guesses."""
import pytest

from drawform.workbench.plate_spec import SpecError
from drawform.workbench.text_simple import parse_simple_plate


TWO = ('矩形板，长120毫米，宽80毫米，厚8毫米。以板中心为原点，两个通孔。'
       '孔1：X=-25，Y=5，直径8毫米；孔2：X=25，Y=-5，直径12毫米。')
EXPECTED = {'schema_version': 2, 'kind': 'plate',
            'parameters': {'length_mm': 120., 'width_mm': 80., 'thickness_mm': 8.},
            'holes': [{'x_mm': -25., 'y_mm': 5., 'diameter_mm': 8.},
                      {'x_mm': 25., 'y_mm': -5., 'diameter_mm': 12.}]}


@pytest.mark.parametrize('text,expected', [
    ('矩形板，长100毫米，宽60毫米，厚8毫米，无孔。',
     {'schema_version': 2, 'kind': 'plate', 'parameters': {'length_mm': 100., 'width_mm': 60., 'thickness_mm': 8.}, 'holes': []}),
    (TWO, EXPECTED),
    ('rectangular plate, length120 mm, width80 mm, thickness8 mm; origin at plate center; two through holes; '
     'hole 1: x=-25, y=5, diameter8 mm; hole 2: x=25, y=-5, diameter12 mm.', EXPECTED),
    ('rectangular plate length100 mm width60 mm thickness8 mm; origin at plate center; 1 through hole; '
     'hole1: x=0 y=0 diameter20 mm',
     {'schema_version': 2, 'kind': 'plate', 'parameters': {'length_mm': 100., 'width_mm': 60., 'thickness_mm': 8.},
      'holes': [{'x_mm': 0., 'y_mm': 0., 'diameter_mm': 20.}]}),
    ('矩形板，长１２０毫米，宽８０毫米，厚８毫米。以板中心为原点，两个通孔。'
     '孔２：X=２５，Y=-５，直径１２毫米；孔１：X=-２５，Y=５，直径８毫米。', EXPECTED),
])
def test_complete_explicit_text_produces_exact_existing_native_spec(text, expected):
    assert parse_simple_plate(text) == expected


@pytest.mark.parametrize('text', [
    '矩形板，长100毫米，宽60毫米，无孔。',
    '矩形板，长100，宽60，厚8，无孔。',
    TWO.replace('以板中心为原点，', ''),
    TWO.replace('两个通孔', '两个盲孔'),
    TWO + '两个孔对称均布。',
    TWO + '孔距边10毫米。',
    TWO.replace('长120毫米', '长120毫米，长130毫米'),
    TWO.replace('孔2：', '孔1：'),
    TWO.replace('孔2：', '孔3：'),
    TWO.replace('两个通孔', '三个通孔'),
    TWO.replace('两个通孔', '17个通孔'),
    TWO + ' ADD A BOSS',
    TWO.replace('毫米', 'inch'),
    TWO.replace('X=-25', 'X=-25+3'),
    TWO.replace('X=-25', 'X=-25，X=-20'),
    TWO.replace('X=25', 'X=60'),
    TWO.replace('X=25，Y=-5', 'X=-25，Y=5'),
    TWO + ' 123',
])
def test_incomplete_ambiguous_or_extra_requirements_are_rejected(text):
    with pytest.raises(SpecError):
        parse_simple_plate(text)


@pytest.mark.parametrize('text', [None, '矩形板' * 800])
def test_invalid_text_shape_or_size_is_a_controlled_spec_error(text):
    with pytest.raises(SpecError):
        parse_simple_plate(text)

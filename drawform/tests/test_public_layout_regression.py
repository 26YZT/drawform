"""冻结输出是数值不变的比较基线；不能只比较本次回答前后。"""
import importlib.util
from pathlib import Path
import sys

import pytest

scripts = Path(__file__).resolve().parents[1] / 'deploy'
sys.path.insert(0, str(scripts))
try:
    spec = importlib.util.spec_from_file_location('public_layout_regression', scripts/'regress_public_step.py')
    regression = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(regression)
finally:
    sys.path.remove(str(scripts))


def _document():
    return {'constraints':[{'feature':name,'dof':'x','value':5.25,'quantity':1} for name in ('a','b')],
            'dimensions':[{'source':[name,'x'],'value':5.25,'shared_sources':[],
                           'shared_witnesses':[],'shared_values':[],'p1':[0,0],'p2':[5.25,0]} for name in ('a','b')]}


def test_shared_annotation_is_not_a_lost_numeric_source():
    before, after = _document(), _document()
    after['dimensions'] = [{'source':['a','x'],'shared_sources':[['b','x']],'value':5.25,
                           'shared_witnesses':[[[0,0],[5.25,0]]],'shared_values':[5.25],'p1':[0,0],'p2':[5.25,0]}]
    report = regression.compare_numbers(before,after)
    assert report['visible_dimensions_before'] == 2 and report['visible_dimensions_after'] == 1
    assert report['dimension_sources'] == 2 and report['values_and_quantities_unchanged']


@pytest.mark.parametrize('changed', ['value','quantity','missing_source','duplicate_source','point','shared_value'])
def test_numeric_regression_rejects_silent_changes(changed):
    before, after = _document(), _document()
    if changed in ('value','quantity'):
        after['constraints'][0][changed] += 1
    elif changed == 'missing_source':
        after['dimensions'].pop()
    elif changed == 'point':
        after['dimensions'][0]['p1'] = [0,2]
    else:
        after['dimensions'][0]['shared_sources'] = [['b','x']]
        after['dimensions'][0]['shared_witnesses'] = [[[0,0],[5.25,0]]]
        after['dimensions'][0]['shared_values'] = [5.25]
        if changed == 'shared_value':
            after['dimensions'].pop()
            after['dimensions'][0]['shared_values'] = [5.25001]
    with pytest.raises(AssertionError):
        regression.compare_numbers(before,after)


@pytest.mark.parametrize('inside', [True,False])
def test_public_check_detects_a_letter_detached_from_its_datum_box(inside):
    import xml.etree.ElementTree as ET
    root = ET.fromstring('<svg xmlns="http://www.w3.org/2000/svg"><g id="DATUM">'
                         '<polyline points="10,10 17,10 17,17 10,17 10,10"/></g>'
                         f'<text x="{13 if inside else 30}" y="14">A</text></svg>')
    if inside:
        regression.check_datum_boxes(root,{'datums':[{'letter':'A'}]})
    else:
        with pytest.raises(AssertionError,match='移出了方框'):
            regression.check_datum_boxes(root,{'datums':[{'letter':'A'}]})

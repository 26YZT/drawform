"""本批公开 STEP 修复回归：与冻结的旧结果逐条比数，不把模拟采纳当人工审图。"""
import argparse
import json
import math
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

import ezdxf

from verify_public_step import verify
from drawform.output.collide import _boxes_overlap
from drawform.output.annotation_layout import annotation_box
from drawform.output.drawing import Text
from drawform.output.paper_notes import UNREVIEWED


def numeric_facts(document):
    return {(c['feature'],c['dof']):(c['value'],c['quantity']) for c in document['constraints']}


def compare_numbers(previous, current):
    assert numeric_facts(previous) == numeric_facts(current), '尺寸数值或数量被改写'
    old = {tuple(d['source']):d['value'] for d in previous['dimensions']}
    old_points = {tuple(d['source']):(d['p1'],d['p2']) for d in previous['dimensions']}
    new = {}
    new_points = {}
    for dim in current['dimensions']:
        assert len(dim['shared_sources']) == len(dim['shared_witnesses']) == len(dim['shared_values'])
        new_points[tuple(dim['source'])] = (dim['p1'],dim['p2'])
        new_points.update((tuple(source),tuple(points)) for source,points in zip(
            dim['shared_sources'],dim['shared_witnesses']))
        for source,value in zip([dim['source'], *dim['shared_sources']],[dim['value'], *dim['shared_values']]):
            key = tuple(source)
            assert key not in new, f'来源重复标注：{key}'
            new[key] = value
    assert old == new, '尺寸显示值或共享标注来源丢失'
    assert old_points == new_points, '原始测量端点丢失或变化'
    return {'constraints':len(numeric_facts(current)), 'dimension_sources':len(new),
            'visible_dimensions_before':len(previous['dimensions']),
            'visible_dimensions_after':len(current['dimensions']),
            'values_and_quantities_unchanged':True,'measurement_points_unchanged':True}


def check_datum_boxes(root, document):
    group = root.find("{*}g[@id='DATUM']")
    boxes = []
    if group is not None:
        for node in group.findall('{*}polyline'):
            points = [tuple(map(float,p.split(','))) for p in node.get('points').split()]
            if len(points) == 5 and points[0] == points[-1]:
                boxes.append((min(p[0] for p in points),min(p[1] for p in points),
                              max(p[0] for p in points),max(p[1] for p in points)))
    for datum in document['datums']:
        labels = [t for t in root.findall('{*}text') if t.text == datum['letter']]
        assert len(labels) == 1, '基准字母缺失或重复'
        x,y = float(labels[0].get('x')),float(labels[0].get('y'))
        assert any(a < x < c and b < y < d for a,b,c,d in boxes), '基准字母移出了方框'


def inspect_files(folder, previous_folder):
    root = ET.parse(folder/'current.svg').getroot()
    labels = root.findall('{*}text')
    assert sum(t.text == UNREVIEWED for t in labels) == 1
    current_document = json.loads((folder/'current.json').read_text())
    check_datum_boxes(root,current_document)
    check_datum_boxes(ET.parse(folder/'original.svg').getroot(),json.loads((folder/'original.json').read_text()))
    sheet_height = float(root.get('viewBox').split()[3])
    boxes = [(t.text, annotation_box(Text(t.text or '', float(t.get('x')),sheet_height-float(t.get('y')),
                                  t.get('text-anchor','start'),
                                  -90 if t.get('transform') else 0, height=float(t.get('font-size')))))
             for t in labels]
    overlaps = [(a,b) for i,(a,x) in enumerate(boxes) for b,y in boxes[i+1:] if _boxes_overlap(x,y)]
    doc = ezdxf.readfile(folder/'current.dxf')
    assert not doc.audit().errors
    assert sum(e.dxf.text == UNREVIEWED for e in doc.modelspace().query('TEXT')) == 1
    dimensions = current_document['dimensions']
    entities = list(doc.modelspace().query('DIMENSION'))
    assert len(entities) == len(dimensions)
    old_dimensions = json.loads((previous_folder/'current.json').read_text())['dimensions']
    old_entities = list(ezdxf.readfile(previous_folder/'current.dxf').modelspace().query('DIMENSION'))
    assert len(old_entities) == len(old_dimensions)
    old_measurements = {tuple(d['source']):e.get_measurement() for d,e in zip(old_dimensions,old_entities)}
    discrepancies = []
    for entity, dim in zip(entities, dimensions):
        measured = entity.get_measurement()
        for source in [dim['source'], *dim['shared_sources']]:
            assert math.isclose(measured,old_measurements[tuple(source)],abs_tol=1e-9), '原生 DXF 测量值变化'
        if not math.isclose(measured,dim['value'],abs_tol=1e-6):
            discrepancies.append({'source':dim['source'],'nominal':dim['value'],
                                  'measurement':measured,'existed_in_baseline':True})
    result = subprocess.run(['pdftotext',str(folder/'current.pdf'),'-'],check=True,capture_output=True,text=True)
    assert UNREVIEWED in result.stdout.replace('\n','')
    return {'svg_text_overlap_pairs':overlaps,'unreviewed_svg_dxf_pdf':True,
            'native_dimensions':len(doc.modelspace().query('DIMENSION')),'dxf_audit_errors':0,
            'native_measurements_unchanged':True,'datum_letters_inside_boxes':True,
            'existing_projection_discrepancies':discrepancies}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('catalog','runtime','output','baseline'):
        parser.add_argument('--'+key,required=True,type=Path)
    args = parser.parse_args()
    result = verify(args.catalog,args.runtime,args.output)
    previous = json.loads((args.baseline/'summary.json').read_text())
    baseline = {r['id']:r for r in previous['cases']}
    regressions = []
    for row in result['cases']:
        old = baseline[row['id']]
        check = {'id':row['id'],'outcome':row['outcome'],'human_review':False}
        try:
            if row['geometry']['solids'] > 1:
                assert row['outcome'] == 'engine_refused'
                assert row['gate_failed'] == ['multiple_solids']
                assert '单体' in ''.join(row['gate_reasons'])
                check['multi_solid_rejected'] = True
            elif old['outcome'] == 'simulated_accepted':
                assert row['outcome'] == 'simulated_accepted', row.get('error',row.get('gate_reasons'))
                folder = args.output/'cases'/row['id']
                for version in ('original','current'):
                    before = json.loads((args.baseline/'cases'/row['id']/(version+'.json')).read_text())
                    after = json.loads((folder/(version+'.json')).read_text())
                    check[version] = compare_numbers(before,after)
                check.update(inspect_files(folder,args.baseline/'cases'/row['id']))
                assert check['svg_text_overlap_pairs'] == [], check['svg_text_overlap_pairs']
            else:
                assert row['outcome'] == 'engine_refused' and row['gate_failed'] == old['gate_failed']
                check['existing_overflow_preserved'] = True
            check['pass'] = True
        except Exception as error:
            check.update({'pass':False,'error':f'{type(error).__name__}: {error}'})
        regressions.append(check)
    report = {'cases':regressions, 'passed':sum(r['pass'] for r in regressions),
              'requested':len(baseline),'true_human_approved':0,'production_release':False}
    (args.output/'regression.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return int(report['passed'] != report['requested'] or result['error'] or result['engine_crashed'])


if __name__ == '__main__':
    raise SystemExit(main())

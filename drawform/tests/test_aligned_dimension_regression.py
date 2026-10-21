"""斜向线性尺寸的可见长度、原生测量和纸面偏移必须表达同一几何。"""
from dataclasses import replace
import json
import math
import xml.etree.ElementTree as ET

import ezdxf
import pytest
from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf

from drawform.dimensioning.place import PlacedDimension
from drawform.domain.curves import FRONT, Line2D
from drawform.geometry.loader import import_step
from drawform.geometry.projection import Projection
from drawform.output.annotation_layout import _attachment, annotation_box
from drawform.output.collide import avoid_lines, separate_labels, text_box, _boxes_overlap
from drawform.output.drawing import Drawing, DrawingItem, Text, _dimension
from drawform.output.dxf import _emit_dimension, _new_document
from drawform.output.paper import _dimensions
from drawform.output.svg import markup
from drawform.pipeline import render_part
from drawform.service.answers import drawing_annotations
from tests.conftest import _write_step


def _case(dx=-12, dy=16):
    return PlacedDimension('depth','front',(100,200),(100+dx,200+dy),
                           (108,200+dy/2),'↧20',20,source=('bore','depth'))


@pytest.mark.parametrize('dx', [-12,12])
def test_visible_oblique_dimension_is_parallel_to_the_true_measurement(dx):
    dim=_case(dx)
    lines,heads,texts=_dimension(dim,(30,50))
    a,b=lines[0]
    assert math.dist(a,b)==pytest.approx(20,abs=1e-9)
    assert (b[0]-a[0])*16-(b[1]-a[1])*dx==pytest.approx(0,abs=1e-9)
    assert math.dist(heads[0][0],heads[1][0])==pytest.approx(20,abs=1e-9)
    assert texts[0].content=='↧20' and dim.p1==(100,200)


@pytest.mark.parametrize('dx', [-12,12])
def test_native_dimension_measures_oblique_span_instead_of_its_horizontal_projection(dx):
    doc=_new_document();dim=_case(dx)
    _emit_dimension(doc.modelspace(),dim,(30,50))
    entity,=doc.modelspace().query('DIMENSION')
    assert entity.get_measurement()==pytest.approx(20,abs=1e-9)
    assert tuple(entity.dxf.defpoint2)[:2]==pytest.approx((130,250))
    assert tuple(entity.dxf.defpoint3)[:2]==pytest.approx((130+dx,266))
    assert not doc.audit().errors


@pytest.mark.parametrize('factor', [.1,.5,2])
def test_oblique_paper_offset_is_relative_to_the_witness_line_not_the_view_box(factor):
    dim=_case();projection=Projection(FRONT,(
        Line2D((0,0),(1000,0)),Line2D((1000,0),(1000,1000)),
        Line2D((1000,1000),(0,1000)),Line2D((0,1000),(0,0))),())
    paper,=_dimensions((dim,),{'front':projection},factor)
    normal=(-.8,-.6)
    mid=tuple((a+b)/2 for a,b in zip(dim.p1,dim.p2))
    distance=sum((dim.base[i]-mid[i])*normal[i] for i in (0,1))
    paper_mid=tuple((a+b)/2 for a,b in zip(paper.p1,paper.p2))
    assert sum((paper.base[i]-paper_mid[i])*normal[i] for i in (0,1))==pytest.approx(distance,abs=1e-9)
    assert paper.value==dim.value and paper.source==dim.source


def test_oblique_annotation_box_contains_the_rotated_font_rectangle():
    text=Text('123456',50,70,'middle',-45,height=4)
    width=.7*4*6;side=(width+4)/math.sqrt(2)
    box=text_box(text)
    assert box[2]-box[0]==pytest.approx(side,abs=1e-9)
    assert box[3]-box[1]==pytest.approx(side,abs=1e-9)
    visible=annotation_box(text)
    for x in (-width/2,width/2):
        for y in (-2,4):
            px=50+(x-y)/math.sqrt(2);py=70+(x+y)/math.sqrt(2)
            assert visible[0]-1e-9<=px<=visible[2]+1e-9
            assert visible[1]-1e-9<=py<=visible[3]+1e-9


def test_relocated_oblique_text_attaches_to_the_actual_dimension_line_midpoint():
    dim=_case();_tip,anchor=_attachment(dim,(30,50))
    assert anchor==pytest.approx((132.96,264.72),abs=1e-9)


def test_oblique_text_avoids_another_line_along_its_own_reading_axis():
    text=Text('123',100,100,'middle',-45,owner='dim:ours')
    blocker=DrawingItem('dim:other','DIM',((100,85),(100,115)))
    moved,=avoid_lines((text,),(blocker,))
    assert (moved.x,moved.y)!=(text.x,text.y)
    assert moved.x-text.x==pytest.approx(moved.y-text.y,abs=1e-9)


def test_oblique_overlap_separation_keeps_both_labels_on_their_reading_axes():
    texts=(Text('123',100,100,'middle',-45,owner='a'),
           Text('456',101,101,'middle',-45,owner='b'))
    moved=separate_labels(texts)
    assert not _boxes_overlap(*map(text_box,moved))
    for old,new in zip(texts,moved):
        assert new.x-old.x==pytest.approx(new.y-old.y,abs=1e-9)


def test_change_markers_accept_the_rotation_emitted_by_an_oblique_dimension(tmp_path):
    before=tmp_path/'before.svg';after=tmp_path/'after.svg'
    text=Text('↧20',100,100,'middle',-45,owner='dim:depth')
    before.write_text(markup(Drawing('A3',(),(),(text,))))
    after.write_text(markup(Drawing('A3',(),(),(replace(text,content='↧20（待确认）'),))))
    annotations=drawing_annotations(before,after)
    assert annotations
    assert all(a['box'][2]>0 and a['box'][3]>0 for a in annotations)


@pytest.mark.parametrize('factor', [1,10])
def test_a_real_rotated_blind_hole_keeps_its_depth_in_all_four_outputs(blind_hole_step,tmp_path,factor):
    shape=import_step(blind_hole_step).shape
    if factor!=1:
        transform=gp_Trsf();transform.SetScale(gp_Pnt(0,0,0),factor)
        shape=BRepBuilderAPI_Transform(shape,transform,True).Shape()
    transform=gp_Trsf();transform.SetRotation(gp_Ax1(gp_Pnt(0,0,0),gp_Dir(1,0,0)),.4)
    shape=BRepBuilderAPI_Transform(shape,transform,True).Shape()
    step=_write_step(shape,tmp_path/'rotated.step')
    from drawform.geometry.references import ReferenceRequired
    before={p.name:p.read_bytes() for p in tmp_path.iterdir()}
    with pytest.raises(ReferenceRequired) as waiting:
        render_part(step,tmp_path,with_questions=True)
    assert {p.name:p.read_bytes() for p in tmp_path.iterdir()}==before
    snapshot=waiting.value.snapshot
    assert snapshot['questions'] and all(q['options'] for q in snapshot['questions'])
    # 自动选择仅用于四出口深度回归，不代表真实工程确认或人工审图。
    reference={'basis_sha':snapshot['basis_sha'],'answers':[
        {'question_id':q['id'],'option_id':q['options'][0]['id']} for q in snapshot['questions']]}
    part=render_part(step,tmp_path,with_questions=True,reference_in=reference)
    files=dict(part.files);data=json.loads(files['json'].read_text());dxf=ezdxf.readfile(files['dxf'])
    rows=[(dim,e) for dim,e in zip(data['dimensions'],dxf.modelspace().query('DIMENSION')) if dim['kind']=='depth']
    assert len(rows)==1
    dim,entity=rows[0]
    assert dim['value']==pytest.approx(12*factor,abs=1e-6)
    assert math.dist(dim['p1'],dim['p2'])==pytest.approx(dim['value'],abs=1e-6)
    assert entity.get_measurement()==pytest.approx(dim['value'],abs=1e-6)
    svg=ET.parse(files['svg']).getroot()
    assert any((t.text or '').startswith('↧') for t in svg.findall('{*}text'))
    assert files['pdf'].read_bytes().startswith(b'%PDF')

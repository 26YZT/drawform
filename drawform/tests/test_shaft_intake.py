"""Strict shaft text, native DXF and actual local OCR; no CAD build/runtime."""
import hashlib
import json
import sys
import copy
import ezdxf
import pytest
from PIL import Image, ImageDraw, ImageFont
from drawform.workbench.plate_spec import SpecError
from drawform.workbench.shaft_input import analyze_shaft_text, refresh_shaft_questions, analyze_shaft_dxf, analyze_shaft_entries
from drawform.workbench.text_intake import analyze_text
from drawform.workbench.drawing_intake import run, consume_annotations

TEXT = '二段同轴实心阶梯轴，第一段直径40毫米、长度30毫米；第二段直径20毫米、长度40毫米。'
EXPECTED = {'diameter_1_mm': 40., 'length_1_mm': 30., 'diameter_2_mm': 20., 'length_2_mm': 40.}
POINTS = [(0,-20),(30,-20),(30,-10),(70,-10),(70,10),(30,10),(30,20),(0,20)]


@pytest.mark.parametrize('text,missing', [
    (TEXT, set()),
    (TEXT.replace('、长度40毫米', ''), {'length_2_mm'}),
    (TEXT.replace('实心', ''), {'shaft_solid'}),
    ('stepped shaft solid D1=40 mm L1=30 mm D2=20 mm L2=40 mm', set()),
    ('阶梯轴 D1=40 L1=30 D2=20 L2=40', {'units','shaft_solid'}),
])
def test_shaft_text_exact_roles_missing_questions_and_source_spans(text, missing):
    data = analyze_shaft_text(text)
    assert data['kind'] == 'stepped_shaft' and data['holes'] == []
    assert {q['parameter'] for q in data['questions']} == missing
    assert data['source_sha256'] == hashlib.sha256(text.encode()).hexdigest()
    if not missing:assert data['parameters'] == EXPECTED
    assert analyze_text(text)['parameters'] == data['parameters']
    for fact in data['facts']:
        start,end = fact['evidence']['span']
        assert fact['evidence']['text'] == data['normalized_text'][start:end]


def test_refresh_shaft_questions_preserves_facts_and_existing_values():
    data = analyze_shaft_text('阶梯轴 D1=40 L1=30 D2=20')
    facts = data['facts']; before = copy.deepcopy(facts)
    data['units']='mm'; data['shaft_solid_confirmed']=True
    assert refresh_shaft_questions(data) is data
    assert [q['parameter'] for q in data['questions']] == ['length_2_mm']
    data['parameters']['length_2_mm']=40
    refresh_shaft_questions(data)
    assert data['questions']==[] and data['facts'] is facts and facts==before


@pytest.mark.parametrize('text', [
    TEXT+' ADD A CUSTOM LUG', TEXT+' 通孔直径5毫米', TEXT+' 螺纹',
    TEXT.replace('二段','三段'), TEXT.replace('直径20','直径40'),
    TEXT.replace('直径40','直径40+2'), TEXT.replace('毫米','inch'),
    TEXT.replace('第一段直径40','第一段直径40直径42'),
    '实心阶梯轴 第一段40 30 第二段20 40 mm',
])
def test_illegal_shaft_requirements_never_become_questions(text):
    with pytest.raises(SpecError):analyze_shaft_text(text)


def source_request(tmp_path, source, fmt):
    path = tmp_path/'request.json'; output=tmp_path/'analysis'
    path.write_text(json.dumps({'input_path':str(source),'format':fmt,'output_dir':str(output),
                              'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest()}))
    return path


def dxf(tmp_path, note=None, extra=False):
    doc=ezdxf.new('R2010');doc.units=4;m=doc.modelspace()
    m.add_lwpolyline(POINTS,close=True)
    m.add_text('SOLID STEPPED SHAFT',dxfattribs={'insert':(0,30),'height':3})
    if note:m.add_text(note,dxfattribs={'insert':(0,38),'height':3})
    if extra:m.add_circle((10,0),2)
    source=tmp_path/'shaft.dxf';doc.saveas(source);return source


def test_actual_eight_vertex_native_dxf_reads_four_dimensions(tmp_path):
    data=run(source_request(tmp_path,dxf(tmp_path),'dxf'))
    assert data['kind']=='stepped_shaft' and not data.get('unsupported'),data
    assert data['parameters']==EXPECTED and data['questions']==[]
    assert any(f['evidence'].get('entity_handle') for f in data['facts'])
    assert (tmp_path/'analysis/preview.png').is_file()


@pytest.mark.parametrize('note,extra,code',[('D1=42 mm',False,'source_conflict'),('REMOVE A WEDGE',False,'unexplained_annotation'),(None,True,'unsupported_geometry')])
def test_native_dxf_conflict_unknown_note_or_extra_geometry_blocks(tmp_path,note,extra,code):
    data=run(source_request(tmp_path,dxf(tmp_path,note,extra),'dxf'))
    assert data.get('unsupported')==code,data
    assert any(q.get('readonly') for q in data['questions'])


@pytest.mark.skipif(sys.platform!='darwin',reason='local macOS OCR')
@pytest.mark.parametrize('fmt', ['png','pdf'])
def test_actual_role_labeled_image_pdf_uses_local_ocr(tmp_path,fmt):
    image=Image.new('RGB',(1800,1000),'white');draw=ImageDraw.Draw(image)
    font=ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial.ttf',46)
    lines=['SOLID STEPPED SHAFT','SEGMENT 1 DIAMETER=40 mm','SEGMENT 1 LENGTH=30 mm',
           'SEGMENT 2 DIAMETER=20 mm','SEGMENT 2 LENGTH=40 mm']
    for i,line in enumerate(lines):draw.text((70,80+i*100),line,font=font,fill='black')
    draw.line([(200+x*15,700-y*5) for x,y in POINTS]+[(200,800)],fill='black',width=4)
    source=tmp_path/f'shaft.{fmt}';image.save(source,'PDF' if fmt=='pdf' else 'PNG',resolution=150)
    data=run(source_request(tmp_path,source,fmt))
    assert data['kind']=='stepped_shaft' and not data.get('unsupported'),data
    assert data['parameters']==EXPECTED and data['questions']==[]
    assert all(x['status']!='unexplained' for x in data['text_consumption'])
    assert any(f['evidence'].get('page_index')==0 and f['evidence'].get('bbox') for f in data['facts'])


def test_old_plate_cannot_consume_unadopted_other_kind_roles():
    data={'kind':'plate','parameters':{'length_mm':100,'width_mm':60,'thickness_mm':8},
          'holes':[],'units':'mm','facts':[],'questions':[],'notes':[]}
    consume_annotations(data,[{'text':'outer diameter=40 mm','evidence':{'type':'vision_ocr','page_index':0,'bbox':[0,0,.2,.1],'confidence':1,'text':'outer diameter=40 mm'}}])
    assert data.get('unsupported')=='unexplained_annotation'


def test_readonly_question_alone_is_never_replaced_with_editable_questions():
    data=analyze_shaft_text(TEXT)
    blocked={'id':'blocked','parameter':'source_conflict','type':'blocked','readonly':True,'label':'source conflict'}
    data['questions']=[blocked]
    assert refresh_shaft_questions(data) is data
    assert data['questions']==[blocked]


@pytest.mark.parametrize('change', ['nan_plane','inf_plane','variable_width','dimension_plane','dimension_extrusion'])
def test_actual_native_bad_planes_or_widths_are_rejected(tmp_path,change):
    source=dxf(tmp_path);doc=ezdxf.readfile(source);m=doc.modelspace();poly=next(iter(m.query('LWPOLYLINE')))
    if change=='nan_plane':poly.dxf.elevation=float('nan')
    elif change=='inf_plane':poly.dxf.elevation=float('inf')
    elif change=='variable_width':poly.set_points([(x,y,.5,.5,0) for x,y in POINTS])
    else:
        dim=m.add_linear_dim(base=(0,-30),p1=(0,-20),p2=(30,-20),angle=0);dim.render()
        if change=='dimension_plane':
            dim.dimension.dxf.defpoint2=(0,-20,3);dim.dimension.dxf.defpoint3=(30,-20,3)
        else:dim.dimension.dxf.extrusion=(0,1,0)
    doc.saveas(source)
    data=analyze_shaft_dxf(ezdxf.readfile(source),hashlib.sha256(source.read_bytes()).hexdigest())
    assert data.get('unsupported') in ('unsupported_geometry','source_conflict'),data
    assert any(q.get('readonly') for q in data['questions'])


@pytest.mark.parametrize('form',['reverse','lines'])
def test_actual_native_end_order_and_line_loop_are_preserved(tmp_path,form):
    doc=ezdxf.new('R2010');doc.units=4;m=doc.modelspace()
    points=[(0,-10),(30,-10),(30,-20),(70,-20),(70,20),(30,20),(30,10),(0,10)] if form=='reverse' else POINTS
    if form=='lines':
        for a,b in zip(points,points[1:]+points[:1]):m.add_line(a,b)
    else:m.add_lwpolyline(points,close=True)
    m.add_text('SOLID STEPPED SHAFT',dxfattribs={'insert':(0,30),'height':3})
    source=tmp_path/'shaft.dxf';doc.saveas(source)
    data=run(source_request(tmp_path,source,'dxf'))
    assert not data.get('unsupported'),data
    assert data['parameters']=={**EXPECTED,'diameter_1_mm':20.,'diameter_2_mm':40.} if form=='reverse' else data['parameters']==EXPECTED


@pytest.mark.parametrize('mode,code',[('length',None),('diameter',None),('override','source_conflict'),
    ('wrong_points','source_conflict'),('extra_note','unexplained_annotation'),
    ('placeholder_number','unexplained_annotation'),('factor','source_conflict')])
def test_real_native_dimensions_are_bound_to_profile_definition_points(tmp_path,mode,code):
    source=dxf(tmp_path);doc=ezdxf.readfile(source);m=doc.modelspace()
    if mode=='diameter':dim=m.add_linear_dim(base=(80,0),p1=(15,-20),p2=(15,20),angle=90,text='%%c<>')
    elif mode=='wrong_points':dim=m.add_linear_dim(base=(0,-30),p1=(15,-10),p2=(55,-10),angle=0)
    else:dim=m.add_linear_dim(base=(0,-30),p1=(0,-20),p2=(30,-20),angle=0,
        text={'override':'42','extra_note':'<> ADD A CUSTOM LUG','placeholder_number':'<> 42'}.get(mode,'<>'))
    dim.render()
    if mode=='factor':dim.update({'dimlfac':2});dim.commit()
    doc.saveas(source)
    data=run(source_request(tmp_path,source,'dxf'))
    assert data.get('unsupported')==code,data
    if code:
        assert any(q.get('readonly') for q in data['questions'])
        assert any(f['parameter'] in ('source_conflict','unexplained_annotation') and f['evidence'].get('entity_handle') for f in data['facts'])
    else:assert any(f['parameter']=='native_dimension' for f in data['facts'])


def role_entries():
    return [{'text':text,'evidence':{'type':'vision_ocr','page_index':0,'bbox':[.1,.1,.3,.05],'text':text,'confidence':1}}
            for text in ['SOLID STEPPED SHAFT','SEGMENT 1 DIAMETER=40 mm','SEGMENT 1 LENGTH=30 mm',
                         'SEGMENT 2 DIAMETER=20 mm','SEGMENT 2 LENGTH=40 mm']]


@pytest.mark.parametrize('change,code',[('low_confidence','unexplained_annotation'),('two_pages','ambiguous_pages'),('two_parts','ambiguous_parts')])
def test_role_sources_cannot_hide_low_confidence_multiple_pages_or_parts(change,code):
    entries=role_entries()
    if change=='low_confidence':entries[-1]['evidence']['confidence']=.1
    elif change=='two_pages':entries[-1]['evidence']['page_index']=1
    else:entries.append(copy.deepcopy(entries[0]))
    data=analyze_shaft_entries(entries,'0'*64)
    assert data.get('unsupported')==code,data
    assert any(q.get('readonly') for q in data['questions'])
    if change=='low_confidence':assert data['parameters']['length_2_mm'] is None


@pytest.mark.skipif(sys.platform!='darwin',reason='local macOS OCR')
@pytest.mark.parametrize('change',['missing_length','unknown_second_page'])
def test_actual_pdf_partial_role_and_unknown_second_page_are_retained(tmp_path,change):
    image=Image.new('RGB',(1800,1000),'white');draw=ImageDraw.Draw(image)
    font=ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial.ttf',46)
    lines=['SOLID STEPPED SHAFT','SEGMENT 1 DIAMETER=40 mm','SEGMENT 1 LENGTH=30 mm',
           'SEGMENT 2 DIAMETER=20 mm','SEGMENT 2 LENGTH=' if change=='missing_length' else 'SEGMENT 2 LENGTH=40 mm']
    for i,line in enumerate(lines):draw.text((70,80+i*100),line,font=font,fill='black')
    source=tmp_path/'shaft.pdf'
    if change=='unknown_second_page':
        second=Image.new('RGB',image.size,'white');ImageDraw.Draw(second).text((70,100),'REMOVE A WEDGE',font=font,fill='black')
        image.save(source,'PDF',resolution=150,save_all=True,append_images=[second])
    else:image.save(source,'PDF',resolution=150)
    data=run(source_request(tmp_path,source,'pdf'))
    if change=='missing_length':
        assert not data.get('unsupported'),data
        assert [q['parameter'] for q in data['questions']]==['length_2_mm']
    else:
        assert data.get('unsupported')=='unexplained_annotation',data
        assert any(f['parameter']=='unexplained_annotation' and f['evidence'].get('page_index')==1
                   and 'WEDGE' in f['value'] for f in data['facts'])


@pytest.mark.parametrize('kind',['text','drawing'])
def test_single_letter_annotation_is_not_swallowed_as_an_article(kind):
    if kind=='text':
        with pytest.raises(SpecError):analyze_shaft_text(TEXT+' A')
    else:
        entries=role_entries();entries.append({'text':'A','evidence':{'type':'vision_ocr','page_index':0,'bbox':[.2,.2,.1,.1],'text':'A','confidence':1}})
        data=analyze_shaft_entries(entries,'0'*64)
        assert data.get('unsupported')=='unexplained_annotation'
        assert data['text_consumption'][-1]['status']=='unexplained'


def test_unknown_drawing_units_do_not_label_missing_length_as_millimetres():
    from drawform.workbench.simple_spec import spec_from_analysis
    entries=[{'text':text,'evidence':{'type':'vision_ocr','page_index':0,'bbox':[.1,.1,.3,.05],
                                     'text':text,'confidence':1}}
             for text in ['SOLID STEPPED SHAFT','D1=1.5','L1=1.2','D2=.75','L2=']]
    data=analyze_shaft_entries(entries,'0'*64)
    assert not data.get('unsupported'),data
    question=next(q for q in data['questions'] if q['parameter']=='length_2_mm')
    assert question['unit']=='source_unit'
    assert '所选原图单位' in question['reason']
    assert {c['value'] for q in data['questions'] if q['parameter']=='units' for c in q['choices']}=={'mm','inch'}
    data['units']='inch';data['parameters']['length_2_mm']=1
    refresh_shaft_questions(data)
    assert data['questions']==[]
    assert spec_from_analysis(data)['parameters']['length_2_mm']==pytest.approx(25.4)
    text=analyze_shaft_text('实心阶梯轴 D1=1.5 L1=1.2 D2=.75')
    assert next(q for q in text['questions'] if q['parameter']=='length_2_mm')['unit']=='mm'

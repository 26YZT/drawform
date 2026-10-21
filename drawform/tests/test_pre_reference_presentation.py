"""图面独立可读，参考引注与原生尺寸分开，人工回写不能悄悄改参考。"""
from dataclasses import replace
import hashlib
import io
import json
import math
import xml.etree.ElementTree as ET

import ezdxf
import pytest

from drawform.pipeline import render_part
from drawform.domain.curves import FRONT,TOP,LEFT,view_basis,to_view
from drawform.dimensioning.scheme import derive_scheme
from drawform.dimensioning.scheme import Scheme
from drawform.dimensioning.constraints import Plan,Datum
from drawform.service.answers import bind_scheme
from drawform.geometry.references import ReferenceFact,ReferenceInputError,resolve
from drawform.output.references import DXF_APP,definitions,require_integrity
from tests.test_pre_reference_geometry import reference_model,selection
from tests.test_pre_reference_service import service_factory,AUTH,waiting,wait,body


@pytest.fixture
def presented(reference_model,tmp_path):
    part=render_part(reference_model[0],tmp_path,with_questions=True,
                     reference_in=selection(reference_model[3]))
    return dict(part.files)


def test_reference_definition_and_target_map_are_visible_without_polluting_native_dimensions(presented):
    data=json.loads(presented['json'].read_text());assert data['schema_version']==4
    presentation=data['reference_presentations'];assert presentation['mapping'] and presentation['callouts']
    for record in presentation['mapping']:
        omitted=next(i for i in range(3) if 'xyz'[i] not in record['axes'])
        assert abs(record['local_plane_constant']-record['display_plane_constant'])/abs(record['plane_normal'][omitted])<=1e-7
    svg=ET.parse(presented['svg']).getroot();texts=[t.text or '' for t in svg.findall('{*}text')]
    assert 'H01' in texts and any('R01' in t for t in texts)
    assert any('原点' in t for t in texts) and any('孔口关系未核实' in t for t in texts)
    doc=ezdxf.readfile(presented['dxf']);native=list(doc.modelspace().query('DIMENSION'))
    assert native and all('H01' not in e.dxf.text and 'R01' not in e.dxf.text for e in native)
    tagged=[e for e in doc.modelspace() if e.has_xdata(DXF_APP)]
    assert tagged and {'TEXT','LINE'}<={e.dxftype() for e in tagged}
    assert all(e.dxf.layer=='NOTE' for e in tagged)


def test_paper_callout_points_are_exact_projections_of_the_confirmed_model_intersection(presented):
    data=json.loads(presented['json'].read_text());presentation=data['reference_presentations']
    points={m['hole_tag']:m['point'] for m in presentation['mapping']}
    views={v.name:v for v in (FRONT,TOP,LEFT)};origins={v['name']:v['origin'] for v in data['views']}
    for call in presentation['callouts']:
        local=to_view(points[call['hole_tag']],*view_basis(views[call['view']]))
        expected=tuple((local[i]+origins[call['view']][i])*data['drawing_scale'] for i in range(2))
        assert call['paper_target']==pytest.approx(expected,abs=1e-8)
        assert call['leader'][0]==pytest.approx(expected,abs=1e-8)


def test_plane_equation_changes_coordinate_origin_when_a_real_datum_is_selected(reference_model):
    _step,model,features,snapshot=reference_model
    facts=resolve(snapshot,selection(snapshot));scheme=bind_scheme(derive_scheme(model,features,reference_facts=facts),model)
    face=max((f for f in scheme.datum_candidates if f.axis=='x'),key=lambda f:f.position)
    scheme=replace(scheme,datum_choices=(('A',face.id),))
    mapping,notes=definitions(scheme)
    assert mapping[0]['coordinate_origin'][0]==face.position
    assert math.fsum(n*o for n,o in zip(mapping[0]['plane_normal'],mapping[0]['coordinate_origin']))+mapping[0]['local_plane_constant']==pytest.approx(facts[0].constant,abs=1e-9)
    assert any('原点' in n for n in notes)


def test_shared_plane_uses_one_constant_safe_for_every_omitted_hole_direction():
    normal=(1e-4,math.sqrt(1-1e-8),0.0);constant=5e-8
    facts=tuple(ReferenceFact(f'hole-{i}',f'bore-{i}','plane-shared',(0,0,0),normal,constant,
                              False,'0'*64,'1'*64,axes)
                for i,axes in enumerate((('x','z'),('y','z'))))
    scheme=Scheme(Plan((),Datum('A','B','C')),(),origin=(0,0,0),reference_facts=facts)
    mapping,notes=definitions(scheme)
    assert len({m['plane_tag'] for m in mapping})==1
    assert {m['display_plane_constant'] for m in mapping}=={constant}
    equations=[n for n in notes if n.startswith('R01：')]
    assert len(equations)==1 and equations[0].endswith(f'={constant:.17g}')
    for record in mapping:
        omitted=next(i for i in range(3) if 'xyz'[i] not in record['axes'])
        assert abs(record['local_plane_constant']-record['display_plane_constant'])/abs(normal[omitted])<=1e-7


def test_unchanged_reference_dxf_can_be_verified_after_save_reload(presented,tmp_path):
    doc=ezdxf.readfile(presented['dxf']);out=tmp_path/'saved-again.dxf';doc.saveas(out)
    require_integrity(presented['dxf'],out.read_bytes())


@pytest.mark.parametrize('change',['text','tag','target','layer',
    'layer_off','layer_frozen','layer_no_plot','layer_color','layer_transparency',
    'entity_invisible','entity_color','entity_transparency','entity_extrusion',
    'text_width','text_mirror','text_oblique','style_font','style_width',
    'viewport_frozen','layer_vp_override'])
def test_deleted_or_changed_reference_definition_cannot_be_returned_as_same_basis(presented,tmp_path,change):
    doc=ezdxf.readfile(presented['dxf']);tagged=[e for e in doc.modelspace() if e.has_xdata(DXF_APP)]
    layer=doc.layers.get('NOTE');text=next(e for e in tagged if e.dxftype()=='TEXT')
    if change=='text':next(e for e in tagged if e.dxftype()=='TEXT').dxf.text='unapproved-reference'
    if change=='tag':tagged[0].discard_xdata(DXF_APP)
    if change=='target':
        e=next(e for e in tagged if e.dxftype()=='LINE');e.dxf.start=(e.dxf.start.x+1,e.dxf.start.y,e.dxf.start.z)
    if change=='layer':tagged[0].dxf.layer='VISIBLE'
    if change=='layer_off':layer.off()
    if change=='layer_frozen':layer.freeze()
    if change=='layer_no_plot':layer.dxf.plot=0
    if change=='layer_color':layer.color=255
    if change=='layer_transparency':layer.transparency=1
    if change=='entity_invisible':tagged[0].dxf.invisible=1
    if change=='entity_color':tagged[0].dxf.color=255
    if change=='entity_transparency':tagged[0].transparency=1
    if change=='entity_extrusion':text.dxf.extrusion=(0,1,0)
    if change=='text_width':text.dxf.width=.01
    if change=='text_mirror':text.dxf.text_generation_flag=2
    if change=='text_oblique':text.dxf.oblique=85
    if change=='style_font':doc.styles.get(text.dxf.style).dxf.font='unapproved-font.shx'
    if change=='style_width':doc.styles.get(text.dxf.style).dxf.width=.01
    if change in ('viewport_frozen','layer_vp_override'):
        vp=doc.layout().add_viewport(center=(100,100),size=(100,100),view_center_point=(100,100),view_height=100)
        if change=='viewport_frozen':vp.freeze('NOTE')
        else:
            overrides=layer.get_vp_overrides();overrides.set_transparency(vp.dxf.handle,1);overrides.commit()
    out=tmp_path/f'{change}.dxf';doc.saveas(out)
    with pytest.raises(ReferenceInputError) as error:require_integrity(presented['dxf'],out.read_bytes())
    assert error.value.reason=='reference_definition_changed' and error.value.status_code==409


def test_other_cad_annotations_do_not_change_the_locked_reference_definition(presented,tmp_path):
    doc=ezdxf.readfile(presented['dxf']);doc.modelspace().add_text('review note',dxfattribs={'layer':'TITLE','height':3.5})
    out=tmp_path/'other-edit.dxf';doc.saveas(out)
    require_integrity(presented['dxf'],out.read_bytes())


@pytest.mark.parametrize('change',['text','layer_no_plot','entity_invisible'])
def test_http_rejects_modified_reference_before_creating_a_version_or_review(reference_model,service_factory,tmp_path,change):
    service=service_factory(seeded=False);job,_status,snapshot=waiting(service,reference_model[0])
    service.http.post(f'/jobs/{job}/reference-answers',headers=AUTH,json=body(snapshot));status=wait(service,job,'drawn')
    original=service.http.get(status['files']['dxf'],headers=AUTH).data
    raw=tmp_path/'original.dxf';raw.write_bytes(original);doc=ezdxf.readfile(raw)
    text=next(e for e in doc.modelspace().query('TEXT') if e.has_xdata(DXF_APP))
    if change=='text':text.dxf.text='changed-reference'
    if change=='layer_no_plot':doc.layers.get('NOTE').dxf.plot=0
    if change=='entity_invisible':text.dxf.invisible=1
    edited=tmp_path/'edited.dxf';doc.saveas(edited)
    response=service.http.post(f'/jobs/{job}/amended',headers=AUTH,
        data={'reviewer':'unit-cad','draft_sha':hashlib.sha256(original).hexdigest(),
              'file':(io.BytesIO(edited.read_bytes()),'edited.dxf')},content_type='multipart/form-data')
    assert response.status_code==409,response.data
    assert response.get_json()['reason']=='reference_definition_changed'
    assert service.http.get('/review/stats',headers=AUTH).get_json()['total']==0
    assert not list((service.work/job).glob('amended-*.dxf'))

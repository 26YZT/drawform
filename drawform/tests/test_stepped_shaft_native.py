"""Four real generated shaft models through text, DXF, PDF and model history.

Native batches are coordinated by the primary agent. These tests never replace
CAD output with mocks and never alter old fixtures, services or golden files.
"""
from __future__ import annotations
import hashlib
import importlib.util
import io
import json
import math
from pathlib import Path
import time
from types import SimpleNamespace

import ezdxf
import pytest
from drawform.service.app import create_app
from drawform.geometry.loader import import_step
# pytest's configured import mode does not add tests/ to sys.path.
_helper_spec=importlib.util.spec_from_file_location('stepped_shaft_independent_helpers',Path(__file__).with_name('test_stepped_shaft_drawing.py'))
_helpers=importlib.util.module_from_spec(_helper_spec)
_helper_spec.loader.exec_module(_helpers)
assert_source_geometry=_helpers.assert_source_geometry
assert_real_shoulder_reference=_helpers.assert_real_shoulder_reference

API='/api/workbench/v1'
TOKEN='stepped-shaft-native-owner'
AUTH={'Authorization':'Bearer '+TOKEN}
PARAMETERS={'diameter_1_mm':40.,'length_1_mm':30.,'diameter_2_mm':20.,'length_2_mm':40.}
TEXT='二段阶梯轴，实心，第1段直径40毫米，第1段长度30毫米，第2段直径20毫米。'


def stop(app):
    service=app.extensions['drawform_modeling'];service.stop();service.jobs.stop()


def get(client,sid):
    response=client.get(API+'/modeling/sessions/'+sid,headers=AUTH)
    assert response.status_code==200,response.data
    return response.get_json()


def wait(client,sid,states=('needs_clarification','awaiting_adoption'),timeout=180):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        data=get(client,sid)
        if data['state'] in states:return data
        assert data['state'] not in ('failed','interrupted'),data
        time.sleep(.1)
    raise AssertionError('shaft session did not reach the expected state')


def create(client,bundle,key):
    response=client.post(API+'/modeling/sessions',headers={**AUTH,'Idempotency-Key':key},
                         json={'bundle_id':bundle,'sheet':'A3'})
    assert response.status_code==201,response.data
    return wait(client,response.get_json()['session_id'])


def answer_length_two(client,data,key):
    assert data['state']=='needs_clarification'
    assert [q['parameter'] for q in data['questions']]==['length_2_mm'],data
    q=data['questions'][0]
    body={'basis_sha256':data['basis_sha256'],'revision':data['clarification_revision'],'answers':{q['id']:40}}
    response=client.post(API+'/modeling/sessions/'+data['session_id']+'/answers',
                         headers={**AUTH,'Idempotency-Key':key},json=body)
    assert response.status_code==200,response.data
    assert client.post(API+'/modeling/sessions/'+data['session_id']+'/answers',
                       headers={**AUTH,'Idempotency-Key':key},json=body).status_code==200
    return wait(client,data['session_id'],('awaiting_adoption',))


def readback(client,data,root,parameters):
    expected=(parameters[k] for k in ('diameter_1_mm','length_1_mm','diameter_2_mm','length_2_mm'))
    expected=tuple(expected)
    assert data['spec']=={'schema_version':2,'kind':'stepped_shaft','parameters':parameters,'holes':[]}
    assert data['analysis']['shaft_solid_confirmed'] is True
    response=client.get(data['files']['step'],headers=AUTH)
    assert response.status_code==200,response.data
    saved=response.data;assert hashlib.sha256(saved).hexdigest()==data['step_sha256']
    path=root/(data['session_id']+'.step');path.write_bytes(saved)
    model=import_step(path);assert_source_geometry(model,expected)
    for kind in ('validation','drawform_validation','preview'):
        report=client.get(data['files'][kind],headers=AUTH)
        assert report.status_code==200,report.data
        payload=report.get_json();assert payload['step_sha256']==data['step_sha256']
        if kind=='preview':assert payload['vertices'] and payload['faces']
        else:assert payload['passed']
    return saved,model


def adopt_draw(client,data,model,work,parameters,key):
    sid=data['session_id']
    response=client.post(API+'/modeling/candidates/'+sid+'/adopt',headers=AUTH,
                         json={'expected_sha256':data['step_sha256']})
    assert response.status_code==200,response.data
    response=client.post(API+'/modeling/candidates/'+sid+'/exports',headers=AUTH,json={'format':'step'})
    assert response.status_code==200 and response.get_json()['representation']=='native_brep'
    response=client.post(API+'/documents/'+sid+'/drawings',headers={**AUTH,'Idempotency-Key':key},
                         json={'step_sha256':data['step_sha256']})
    assert response.status_code==202,response.data
    jid=response.get_json()['job_id'];deadline=time.monotonic()+120
    while time.monotonic()<deadline:
        response=client.get('/jobs/'+jid,headers=AUTH);assert response.status_code==200,response.data
        job=response.get_json()
        if job['state'] not in ('queued','running'):break
        time.sleep(.1)
    assert job['state']=='drawn',job
    assert hashlib.sha256((work/jid/'source.step').read_bytes()).hexdigest()==data['step_sha256']
    assert set(job['files'])=={'dxf','svg','pdf','json'}
    files={}
    for kind,endpoint in job['files'].items():
        response=client.get(endpoint,headers=AUTH);assert response.status_code==200 and response.data
        files[kind]=response.data
    assert files['pdf'].startswith(b'%PDF')
    document=json.loads(files['json'])
    shoulder=[d for d in document['dimensions'] if d['source'] and d['source'][1]=='gap' and d['axis']=='z']
    assert len(shoulder)==1
    values=tuple(parameters[k] for k in ('diameter_1_mm','length_1_mm','diameter_2_mm','length_2_mm'))
    assert shoulder[0]['value']==pytest.approx(min(values[1],values[3]))
    assert_real_shoulder_reference(model,SimpleNamespace(value=shoulder[0]['value'],
        reference_points_3d=shoulder[0]['reference_points_3d']),values)
    assert any(d['source']==['outline','height'] and d['value']==pytest.approx(values[1]+values[3]) for d in document['dimensions'])
    diameters=[d['value'] for d in document['dimensions'] if d['kind']=='diameter']
    assert sorted(diameters)==pytest.approx(sorted((values[0],values[2])))
    native=list(ezdxf.read(io.StringIO(files['dxf'].decode())).modelspace().query('DIMENSION'))
    assert len(native)==len(document['dimensions'])
    for entity,dimension in zip(native,document['dimensions']):
        assert entity.get_measurement()==pytest.approx(dimension['value'],abs=1e-5)
        if dimension['kind']=='diameter':assert entity.dxf.dimtype&7 in (3,4)
    return jid,job,files


def test_real_text_missing_segment_length_revision_and_restore(tmp_path):
    work=tmp_path/'work';app=create_app(work,token=TOKEN)
    try:
        client=app.test_client()
        uploaded=client.post(API+'/modeling/inputs',headers=AUTH,json={'text':TEXT,'units':'mm'})
        assert uploaded.status_code==201,uploaded.data
        parent=create(client,uploaded.get_json()['bundle_id'],'shaft-text')
        assert parent['text']==TEXT and parent['analysis']['shaft_solid_confirmed'] is True
        assert not list(work.rglob('*.step')) and not list(work.rglob('model.log'))
        parent=answer_length_two(client,parent,'last-shaft-length')
        original_step,model=readback(client,parent,tmp_path,PARAMETERS)
        parent_job,parent_job_data,original_files=adopt_draw(client,parent,model,work,PARAMETERS,'shaft-original-drawing')
        parent=get(client,parent['session_id']);original_facts=json.loads(json.dumps(parent['analysis']['facts']))
        body={'expected_step_sha256':parent['step_sha256'],'expected_spec_sha256':parent['model_version']['spec_sha256'],
              'expected_revision':parent['model_version']['latest_number'],
              'changes':[{'op':'set_parameter','parameter':'length_1_mm','value_mm':35}]}
        endpoint=API+'/modeling/sessions/'+parent['session_id']+'/revisions'
        headers={**AUTH,'Idempotency-Key':'shift-actual-shoulder'}
        response=client.post(endpoint,headers=headers,json=body)
        assert response.status_code==201,response.data
        child=wait(client,response.get_json()['session_id'],('awaiting_adoption',))
        assert client.post(endpoint,headers=headers,json=body).get_json()['session_id']==child['session_id']
        assert child['analysis']['facts']==original_facts and child['analysis']['parameters']['length_1_mm']==30
        assert child['model_version']['parent_session_id']==parent['session_id']
        assert client.post(API+'/documents/'+child['session_id']+'/drawings',headers={**AUTH,'Idempotency-Key':'before-shaft-adoption'},
                           json={'step_sha256':child['step_sha256']}).status_code==409
        changed={**PARAMETERS,'length_1_mm':35.}
        child_step,model=readback(client,child,tmp_path,changed)
        child_job,child_job_data,child_files=adopt_draw(client,child,model,work,changed,'shifted-shoulder-drawing')
        assert child_job!=parent_job and child_step!=original_step
        assert client.get(parent['files']['step'],headers=AUTH).data==original_step
        for kind,path in parent_job_data['files'].items():assert client.get(path,headers=AUTH).data==original_files[kind]
        stop(app);app=create_app(work,token=TOKEN);client=app.test_client()
        restored=get(client,child['session_id']);assert restored['state']=='adopted' and restored['job_id']==child_job
        assert restored['analysis']['facts']==original_facts
        assert get(client,parent['session_id'])['job_id']==parent_job
        assert client.get(restored['files']['step'],headers=AUTH).data==child_step
        assert client.get(parent['files']['step'],headers=AUTH).data==original_step
        for job,expected in ((parent_job_data,original_files),(child_job_data,child_files)):
            for kind,path in job['files'].items():assert client.get(path,headers=AUTH).data==expected[kind]
        history=client.get(API+'/modeling/sessions/'+child['session_id']+'/history',headers=AUTH).get_json()
        assert history['latest_number']==2 and len(history['versions'])==2
    finally:stop(app)


def dxf_source():
    doc=ezdxf.new('R2010');doc.units=4;msp=doc.modelspace()
    msp.add_lwpolyline([(0,-20),(30,-20),(30,-10),(70,-10),(70,10),(30,10),(30,20),(0,20)],close=True)
    msp.add_text('STEPPED SHAFT',dxfattribs={'insert':(0,-35)})
    msp.add_text('SOLID',dxfattribs={'insert':(0,-43)})
    out=io.StringIO();doc.write(out);return out.getvalue().encode()


def pdf_source():
    lines=('SOLID STEPPED SHAFT','SEGMENT 1 DIAMETER=40 mm','SEGMENT 1 LENGTH=30 mm',
           'SEGMENT 2 DIAMETER=20 mm','SEGMENT 2 LENGTH=')
    commands=['0 0 0 RG 1 w 80 340 m 170 340 l 170 370 l 290 370 l 290 430 l 170 430 l 170 460 l 80 460 l h S']
    for index,text in enumerate(lines):commands.append(f'BT /F1 18 Tf 70 {780-index*35} Td ({text}) Tj ET')
    stream=('\n'.join(commands)+'\n').encode('ascii')
    objects=[b'<< /Type /Catalog /Pages 2 0 R >>',b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
        b'<< /Length '+str(len(stream)).encode()+b' >>\nstream\n'+stream+b'endstream']
    result=bytearray(b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\n');offsets=[]
    for index,value in enumerate(objects,1):
        offsets.append(len(result));result.extend(str(index).encode()+b' 0 obj\n'+value+b'\nendobj\n')
    xref=len(result);result.extend(b'xref\n0 6\n0000000000 65535 f \n')
    for offset in offsets:result.extend(f'{offset:010d} 00000 n \n'.encode())
    result.extend(b'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n'+str(xref).encode()+b'\n%%EOF\n')
    return bytes(result)


@pytest.mark.parametrize('format',('dxf','pdf'))
def test_real_shaft_drawing_input_clarification_step_and_2d(tmp_path,format):
    work=tmp_path/'work';app=create_app(work,token=TOKEN)
    try:
        client=app.test_client();source=dxf_source() if format=='dxf' else pdf_source()
        upload=client.post(API+'/modeling/drawing-inputs',headers=AUTH,
                           data={'file':(io.BytesIO(source),'shaft.'+format)})
        assert upload.status_code==201,upload.data
        bundle=upload.get_json();assert bundle['source_sha256']==hashlib.sha256(source).hexdigest()
        data=create(client,bundle['bundle_id'],'shaft-input-'+format)
        assert data['input_kind']=='drawing' and data['analysis']['kind']=='stepped_shaft'
        assert data['analysis']['shaft_solid_confirmed'] is True
        known=[f for f in data['analysis']['facts'] if f['parameter'] in PARAMETERS and f['value'] is not None]
        assert len(known)>=2
        if format=='pdf':
            assert any(f['evidence'].get('bbox') is not None and 'page_index' in f['evidence'] for f in known)
            assert data['state']=='needs_clarification'
            data=answer_length_two(client,data,'pdf-last-length')
        else:
            assert any(f['evidence'].get('entity_handle') for f in known)
            assert data['state']=='awaiting_adoption'
        preview=client.get(data['source_preview_url'],headers=AUTH)
        assert preview.status_code==200 and preview.data.startswith(b'\x89PNG')
        _,model=readback(client,data,tmp_path,PARAMETERS)
        adopt_draw(client,data,model,work,PARAMETERS,'input-shaft-drawing-'+format)
    finally:stop(app)

"""首包v2首段：真实文字→native STEP→既有出图链，拒绝未知输入。"""
import hashlib
import json
import time

import pytest

from drawform.service.app import create_app

AUTH = {'Authorization': 'Bearer first-loop-test'}
TEXT = '做一块长100毫米、宽60毫米、厚8毫米的板，中间开一个直径20毫米的通孔。'


def test_modeling_input_is_an_authenticated_immutable_bundle(tmp_path):
    app = create_app(tmp_path / 'work', token='first-loop-test')
    client = app.test_client()
    response = client.post('/api/workbench/v1/modeling/inputs', headers=AUTH,
                           json={'text': TEXT, 'units': 'mm'})
    assert response.status_code == 201, response.data
    data = response.get_json()
    assert data['text_sha256'] == hashlib.sha256(TEXT.encode()).hexdigest()
    assert data['text'] == TEXT
    assert client.post('/api/workbench/v1/modeling/inputs', json={'text': TEXT}).status_code == 401

from drawform.workbench.plate_spec import parse_plate, SpecError

@pytest.mark.parametrize('text',[
    TEXT,
    '100×60×8 mm 的板，中心通孔 Ø20 mm',
    'plate length 100 width 60 thickness 8 mm with center through hole diameter 20 mm',
])
def test_explicit_plate_text_has_exact_parameters(text):
    assert parse_plate(text)=={'length_mm':100.,'width_mm':60.,'thickness_mm':8.,'hole_diameter_mm':20.}

@pytest.mark.parametrize('text',[
    '长100、宽60的板，中心通孔直径20',
    '长100、宽60、厚8的板，中心盲孔直径20',
    '长100、宽60、厚8的板，中心通孔直径80',
    '长100、宽60、厚8的板，中心通孔直径20，再加一个凸台',
    '长100、宽60、厚8厘米的板，中心通孔直径20',
    '长100、宽60、厚8的板，中心通孔直径20，另一个孔直径10',
    '长100、宽60、厚8的板，中心通孔直径20，表面开槽',
])
def test_missing_or_unconsumed_requirements_never_become_a_silent_model(text):
    with pytest.raises(SpecError):parse_plate(text)


def wait_session(client,sid,timeout=180):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        response=client.get('/api/workbench/v1/modeling/sessions/'+sid,headers=AUTH)
        assert response.status_code==200,response.data
        data=response.get_json()
        if data['state'] not in ('queued','building'):return data
        time.sleep(.1)
    raise AssertionError('native model timed out')


def test_real_text_step_drawing_and_restart(tmp_path):
    app=create_app(tmp_path/'work',token='first-loop-test');client=app.test_client()
    bundle=client.post('/api/workbench/v1/modeling/inputs',headers=AUTH,json={'text':TEXT,'units':'mm'}).get_json()
    header={**AUTH,'Idempotency-Key':'first-real-loop'}
    posted=client.post('/api/workbench/v1/modeling/sessions',headers=header,json={'bundle_id':bundle['bundle_id'],'sheet':'A3'})
    assert posted.status_code==201,posted.data
    sid=posted.get_json()['session_id'];data=wait_session(client,sid)
    assert data['state']=='awaiting_adoption',data
    assert data['validation']['passed'] and data['parameters']['hole_diameter_mm']==20
    step=client.get(data['files']['step'],headers=AUTH)
    assert step.status_code==200 and b'ISO-10303-21' in step.data
    assert hashlib.sha256(step.data).hexdigest()==data['step_sha256']
    preview=client.get(data['files']['preview'],headers=AUTH).get_json()
    assert preview['step_sha256']==data['step_sha256'] and len(preview['faces'])>0
    drawing_url='/api/workbench/v1/documents/'+sid+'/drawings'
    assert client.post(drawing_url,headers=header,json={'step_sha256':data['step_sha256']}).status_code==409
    adopted=client.post('/api/workbench/v1/modeling/candidates/'+sid+'/adopt',headers=AUTH,json={'expected_sha256':data['step_sha256']})
    assert adopted.status_code==200,adopted.data
    drawing=client.post(drawing_url,headers=header,json={'step_sha256':data['step_sha256']})
    assert drawing.status_code==202,drawing.data
    job_id=drawing.get_json()['job_id']
    assert client.post(drawing_url,headers=header,json={'step_sha256':data['step_sha256']}).get_json()['job_id']==job_id
    deadline=time.monotonic()+120
    while time.monotonic()<deadline:
        job=client.get('/jobs/'+job_id,headers=AUTH).get_json()
        if job['state'] not in ('queued','running'):break
        time.sleep(.1)
    assert job['state']=='drawn',job
    assert set(job['files'])=={'dxf','svg','pdf','json'}
    from drawform.geometry.loader import import_step
    from drawform.geometry.features import extract_features,Bore
    model=import_step(tmp_path/'work'/'workbench'/'sessions'/sid/'artifacts'/'plate.step')
    assert model.is_valid and model.solid_count==1
    holes=[f for f in extract_features(model.shape,model.bbox) if isinstance(f,Bore)]
    assert len(holes)==1 and holes[0].diameter==pytest.approx(20) and holes[0].through
    import ezdxf,io
    dxf=client.get('/jobs/'+job_id+'/files/dxf',headers=AUTH)
    doc=ezdxf.read(io.StringIO(dxf.data.decode()))
    assert any(d.dxf.dimtype&7 in (3,4) and d.get_measurement()==pytest.approx(20) for d in doc.modelspace().query('DIMENSION'))
    assert client.get('/jobs/'+job_id+'/files/pdf',headers=AUTH).data.startswith(b'%PDF')
    restarted=create_app(tmp_path/'work',token='first-loop-test').test_client()
    restored=restarted.get('/api/workbench/v1/modeling/sessions/'+sid,headers=AUTH).get_json()
    assert restored['state']=='adopted' and restored['job_id']==job_id
    assert restarted.get(restored['files']['step'],headers=AUTH).data==step.data
    assert restarted.post(drawing_url,headers=header,json={'step_sha256':data['step_sha256']}).get_json()['job_id']==job_id
    assert client.post('/api/workbench/v1/modeling/sessions',headers=header,json={'bundle_id':bundle['bundle_id'],'sheet':'A2'}).status_code==409
    (tmp_path/'work'/'workbench'/'sessions'/sid/'artifacts'/'plate.step').write_bytes(b'corrupted')
    assert client.get(data['files']['step'],headers=AUTH).status_code==409


def test_bad_reference_and_unknown_requirements_are_4xx(tmp_path):
    c=create_app(tmp_path/'work',token='first-loop-test').test_client()
    for bad in ([],{},3,None):
        response=c.post('/api/workbench/v1/modeling/sessions',headers={**AUTH,'Idempotency-Key':'bad'},json={'bundle_id':bad})
        assert response.status_code==422,response.data
    for text in ('长100宽60厚8的圆形板，中心通孔直径20',
                 '长100宽60厚8的板，中心通孔直径20，要求方孔',
                 'plate length100 width60 thickness8 center through hole diameter20 with screw threads'):
        with pytest.raises(SpecError):parse_plate(text)


def test_stop_reaps_owned_process_group(tmp_path):
    import sys,threading
    from drawform.workbench.modeling import ModelingService
    from drawform.service.jobs import JobQueue
    service=ModelingService(tmp_path/'work',JobQueue(tmp_path/'work'))
    errors=[]
    def run():
        try:service.subprocess([sys.executable,'-c','import time;time.sleep(30)'],tmp_path,tmp_path/'child.log')
        except RuntimeError as e:errors.append(str(e))
    thread=threading.Thread(target=run);thread.start()
    deadline=time.monotonic()+5
    while not service.active and time.monotonic()<deadline:time.sleep(.01)
    assert service.active
    processes=list(service.active)
    service.stop();thread.join(timeout=5)
    assert not thread.is_alive() and all(p.poll() is not None for p in processes)
    assert errors

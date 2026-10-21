"""图纸闭环：输入真实DXF字节，提取/澄清后构建，不由客户端重传整组尺寸。"""
import io
import time
import hashlib
import json
import pytest
import ezdxf
from drawform.service.app import create_app

AUTH={'Authorization':'Bearer drawing-workflow-test'}


def drawing_bytes(tmp_path,*,thickness=None,kind='plate',override=False):
    doc=ezdxf.new('R2010');doc.units=4;m=doc.modelspace()
    if kind=='plate':
        m.add_lwpolyline([(0,0),(120,0),(120,80),(0,80)],close=True)
        m.add_circle((35,45),4);m.add_circle((85,35),6)
        m.add_text('PLATE')
        dim=m.add_linear_dim(base=(60,-15),p1=(0,0),p2=(120,0),angle=0)
        if override:dim.dimension.dxf.text='240'
        dim.render()
    else:
        m.add_circle((0,0),30);m.add_circle((0,0),10);m.add_text('WASHER')
    if thickness is not None:m.add_text(f'THICKNESS {thickness} mm',dxfattribs={'insert':(0,-25)})
    out=io.StringIO();doc.write(out);return out.getvalue().encode()


def test_drawing_upload_is_immutable_and_authenticated(tmp_path):
    c=create_app(tmp_path/'work',token='drawing-workflow-test').test_client()
    source=drawing_bytes(tmp_path)
    res=c.post('/api/workbench/v1/modeling/drawing-inputs',headers=AUTH,
               data={'file':(io.BytesIO(source),'part.dxf')},content_type='multipart/form-data')
    assert res.status_code==201,res.data
    body=res.get_json();assert body['input_kind']=='drawing' and body['source_sha256']==hashlib.sha256(source).hexdigest()
    assert c.post('/api/workbench/v1/modeling/drawing-inputs',data={'file':(io.BytesIO(source),'part.dxf')}).status_code==401


def wait_state(c,sid,states,timeout=180):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        data=c.get('/api/workbench/v1/modeling/sessions/'+sid,headers=AUTH).get_json()
        if data['state'] in states:return data
        if data['state'] in ('failed','interrupted'):raise AssertionError(data)
        time.sleep(.1)
    raise AssertionError('drawing workflow timed out')


def upload_session(c,source,key='drawing-case'):
    res=c.post('/api/workbench/v1/modeling/drawing-inputs',headers=AUTH,data={'file':(io.BytesIO(source),'source.dxf')})
    assert res.status_code==201,res.data
    bundle=res.get_json()
    made=c.post('/api/workbench/v1/modeling/sessions',headers={**AUTH,'Idempotency-Key':key},json={'bundle_id':bundle['bundle_id'],'sheet':'A3'})
    assert made.status_code==201,made.data
    return made.get_json()['session_id']


def test_missing_drawing_dimensions_clarify_then_real_step_and_2d(tmp_path):
    work=tmp_path/'work';c=create_app(work,token='drawing-workflow-test').test_client()
    source=drawing_bytes(tmp_path);sid=upload_session(c,source)
    data=wait_state(c,sid,{'needs_clarification'})
    assert data['analysis']['parameters']['length_mm']==120 and data['analysis']['parameters']['width_mm']==80
    assert data['analysis']['holes']==[{'x_mm':-25.0,'y_mm':5.0,'diameter_mm':8.0},{'x_mm':25.0,'y_mm':-5.0,'diameter_mm':12.0}]
    assert not list(work.rglob('model.step'))
    assert c.get(data['source_preview_url'],headers=AUTH).data.startswith(b'\x89PNG')
    restarted=create_app(work,token='drawing-workflow-test').test_client()
    restored=restarted.get('/api/workbench/v1/modeling/sessions/'+sid,headers=AUTH).get_json()
    assert restored['basis_sha256']==data['basis_sha256'] and restored['questions']==data['questions']
    answers={q['id']:(8.0 if q['parameter']=='thickness_mm' else 'through' if q['parameter']=='through' else 'mm') for q in data['questions']}
    body={'basis_sha256':data['basis_sha256'],'revision':data['clarification_revision'],'answers':answers}
    url='/api/workbench/v1/modeling/sessions/'+sid+'/answers';headers={**AUTH,'Idempotency-Key':'clarify-once'}
    assert restarted.post(url,headers=headers,json={**body,'basis_sha256':'wrong'}).status_code==409
    answer=restarted.post(url,headers=headers,json=body)
    assert answer.status_code==200,answer.data
    assert restarted.post(url,headers=headers,json=body).status_code==200
    ready=wait_state(restarted,sid,{'awaiting_adoption'})
    assert ready['spec']['kind']=='plate' and len(ready['spec']['holes'])==2
    assert ready['spec']['parameters']=={'length_mm':120.,'width_mm':80.,'thickness_mm':8.}
    assert any(f['evidence']['type']=='user_answer' for f in ready['analysis']['facts'])
    step=restarted.get(ready['files']['step'],headers=AUTH)
    assert step.status_code==200 and b'ISO-10303-21' in step.data
    assert restarted.post('/api/workbench/v1/modeling/candidates/'+sid+'/adopt',headers=AUTH,json={'expected_sha256':ready['step_sha256']}).status_code==200
    post=restarted.post('/api/workbench/v1/documents/'+sid+'/drawings',headers={**AUTH,'Idempotency-Key':'drawing-once'},json={'step_sha256':ready['step_sha256']})
    assert post.status_code==202,post.data
    jid=post.get_json()['job_id'];deadline=time.monotonic()+120
    while time.monotonic()<deadline:
        job=restarted.get('/jobs/'+jid,headers=AUTH).get_json()
        if job['state'] not in ('queued','running'):break
        time.sleep(.1)
    assert job['state']=='drawn',job
    assert set(job['files'])=={'dxf','svg','pdf','json'}
    dxf=ezdxf.read(io.StringIO(restarted.get('/jobs/'+jid+'/files/dxf',headers=AUTH).data.decode()))
    measurements=[d.get_measurement() for d in dxf.modelspace().query('DIMENSION')]
    assert any(v==pytest.approx(8) for v in measurements) and any(v==pytest.approx(12) for v in measurements)
    assert hashlib.sha256(source).hexdigest()==ready['source_sha256']


def test_drawing_conflicts_cannot_be_overridden_into_model(tmp_path):
    work=tmp_path/'work';c=create_app(work,token='drawing-workflow-test').test_client()
    sid=upload_session(c,drawing_bytes(tmp_path,thickness=8,override=True))
    data=wait_state(c,sid,{'needs_clarification'})
    blocked=[q for q in data['questions'] if q.get('type')=='blocked' or q.get('readonly')]
    assert blocked and not list(work.rglob('model.step'))
    response=c.post('/api/workbench/v1/modeling/sessions/'+sid+'/answers',headers={**AUTH,'Idempotency-Key':'cannot-override'},json={'basis_sha256':data['basis_sha256'],'revision':data['clarification_revision'],'answers':{blocked[0]['id']:120}})
    assert response.status_code==422,response.data
    assert c.get('/api/workbench/v1/modeling/sessions/'+sid+'/files/step',headers=AUTH).status_code==404


@pytest.mark.parametrize('filename,payload',[('fake.pdf',b'not PDF'),('x.exe',b'not image'),('../x.png',b'not png')])
def test_upload_rejects_fake_formats_without_native_side_effects(tmp_path,filename,payload):
    work=tmp_path/'work';c=create_app(work,token='drawing-workflow-test').test_client()
    res=c.post('/api/workbench/v1/modeling/drawing-inputs',headers=AUTH,data={'file':(io.BytesIO(payload),filename)})
    assert res.status_code==422
    assert not list(work.rglob('source.*'))


def raster_bytes(format):
    from PIL import Image,ImageDraw,ImageFont
    image=Image.new('RGB',(1600,1200),'white');d=ImageDraw.Draw(image)
    d.rectangle((200,180,1000,660),outline='black',width=5);d.ellipse((520,340,680,500),outline='black',width=5)
    font=ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial.ttf',44)
    for i,text in enumerate(('RECTANGULAR PLATE','length=100 mm','width=60 mm','thickness=8 mm','CENTER THROUGH HOLE DIAMETER=20 mm')):
        d.text((70,760+i*65),text,font=font,fill='black')
    out=io.BytesIO();image.save(out,{'png':'PNG','pdf':'PDF','jpeg':'JPEG'}[format],resolution=150,quality=95);return out.getvalue()


@pytest.mark.parametrize('format,extension',[('png','png'),('pdf','pdf'),('jpeg','jpg')])
def test_actual_image_pdf_upload_reaches_step_and_2d_without_retyping_dimensions(tmp_path,format,extension):
    c=create_app(tmp_path/'work',token='drawing-workflow-test').test_client();source=raster_bytes(format)
    uploaded=c.post('/api/workbench/v1/modeling/drawing-inputs',headers=AUTH,data={'file':(io.BytesIO(source),'drawing.'+extension)})
    assert uploaded.status_code==201,uploaded.data
    made=c.post('/api/workbench/v1/modeling/sessions',headers={**AUTH,'Idempotency-Key':'raster'},json={'bundle_id':uploaded.get_json()['bundle_id'],'sheet':'A3'})
    assert made.status_code==201,made.data
    sid=made.get_json()['session_id'];data=wait_state(c,sid,{'needs_clarification','awaiting_adoption'})
    if data['state']=='needs_clarification':
        assert len(data['questions'])<=1 and all(q['parameter'] in ('length_mm','width_mm') for q in data['questions']),data
        answers={q['id']:100 if q['parameter']=='length_mm' else 60 for q in data['questions']}
        res=c.post('/api/workbench/v1/modeling/sessions/'+sid+'/answers',headers={**AUTH,'Idempotency-Key':'one-unreadable-label'},json={'basis_sha256':data['basis_sha256'],'revision':data['clarification_revision'],'answers':answers})
        assert res.status_code==200,res.data
        data=wait_state(c,sid,{'awaiting_adoption'})
    assert data['parameters']=={'length_mm':100.,'width_mm':60.,'thickness_mm':8.}
    assert data['spec']['holes']==[{'x_mm':0.,'y_mm':0.,'diameter_mm':20.}]
    assert data['analysis']['through_basis']=='source_explicit'
    assert any(f['evidence'].get('bbox') for f in data['analysis']['facts'])
    assert c.get(data['files']['step'],headers=AUTH).status_code==200
    assert c.post('/api/workbench/v1/modeling/candidates/'+sid+'/adopt',headers=AUTH,json={'expected_sha256':data['step_sha256']}).status_code==200
    post=c.post('/api/workbench/v1/documents/'+sid+'/drawings',headers={**AUTH,'Idempotency-Key':'raster-drawing'},json={'step_sha256':data['step_sha256']})
    jid=post.get_json()['job_id'];deadline=time.monotonic()+120
    while time.monotonic()<deadline:
        job=c.get('/jobs/'+jid,headers=AUTH).get_json()
        if job['state'] not in ('queued','running'):break
        time.sleep(.1)
    assert job['state']=='drawn',job
    assert set(job['files'])=={'dxf','svg','pdf','json'}


def test_duplicate_upload_fields_and_overflow_answers_are_controlled(tmp_path):
    from werkzeug.datastructures import MultiDict
    c=create_app(tmp_path/'work',token='drawing-workflow-test').test_client();source=drawing_bytes(tmp_path)
    res=c.post('/api/workbench/v1/modeling/drawing-inputs',headers=AUTH,data=MultiDict([('file',(io.BytesIO(source),'a.dxf')),('file',(io.BytesIO(source),'b.dxf'))]))
    assert res.status_code==422
    sid=upload_session(c,source);data=wait_state(c,sid,{'needs_clarification'})
    q=next(q for q in data['questions'] if q['parameter']=='thickness_mm')
    for value in (True,10**400,float('nan')):
        res=c.post('/api/workbench/v1/modeling/sessions/'+sid+'/answers',headers={**AUTH,'Idempotency-Key':'invalid-'+str(type(value))},json={'basis_sha256':data['basis_sha256'],'revision':data['clarification_revision'],'answers':{q['id']:value}})
        assert res.status_code==422,res.data


def test_source_change_invalidates_clarification_basis(tmp_path):
    work=tmp_path/'work';c=create_app(work,token='drawing-workflow-test').test_client();sid=upload_session(c,drawing_bytes(tmp_path));data=wait_state(c,sid,{'needs_clarification'})
    source=work/'workbench'/'inputs'/data['bundle_id']/'source.dxf';source.write_bytes(b'changed source')
    q=next(q for q in data['questions'] if q['parameter']=='thickness_mm')
    res=c.post('/api/workbench/v1/modeling/sessions/'+sid+'/answers',headers={**AUTH,'Idempotency-Key':'old-basis'},json={'basis_sha256':data['basis_sha256'],'revision':data['clarification_revision'],'answers':{q['id']:8}})
    assert res.status_code==409
    assert not list(work.rglob('model.step'))


def test_simple_washer_text_uses_explicit_new_dimensions(tmp_path):
    c=create_app(tmp_path/'work',token='drawing-workflow-test').test_client()
    bundle=c.post('/api/workbench/v1/modeling/inputs',headers=AUTH,json={'text':'垫圈 外径60毫米 内径20毫米 厚度8毫米','units':'mm'}).get_json()
    made=c.post('/api/workbench/v1/modeling/sessions',headers={**AUTH,'Idempotency-Key':'washer-text'},json={'bundle_id':bundle['bundle_id'],'sheet':'A3'})
    assert made.status_code==201,made.data
    data=wait_state(c,made.get_json()['session_id'],{'awaiting_adoption'})
    assert data['model_kind']=='washer' and data['spec']['parameters']=={'outer_diameter_mm':60.,'inner_diameter_mm':20.,'thickness_mm':8.}
    assert c.get(data['files']['step'],headers=AUTH).status_code==200


def test_unexplained_raster_note_is_visible_and_cannot_be_overridden(tmp_path):
    from PIL import Image,ImageDraw,ImageFont
    image=Image.open(io.BytesIO(raster_bytes('png')))
    font=ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial.ttf',44)
    ImageDraw.Draw(image).text((70,30),'REMOVE A WEDGE',font=font,fill='black')
    buf=io.BytesIO();image.save(buf,'PNG')
    work=tmp_path/'work';c=create_app(work,token='drawing-workflow-test').test_client()
    uploaded=c.post('/api/workbench/v1/modeling/drawing-inputs',headers=AUTH,data={'file':(io.BytesIO(buf.getvalue()),'unexplained.png')})
    assert uploaded.status_code==201,uploaded.data
    made=c.post('/api/workbench/v1/modeling/sessions',headers={**AUTH,'Idempotency-Key':'unexplained'},json={'bundle_id':uploaded.get_json()['bundle_id'],'sheet':'A3'})
    assert made.status_code==201,made.data
    sid=made.get_json()['session_id']
    data=wait_state(c,sid,{'needs_clarification','awaiting_adoption'})
    assert data['state']=='needs_clarification',data
    assert data['analysis']['unsupported']=='unexplained_annotation'
    facts=[f for f in data['analysis']['facts'] if f['parameter']=='unexplained_annotation']
    assert facts and any('WEDGE' in f['value'].upper() and f['evidence'].get('bbox') is not None for f in facts)
    assert c.get(data['source_preview_url'],headers=AUTH).status_code==200
    blocked=next(q for q in data['questions'] if q['type']=='blocked')
    answer=c.post('/api/workbench/v1/modeling/sessions/'+sid+'/answers',headers={**AUTH,'Idempotency-Key':'no-override'},json={'basis_sha256':data['basis_sha256'],'revision':data['clarification_revision'],'answers':{blocked['id']:'ignore'}})
    assert answer.status_code==422,answer.data
    assert c.get('/api/workbench/v1/modeling/sessions/'+sid+'/files/step',headers=AUTH).status_code==404
    assert not list(work.rglob('model.step'))

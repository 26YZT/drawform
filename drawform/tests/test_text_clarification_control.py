"""HTTP answer guards; CAD is held at the queue boundary in these fast tests."""
import hashlib
import json
import pytest
from drawform.service.app import create_app

API='/api/workbench/v1/modeling'
TOKEN='text-control-test'
AUTH={'Authorization':'Bearer '+TOKEN}

@pytest.fixture
def env(tmp_path,monkeypatch):
    app=create_app(tmp_path/'work',token=TOKEN+',other-seat')
    s=app.extensions['drawform_modeling'];enqueued=[]
    # Preserve the real consumer's stop signal while observing requests to queue.
    put=s.pending.put
    monkeypatch.setattr(s.pending,'put',lambda item:put(item) if item is None else enqueued.append(item))
    yield app.test_client(),s,enqueued
    s.stop();s.jobs.stop()


def start(env,text):
    c,s,queued=env
    inp=c.post(API+'/inputs',headers=AUTH,json={'text':text}).get_json()
    res=c.post(API+'/sessions',headers={**AUTH,'Idempotency-Key':'create'},json={'bundle_id':inp['bundle_id']})
    assert res.status_code==201,res.data
    data=res.get_json();assert data['state']=='needs_clarification'
    assert not queued
    return data


def answer(env,data,values,key='answer'):
    c,s,queued=env
    lookup={q['parameter']:q['id'] for q in data['questions']}
    body={'basis_sha256':data['basis_sha256'],'revision':data['clarification_revision'],
          'answers':{lookup.get(k,k):v for k,v in values.items()}}
    return c.post(API+'/sessions/'+data['session_id']+'/answers',headers={**AUTH,'Idempotency-Key':key},json=body),body


def test_missing_only_then_exact_spec_and_immutable_text(env):
    text='做一块长100毫米、宽60毫米的板，中间开一个直径20毫米的通孔。'
    data=start(env,text)
    assert [q['parameter'] for q in data['questions']]==['thickness_mm']
    res,body=answer(env,data,{'thickness_mm':8})
    assert res.status_code==200,res.data
    ready=res.get_json();assert ready['state']=='queued'
    assert ready['text']==text and ready['source_sha256']==hashlib.sha256(text.encode()).hexdigest()
    assert ready['spec']['parameters']=={'length_mm':100.,'width_mm':60.,'thickness_mm':8.}
    assert ready['spec']['holes']==[{'x_mm':0.,'y_mm':0.,'diameter_mm':20.}]
    assert len(env[2])==1
    replay=env[0].post(API+'/sessions/'+data['session_id']+'/answers',headers={**AUTH,'Idempotency-Key':'answer'},json=body)
    assert replay.status_code==200 and len(env[2])==1
    assert len(ready['clarifications'])==1


@pytest.mark.parametrize('bad',[0,-1,True,'8',float('nan'),float('inf'),1001])
def test_invalid_missing_dimension_does_not_mutate_or_queue(env,bad):
    data=start(env,'做一块长100毫米、宽60毫米的板，中间开一个直径20毫米的通孔。')
    res,_=answer(env,data,{'thickness_mm':bad})
    assert res.status_code==422,res.data
    restored=env[1].get(data['session_id'],_owner(env))
    assert restored['basis_sha256']==data['basis_sha256'] and not env[2]


def _owner(env):
    with env[1].connect() as db:return db.execute('SELECT owner FROM sessions').fetchone()['owner']


def test_answers_reject_known_fields_duplicate_keys_other_seat_and_stale_basis(env):
    data=start(env,'做一块长100毫米、宽60毫米的板，中间开一个直径20毫米的通孔。')
    res,body=answer(env,data,{'length_mm':200})
    assert res.status_code==422
    c=env[0];url=API+'/sessions/'+data['session_id']+'/answers'
    body={'basis_sha256':data['basis_sha256'],'revision':data['clarification_revision'],'answers':{data['questions'][0]['id']:8}}
    assert c.post(url,headers={**AUTH,'Idempotency-Key':'stale'},json={**body,'basis_sha256':'0'*64}).status_code==409
    assert c.post(url,headers={'Authorization':'Bearer other-seat','Idempotency-Key':'cross'},json=body).status_code==404
    raw=json.dumps(body).replace(': 8}',': 8, '+json.dumps(data['questions'][0]['id'])+': 12}')
    assert c.post(url,headers={**AUTH,'Idempotency-Key':'dup','Content-Type':'application/json'},data=raw).status_code==422
    assert not env[2]


def test_source_changes_reject_answer_and_receipt_replay(env):
    data=start(env,'做一块长100毫米、宽60毫米的板，中间开一个直径20毫米的通孔。')
    res,body=answer(env,data,{'thickness_mm':8});assert res.status_code==200
    path=env[1].root/'inputs'/(data['bundle_id']+'.json')
    original=path.read_bytes();path.write_text('{}')
    c=env[0];url=API+'/sessions/'+data['session_id']+'/answers'
    assert c.post(url,headers={**AUTH,'Idempotency-Key':'answer'},json=body).status_code==409
    path.write_bytes(original)


def test_partial_answers_and_dynamic_center_hole_questions(env):
    data=start(env,'做一块长100、宽60的矩形板。')
    assert {q['parameter'] for q in data['questions']}=={'thickness_mm','units','hole_mode'}
    res,_=answer(env,data,{'hole_mode':'center_through'},'mode');assert res.status_code==200,res.data
    next=res.get_json();assert {q['parameter'] for q in next['questions']}=={'thickness_mm','units','holes.0.diameter_mm'}
    assert not env[2]
    res,_=answer(env,next,{'thickness_mm':8,'units':'mm','holes.0.diameter_mm':20},'finish')
    assert res.status_code==200,res.data
    assert res.get_json()['state']=='queued' and len(env[2])==1
    assert all(f['unit']=='mm' for f in res.get_json()['clarifications'][-3:])


def test_signed_xy_answers_and_explicit_origin(env):
    data=start(env,'矩形板，长120毫米，宽80毫米，厚8毫米，一个通孔。孔1：直径10毫米，X=，Y=。')
    required={q['parameter'] for q in data['questions']}
    assert {'holes.0.x_mm','holes.0.y_mm','hole_origin'}<=required
    res,_=answer(env,data,{'holes.0.x_mm':-20,'holes.0.y_mm':0,'hole_origin':'plate_center'})
    assert res.status_code==200,res.data
    assert res.get_json()['spec']['holes'][0]=={'x_mm':-20.,'y_mm':0.,'diameter_mm':10.}


def test_outside_hole_is_rejected_before_queue(env):
    data=start(env,'做一块长100毫米、宽60毫米、厚8毫米的矩形板。')
    res,_=answer(env,data,{'hole_mode':'center_through'},'mode');assert res.status_code==200
    next=res.get_json()
    res,_=answer(env,next,{'holes.0.diameter_mm':60},'bad-hole')
    assert res.status_code==422 and not env[2]
    restored=env[1].get(data['session_id'],_owner(env))
    assert restored['basis_sha256']==next['basis_sha256']


def test_deeply_nested_answer_json_is_422_without_state_change(env):
    data=start(env,'做一块长100毫米、宽60毫米的板，中间开一个直径20毫米的通孔。')
    qid=data['questions'][0]['id']
    raw='{"basis_sha256":'+json.dumps(data['basis_sha256'])+',"revision":1,"answers":{'+json.dumps(qid)+':'+'['*10000+'0'+']'*10000+'}}'
    res=env[0].post(API+'/sessions/'+data['session_id']+'/answers',
        headers={**AUTH,'Idempotency-Key':'deep-json','Content-Type':'application/json'},data=raw)
    assert res.status_code==422,res.data
    assert env[1].get(data['session_id'],_owner(env))['basis_sha256']==data['basis_sha256']
    assert not env[2]

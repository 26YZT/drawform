"""Finite shaft clarification control over the real HTTP/source archive seam."""
import hashlib
import pytest
from drawform.service.app import create_app

API='/api/workbench/v1/modeling';AUTH={'Authorization':'Bearer shaft-control'}
TEXT='实心阶梯轴，第1段直径40毫米长30毫米，第2段直径20毫米'

@pytest.fixture
def env(tmp_path,monkeypatch):
    app=create_app(tmp_path/'work',token='shaft-control,shaft-other');s=app.extensions['drawform_modeling'];queued=[];put=s.pending.put
    monkeypatch.setattr(s.pending,'put',lambda item:put(item) if item is None else queued.append(item))
    yield app.test_client(),s,queued
    s.stop();s.jobs.stop()

def start(env,text=TEXT):
    c,s,q=env;inp=c.post(API+'/inputs',headers=AUTH,json={'text':text}).get_json()
    res=c.post(API+'/sessions',headers={**AUTH,'Idempotency-Key':'make'},json={'bundle_id':inp['bundle_id']})
    assert res.status_code==201,res.data
    data=res.get_json();assert data['state']=='needs_clarification' and not q
    assert data['input_kind']=='text' and data['source_sha256']==hashlib.sha256(text.encode()).hexdigest()
    assert 'thickness_mm' not in data['parameters']
    return data

def answer(env,data,values,key='answer'):
    ids={q['parameter']:q['id'] for q in data['questions']}
    body={'basis_sha256':data['basis_sha256'],'revision':data['clarification_revision'],'answers':{ids.get(k,k):v for k,v in values.items()}}
    return env[0].post(API+'/sessions/'+data['session_id']+'/answers',headers={**AUTH,'Idempotency-Key':key},json=body),body

def test_missing_second_length_only_queues_after_answer(env):
    d=start(env);assert [q['parameter'] for q in d['questions']]==['length_2_mm']
    res,body=answer(env,d,{'length_2_mm':40});assert res.status_code==200,res.data
    data=res.get_json();assert data['state']=='queued' and len(env[2])==1
    assert data['spec']=={'schema_version':2,'kind':'stepped_shaft','parameters':{'diameter_1_mm':40.,'length_1_mm':30.,'diameter_2_mm':20.,'length_2_mm':40.},'holes':[]}
    assert data['analysis']['shaft_solid_confirmed']
    assert data['clarifications'][0]['parameter']=='length_2_mm'
    assert data['text']==TEXT
    replay=env[0].post(API+'/sessions/'+d['session_id']+'/answers',headers={**AUTH,'Idempotency-Key':'answer'},json=body)
    assert replay.status_code==200 and len(env[2])==1

@pytest.mark.parametrize('value',[0,-1,True,'40',float('nan'),float('inf'),1001,971])
def test_bad_length_or_total_keeps_question_and_cad_queue_empty(env,value):
    d=start(env);res,_=answer(env,d,{'length_2_mm':value});assert res.status_code==422,res.data
    restored=env[0].get(API+'/sessions/'+d['session_id'],headers=AUTH).get_json()
    assert restored['basis_sha256']==d['basis_sha256'] and restored['questions']==d['questions']
    assert not env[2]

def test_solid_and_dimensions_can_be_answered_in_separate_rounds(env):
    d=start(env,TEXT.replace('实心',''))
    assert {q['parameter'] for q in d['questions']}=={'shaft_solid','length_2_mm'}
    res,_=answer(env,d,{'length_2_mm':40},'length');assert res.status_code==200,res.data
    mid=res.get_json();assert mid['state']=='needs_clarification' and not env[2]
    assert [q['parameter'] for q in mid['questions']]==['shaft_solid']
    res,_=answer(env,mid,{'shaft_solid':'solid'},'solid');assert res.status_code==200,res.data
    assert res.get_json()['state']=='queued' and len(env[2])==1
    assert answer(env,d,{'shaft_solid':'solid'},'stale')[0].status_code==409

def test_same_diameter_answer_rolls_back_before_missing_other_fields(env):
    d=start(env,'实心阶梯轴，第1段长度30毫米，第2段直径20毫米')
    res,_=answer(env,d,{'diameter_1_mm':20},'same');assert res.status_code==422,res.data
    restored=env[0].get(API+'/sessions/'+d['session_id'],headers=AUTH).get_json()
    assert restored['basis_sha256']==d['basis_sha256'] and not env[2]

def test_answers_cannot_override_known_values_or_cross_seats(env):
    d=start(env)
    assert answer(env,d,{'diameter_1_mm':50},'known')[0].status_code==422
    res,body=answer(env,d,{'length_2_mm':40},'valid');assert res.status_code==200
    url=API+'/sessions/'+d['session_id']+'/answers'
    assert env[0].post(url,headers={'Authorization':'Bearer shaft-other','Idempotency-Key':'other'},json=body).status_code==404

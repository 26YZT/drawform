"""Attempt lifetime, frozen intent, CAS and bounded restart controls; no CAD mocks as geometry evidence."""
import hashlib
import json
import threading
import pytest
from drawform.service.jobs import JobQueue
from drawform.workbench.modeling import ModelingService,Conflict

OWNER='a'*64
class NoWorker:
    def __init__(self,*a,**kw):pass
    def start(self):pass
    def join(self,timeout=None):pass

@pytest.fixture
def factory(tmp_path,monkeypatch):
    monkeypatch.setattr(threading,'Thread',NoWorker)
    services=[]
    def make():
        service=ModelingService(tmp_path/'work',JobQueue(tmp_path/'work'),auto_resume=True);services.append(service);return service
    yield make
    for s in services:s.stop()

def seed(s):
    bundle=s.add_input('矩形板长100宽60厚8毫米，中心直径20毫米通孔','mm',OWNER)
    return s.create(bundle['bundle_id'],'A3','model-request',OWNER)['session_id']

def test_restart_uses_new_attempt_preserving_old_bytes_and_same_version(factory):
    first=factory();sid=seed(first);a,path=first.attempts.begin(sid,OWNER,'build')
    (path/'partial.step').write_bytes(b'old incomplete evidence')
    second=factory()
    assert second.raw(sid,OWNER)['state']=='queued'
    b,path2=second.attempts.begin(sid,OWNER,'build')
    assert a!=b and path2!=path and (path/'partial.step').read_bytes()==b'old incomplete evidence'
    assert second.get(sid,OWNER)['model_version']['number']==1
    history=second.attempts.history(sid,OWNER)
    assert [x['state'] for x in history]==['interrupted','running']
    assert history[0]['retained_files']['partial.step']==hashlib.sha256(b'old incomplete evidence').hexdigest()

def test_late_old_attempt_cannot_commit(factory):
    s=factory();sid=seed(s);old,_=s.attempts.begin(sid,OWNER,'build');new,_=s.attempts.begin(sid,OWNER,'build')
    with pytest.raises(Conflict):s.attempts.complete(sid,OWNER,old,'awaiting_adoption',{'step_sha256':'b'*64})
    assert s.raw(sid,OWNER)['current_attempt_id']==new and not s.raw(sid,OWNER).get('step_sha256')

@pytest.mark.parametrize('change',['source','spec','intent'])
def test_changed_basis_blocks_automatic_restart(factory,change):
    s=factory();sid=seed(s);s.attempts.begin(sid,OWNER,'build')
    d=s.raw(sid,OWNER)
    if change=='source':(s.root/'inputs'/(d['bundle_id']+'.json')).write_bytes(b'changed')
    else:
        if change=='spec':d['parameters']['thickness_mm']=9
        else:d['execution_intent_sha256']='0'*64
        with s.connect() as db:db.execute('UPDATE sessions SET data=? WHERE id=?',(json.dumps(d),sid));db.commit()
    resumed=factory();assert resumed.raw(sid,OWNER)['state']=='interrupted' and resumed.raw(sid,OWNER)['recovery_blocked']
    assert len(resumed.attempts.history(sid,OWNER))==1

def test_three_recoveries_are_persistent_and_do_not_loop_forever(factory):
    s=factory();sid=seed(s)
    for _ in range(4):
        s.attempts.begin(sid,OWNER,'build');s=factory()
    assert s.raw(sid,OWNER)['state']=='interrupted' and s.raw(sid,OWNER)['recovery_blocked']
    assert len(s.attempts.history(sid,OWNER))==4

@pytest.mark.parametrize('state',['failed','cancel_requested'])
def test_failures_and_cancellations_do_not_automatically_retry(factory,state):
    s=factory();sid=seed(s);a,_=s.attempts.begin(sid,OWNER,'build');s.attempts.set(a,'failed',reason='timeout')
    s.update(sid,OWNER,state,error='explicit terminal cause')
    resumed=factory();assert resumed.raw(sid,OWNER)['state'] in ('failed','interrupted')
    assert len(resumed.attempts.history(sid,OWNER))==1

def test_fresh_queued_intent_is_recovered_without_allocating_new_design_version(factory):
    s=factory();sid=seed(s);new=factory()
    assert new.raw(sid,OWNER)['state']=='queued' and new.get(sid,OWNER)['model_version']['number']==1
    assert new.create(new.raw(sid,OWNER)['bundle_id'],'A3','model-request',OWNER)['session_id']==sid

def test_clarification_freezes_the_completed_build_intent_before_queueing(factory):
    s=factory();bundle=s.add_input('做一块长100毫米、宽60毫米的板，中间开一个直径20毫米的通孔','mm',OWNER)
    d=s.create(bundle['bundle_id'],'A3','missing-thickness',OWNER)
    assert d['state']=='needs_clarification'
    answers={q['id']:8. for q in d['questions']}
    result=s.clarifications.answer(d['session_id'],OWNER,{'basis_sha256':d['basis_sha256'],'revision':d['clarification_revision'],'answers':answers},'answer')
    assert result['state']=='queued' and result['execution_intent']['kind']=='build'
    identity,_=s.attempts.begin(d['session_id'],OWNER,'build')
    assert identity

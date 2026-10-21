"""SQLite/API controls with synthetic saved assets; native geometry is tested separately."""
import hashlib
import json
import uuid
import pytest
from drawform.service.app import create_app
from drawform.workbench.modeling import save_json

TOKEN='revision-control-test'
AUTH={'Authorization':'Bearer '+TOKEN}
SPEC={'schema_version':2,'kind':'plate','parameters':{'length_mm':100.0,'width_mm':60.0,'thickness_mm':8.0},'holes':[{'x_mm':0.0,'y_mm':0.0,'diameter_mm':20.0}]}

def spec_sha(spec):
    return hashlib.sha256(json.dumps(spec,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()

@pytest.fixture
def saved(tmp_path):
    app=create_app(tmp_path/'work',token=TOKEN);s=app.extensions['drawform_modeling']
    s.stop();s.stopped.clear()  # No worker in these transaction-only unit tests.
    owner=hashlib.sha256(TOKEN.encode()).hexdigest();sid=uuid.uuid4().hex
    bundle=s.add_input('矩形板长100宽60厚8毫米，中心通孔直径20毫米','mm',owner)
    out=s.directory(sid)/'artifacts';out.mkdir(parents=True)
    (out/'plate.step').write_bytes(b'synthetic-saved-step-for-transaction-test')
    sha=hashlib.sha256((out/'plate.step').read_bytes()).hexdigest()
    for name in ['preview.json','validation.json','drawform-validation.json']:save_json(out/name,{'step_sha256':sha,'passed':True})
    hashes={k:hashlib.sha256((out/v).read_bytes()).hexdigest() for k,v in {'step':'plate.step','preview':'preview.json','validation':'validation.json','drawform_validation':'drawform-validation.json'}.items()}
    data={'session_id':sid,'bundle_id':bundle['bundle_id'],'text':bundle['text'],'text_sha256':bundle['text_sha256'],'source_sha256':bundle['text_sha256'],'input_kind':'text','parameters':{'length_mm':100.,'width_mm':60.,'thickness_mm':8.,'hole_diameter_mm':20.},'sheet':'A3','candidate_id':sid,'created_at':'2026-10-18T00:00:00+00:00','job_id':None,'error':'','human_review':'not_reviewed','step_sha256':sha,'artifact_hashes':hashes}
    with s.connect() as db:db.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?)',(sid,owner,'seed','seed','awaiting_adoption',json.dumps(data)));db.commit()
    yield app.test_client(),s,sid,sha,owner
    s.stop()

def body(sha,**extra):
    return {'expected_step_sha256':sha,'expected_spec_sha256':spec_sha(SPEC),'expected_revision':1,'changes':[{'op':'set_hole','hole_id':'hole_1','diameter_mm':12}],**extra}

def revise(c,sid,sha,key='rev-1',**extra):
    return c.post('/api/workbench/v1/modeling/sessions/'+sid+'/revisions',headers={**AUTH,'Idempotency-Key':key},json=body(sha,**extra))

def test_revision_creates_new_candidate_without_overwriting_parent(saved):
    c,s,sid,sha,owner=saved
    res=revise(c,sid,sha)
    assert res.status_code==201,res.data
    child=res.get_json();assert child['session_id']!=sid and child['state']=='queued'
    assert child['spec']['holes'][0]['diameter_mm']==12
    assert child['model_version']['parent_session_id']==sid and child['model_version']['number']==2
    old=s.get(sid,owner);assert old['step_sha256']==sha and old['parameters']['hole_diameter_mm']==20
    assert s.file(sid,owner,'step').read_bytes()==b'synthetic-saved-step-for-transaction-test'

def test_version_basis_receipts_history_and_branches(saved):
    c,s,sid,sha,owner=saved
    first=revise(c,sid,sha);child=first.get_json()['session_id']
    repeat=revise(c,sid,sha);assert repeat.status_code==201 and repeat.get_json()['session_id']==child
    assert revise(c,sid,sha,changes=[{'op':'set_hole','hole_id':'hole_1','diameter_mm':14}]).status_code==409
    assert revise(c,sid,sha,key='stale').status_code==409
    assert revise(c,sid,sha,key='wrong-step',expected_step_sha256='0'*64,expected_revision=2).status_code==409
    assert revise(c,sid,sha,key='wrong-spec',expected_spec_sha256='0'*64,expected_revision=2).status_code==409
    second=revise(c,sid,sha,key='branch',expected_revision=2,changes=[{'op':'set_parameter','parameter':'thickness_mm','value_mm':10}])
    assert second.status_code==201 and second.get_json()['model_version']['number']==3
    history=c.get('/api/workbench/v1/modeling/sessions/'+child+'/history',headers=AUTH).get_json()
    assert [v['number'] for v in history['versions']]==[1,2,3]
    assert history['versions'][1]['parent_session_id']==sid==history['versions'][2]['parent_session_id']
    assert s.get(sid,owner)['editable_spec']==SPEC
    assert s.get(sid,owner)['model_version']['latest_number']==3

@pytest.mark.parametrize('changes',[
    [],[{'op':'exec','code':'print(1)'}],
    [{'op':'set_parameter','parameter':'__class__','value_mm':10}],
    [{'op':'set_parameter','parameter':[], 'value_mm':10}],
    [{'op':'set_parameter','parameter':'length_mm','value_mm':'100+20'}],
    [{'op':'set_parameter','parameter':'length_mm','value_mm':True}],
    [{'op':'set_parameter','parameter':'length_mm','value_mm':10**1000}],
    [{'op':'set_parameter','parameter':'length_mm','value_mm':100}],
    [{'op':'set_parameter','parameter':'length_mm','value_mm':120},{'op':'set_parameter','parameter':'length_mm','value_mm':130}],
    [{'op':'set_hole','hole_id':'hole_0','diameter_mm':12}],
    [{'op':'set_hole','hole_id':'hole_2','diameter_mm':12}],
    [{'op':'set_hole','hole_id':'hole_1','x_mm':50}],
    [{'op':'set_hole','hole_id':'hole_1','diameter_mm':60}],
    [{'op':'set_hole','hole_id':'hole_1','diameter_mm':12,'path':'../output'}],
    [{'op':'set_hole','hole_id':'hole_1','diameter_mm':12},{'op':'set_hole','hole_id':'hole_1','y_mm':5}],
])
def test_invalid_changes_create_no_candidate(saved,changes):
    c,s,sid,sha,owner=saved
    response=revise(c,sid,sha,changes=changes)
    assert response.status_code==422,response.data
    assert c.get('/api/workbench/v1/modeling/sessions/'+sid+'/history',headers=AUTH).get_json()['latest_number']==1
    assert len(list((s.root/'sessions').iterdir()))==1


def test_damaged_metadata_is_visible_and_mutations_refuse(saved):
    c,s,sid,sha,owner=saved
    s.file(sid,owner,'step').write_bytes(b'changed')
    data=c.get('/api/workbench/v1/modeling/sessions/'+sid,headers=AUTH).get_json()
    assert data['state']=='awaiting_adoption' and data['artifact_integrity']['status']=='unavailable'
    assert revise(c,sid,sha).status_code==409
    assert c.post('/api/workbench/v1/modeling/candidates/'+sid+'/adopt',headers=AUTH,json={'expected_sha256':sha}).status_code==409
    history=c.get('/api/workbench/v1/modeling/sessions/'+sid+'/history',headers=AUTH)
    assert history.status_code==200 and history.get_json()['versions'][0]['artifact_integrity']['status']=='unavailable'


def test_concurrent_same_request_reserves_one_version(saved):
    from concurrent.futures import ThreadPoolExecutor
    c,s,sid,sha,owner=saved
    app=c.application
    def call(_):
        with app.test_client() as client:return revise(client,sid,sha)
    with ThreadPoolExecutor(max_workers=2) as pool:responses=list(pool.map(call,range(2)))
    assert [r.status_code for r in responses]==[201,201]
    assert len({r.get_json()['session_id'] for r in responses})==1
    with s.connect() as db:assert db.execute('SELECT count(*) FROM model_revisions').fetchone()[0]==1


def test_failed_provenance_write_rolls_back_version_and_retry(saved,monkeypatch):
    c,s,sid,sha,owner=saved
    original=s.write_json
    def fail(*args):raise OSError('synthetic fsync failure')
    monkeypatch.setattr(s,'write_json',fail)
    assert revise(c,sid,sha).status_code==500
    assert c.get('/api/workbench/v1/modeling/sessions/'+sid+'/history',headers=AUTH).get_json()['latest_number']==1
    monkeypatch.setattr(s,'write_json',original)
    assert revise(c,sid,sha).get_json()['model_version']['number']==2


def test_interrupted_revision_is_retained_and_next_number_not_reused(saved):
    c,s,sid,sha,owner=saved
    child=revise(c,sid,sha).get_json()['session_id']
    restarted=create_app(s.root.parent,token=TOKEN);service=restarted.extensions['drawform_modeling'];service.stop();service.stopped.clear()
    try:
        client=restarted.test_client();data=client.get('/api/workbench/v1/modeling/sessions/'+child,headers=AUTH).get_json()
        assert data['state']=='interrupted' and data['model_version']['number']==2
        assert revise(client,sid,sha).get_json()['session_id']==child
        new=revise(client,sid,sha,key='after-restart',expected_revision=2)
        assert new.status_code==201 and new.get_json()['model_version']['number']==3
    finally:service.stop();service.jobs.stop()

def test_duplicate_raw_json_fields_are_rejected(saved):
    c,s,sid,sha,owner=saved
    raw=json.dumps(body(sha)).replace('"diameter_mm": 12','"diameter_mm": 12, "diameter_mm": 14')
    assert raw.count('diameter_mm')==2
    res=c.post('/api/workbench/v1/modeling/sessions/'+sid+'/revisions',headers={**AUTH,'Idempotency-Key':'duplicate-field'},data=raw,content_type='application/json')
    assert res.status_code==422,res.data
    with s.connect() as db:assert db.execute('SELECT count(*) FROM model_revisions').fetchone()[0]==0

@pytest.mark.parametrize('filename',['preview.json','validation.json','drawform-validation.json'])
def test_corrupt_parent_companion_blocks_saved_child(saved,filename):
    c,s,sid,sha,owner=saved
    child=revise(c,sid,sha).get_json()['session_id'];out=s.directory(child)/'artifacts';out.mkdir()
    (out/'model.step').write_bytes(b'synthetic-child-step')
    child_sha=hashlib.sha256((out/'model.step').read_bytes()).hexdigest()
    for name in ['preview.json','validation.json','drawform-validation.json']:save_json(out/name,{'step_sha256':child_sha,'passed':True})
    hashes={k:hashlib.sha256((out/v).read_bytes()).hexdigest() for k,v in {'step':'model.step','preview':'preview.json','validation':'validation.json','drawform_validation':'drawform-validation.json'}.items()}
    s.update(child,owner,'awaiting_adoption',step_filename='model.step',step_sha256=child_sha,artifact_hashes=hashes)
    assert s.get(child,owner)['artifact_integrity']['status']=='verified'
    (s.directory(sid)/'artifacts'/filename).write_bytes(b'changed-parent-companion')
    assert s.get(sid,owner)['artifact_integrity']['status']=='unavailable'
    assert s.get(child,owner)['artifact_integrity']['status']=='unavailable'
    assert c.get('/api/workbench/v1/modeling/sessions/'+child+'/files/step',headers=AUTH).status_code==409
    assert c.post('/api/workbench/v1/modeling/candidates/'+child+'/adopt',headers=AUTH,json={'expected_sha256':child_sha}).status_code==409


def test_history_version_number_and_list_share_a_snapshot(saved,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading,time
    c,s,sid,sha,owner=saved;app=c.application;original=s.revisions.latest
    def delayed(root,seat,db):
        if threading.current_thread().name.startswith('history-reader'):time.sleep(.01)
        return original(root,seat,db)
    monkeypatch.setattr(s.revisions,'latest',delayed)
    def read_many():
        with app.test_client() as reader:
            for _ in range(30):
                history=reader.get('/api/workbench/v1/modeling/sessions/'+sid+'/history',headers=AUTH).get_json()
                assert history['latest_number']==max(v['number'] for v in history['versions']),history
    with ThreadPoolExecutor(max_workers=1,thread_name_prefix='history-reader') as pool:
        future=pool.submit(read_many)
        for number in range(1,12):
            response=revise(c,sid,sha,key='parallel-'+str(number),expected_revision=number,changes=[{'op':'set_parameter','parameter':'thickness_mm','value_mm':8+number}])
            assert response.status_code==201,response.data
        future.result()

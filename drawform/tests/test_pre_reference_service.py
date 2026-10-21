"""图前参考等待、闭合提交、重启和审图统计互不冒充。"""
import io
import json
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from drawform.service.jobs import JobQueue, WAITING_REFERENCE
from drawform.geometry.references import ReferenceInputError
from drawform.service import maintenance
from tests.test_live_questions import service_factory, AUTH, TOKEN
from tests.test_pre_reference_geometry import reference_model, selection


def wait(service, job, expected=None):
    deadline=time.monotonic()+30
    while time.monotonic()<deadline:
        status=service.http.get(f'/jobs/{job}',headers=AUTH).get_json()
        if status['state'] not in ('queued','running'):
            if expected is not None:assert status['state']==expected,status
            return status
        time.sleep(.01)
    raise AssertionError('reference worker did not settle')


def upload(service, step):
    response=service.http.post('/jobs',headers=AUTH,
        data={'step':(io.BytesIO(step.read_bytes()),'source.step')},content_type='multipart/form-data')
    assert response.status_code==202,response.data
    return response.get_json()['job_id']


def waiting(service, step):
    job=upload(service,step);status=wait(service,job,WAITING_REFERENCE)
    response=service.http.get(f'/jobs/{job}/reference-questions',headers=AUTH)
    assert response.status_code==200,response.data
    return job,status,response.get_json()['snapshot']


def body(snapshot,unknown=False):
    return {'reviewer':'unit-reference-test',**selection(snapshot,unknown)}


def test_waiting_has_source_preview_but_no_deliverable_and_keeps_old_endpoint_contract(reference_model,service_factory):
    service=service_factory(seeded=False);job,status,snapshot=waiting(service,reference_model[0])
    assert status['files']=={} and snapshot['preview'] and len(snapshot['preview'])==3
    assert service.http.get(f'/jobs/{job}/questions',headers=AUTH).status_code==409
    assert service.http.get(f'/jobs/{job}/files/svg',headers=AUTH).status_code==404
    assert service.http.post(f'/jobs/{job}/answers',headers=AUTH,json={'reviewer':'test','answers':[]}).status_code==409
    assert not list((service.work/job).glob('*.dxf'))
    assert service.http.get(f'/jobs/{job}/reference-questions').status_code==401


def test_waiting_releases_single_geometry_worker_for_another_job(reference_model,bearing_seat_step,service_factory):
    service=service_factory(seeded=False);threads=[];original=service.queue._render
    def observe(*args,**kwargs):threads.append(threading.get_ident());return original(*args,**kwargs)
    service.queue._render=observe
    waiting(service,reference_model[0])
    other=upload(service,bearing_seat_step);wait(service,other,'drawn')
    assert len(threads)==2 and len(set(threads))==1


@pytest.mark.parametrize('bad',['value','coordinate','extra','partial','duplicate','stale'])
def test_http_rejects_numeric_foreign_or_incomplete_answers_without_changing_waiting(reference_model,service_factory,bad):
    service=service_factory(seeded=False);job,_status,snapshot=waiting(service,reference_model[0]);sent=body(snapshot)
    if bad=='value':sent['answers'][0]['value']=12.3
    if bad=='coordinate':sent['answers'][0]['point']=[1,2,3]
    if bad=='extra':sent['vector']=[0,0,1]
    if bad=='partial':sent['answers']=[]
    if bad=='duplicate':sent['answers'].append(dict(sent['answers'][0]))
    if bad=='stale':sent['basis_sha']='a'*64
    response=service.http.post(f'/jobs/{job}/reference-answers',headers=AUTH,json=sent)
    assert response.status_code==(409 if bad=='stale' else 400),response.data
    wait(service,job,WAITING_REFERENCE)
    assert not service.queue.get(job).reference_runs


def test_unknown_is_recorded_as_reference_refusal_and_never_as_human_review(reference_model,service_factory):
    service=service_factory(seeded=False);job,_status,snapshot=waiting(service,reference_model[0])
    sent=body(snapshot,True)
    response=service.http.post(f'/jobs/{job}/reference-answers',headers=AUTH,json=sent)
    assert response.status_code==202
    status=wait(service,job,'refused');assert status['failed']==['reference_unconfirmed']
    assert status['files']=={}
    assert len(service.queue.get(job).reference_runs)==1
    assert service.http.get('/review/stats',headers=AUTH).get_json()['total']==0
    assert not (service.work/'reviews.jsonl').exists()
    assert TOKEN not in (service.work/'jobs.jsonl').read_text()


def test_confirmed_reference_produces_four_files_but_no_review_and_retries_are_idempotent(reference_model,service_factory):
    service=service_factory(seeded=False);job,_status,snapshot=waiting(service,reference_model[0]);sent=body(snapshot)
    response=service.http.post(f'/jobs/{job}/reference-answers',headers=AUTH,json=sent)
    assert response.status_code==202,response.data
    status=wait(service,job,'drawn');assert set(status['files'])=={'dxf','svg','pdf','json'}
    document=service.http.get(status['files']['json'],headers=AUTH).get_json()
    assert document['reference_confirmations']
    before=(service.work/'jobs.jsonl').read_bytes()
    retry=service.http.post(f'/jobs/{job}/reference-answers',headers=AUTH,json=sent)
    assert retry.status_code==202 and retry.get_json()['reference_run']==response.get_json()['reference_run']
    assert (service.work/'jobs.jsonl').read_bytes()==before
    assert len(service.queue.get(job).reference_runs)==1
    assert service.http.get('/review/stats',headers=AUTH).get_json()['total']==0
    assert service.http.get(f'/jobs/{job}/reference-questions',headers=AUTH).status_code==409


def test_concurrent_same_submission_queues_one_render_and_different_submission_conflicts(reference_model,service_factory):
    service=service_factory(seeded=False);job,_status,snapshot=waiting(service,reference_model[0]);submission=selection(snapshot)
    def submit():return service.queue.submit_reference_answers(job,submission,'unit-concurrent')
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(submit) for _ in range(2)];results=[f.result() for f in futures]
    assert results[0]['key']==results[1]['key']
    wait(service,job,'drawn');assert len(service.queue.get(job).reference_runs)==1
    other=selection(snapshot,True)
    with pytest.raises(ReferenceInputError) as error:service.queue.submit_reference_answers(job,other,'unit-concurrent')
    assert error.value.reason=='reference_conflict'


def test_fresh_process_rechecks_waiting_and_restores_reference_candidates(reference_model,service_factory):
    service=service_factory(seeded=False);job,_status,snapshot=waiting(service,reference_model[0]);service.queue.stop()
    script='''
import json,sys,time
from pathlib import Path
from drawform.service.jobs import JobQueue
work=Path(sys.argv[1]);before=len((work/'jobs.jsonl').read_text().splitlines());q=JobQueue(work);q.start()
try:
 deadline=time.monotonic()+30
 while time.monotonic()<deadline:
  j=q.get(sys.argv[2])
  if j.state=='waiting_reference' and len((work/'jobs.jsonl').read_text().splitlines())>=before+2:break
  time.sleep(.01)
 else:raise RuntimeError('restart did not recheck')
 print(json.dumps({'state':j.state,'basis_sha':j.reference_snapshot['basis_sha'],'files':len(j.files),'runs':len(j.reference_runs)}))
finally:q.stop()
'''
    result=subprocess.run([sys.executable,'-c',script,str(service.work),job],capture_output=True,text=True,timeout=40)
    assert result.returncode==0,result.stderr
    data=json.loads(result.stdout);assert data=={'state':WAITING_REFERENCE,'basis_sha':snapshot['basis_sha'],'files':0,'runs':0}


def test_waiting_input_change_is_rejected_on_restart_without_drawing(reference_model,service_factory):
    service=service_factory(seeded=False);job,_status,_snapshot=waiting(service,reference_model[0]);service.queue.stop()
    source=service.queue.get(job).source;source.write_bytes(source.read_bytes()+b'\n')
    q=JobQueue(service.work);q.start()
    try:
        deadline=time.monotonic()+10
        while time.monotonic()<deadline and q.get(job).state in ('waiting_reference','queued','running'):time.sleep(.01)
        assert q.get(job).state=='refused' and q.get(job).failed==('stale_reference',)
        assert not q.get(job).files
    finally:q.stop()


def test_restored_waiting_queue_item_cannot_undo_a_newly_confirmed_drawing(reference_model,service_factory):
    service=service_factory(seeded=False);job,_status,snapshot=waiting(service,reference_model[0]);service.queue.stop()
    q=JobQueue(service.work);go=threading.Event();ready=threading.Event();original_run=q._run;calls=[];render=q._render
    def frozen():ready.set();assert go.wait(10);original_run()
    def observe(*args,**kwargs):calls.append(kwargs);return render(*args,**kwargs)
    q._run=frozen;q._render=observe;q.start();assert ready.wait(5)
    try:
        q.submit_reference_answers(job,selection(snapshot),'unit-race')
        go.set();deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            if q.get(job).state=='drawn' and q._pending.empty():break
            time.sleep(.01)
        assert q.get(job).state=='drawn' and len(q.get(job).files)==4
        assert len(calls)==1 and 'reference_in' in calls[0]
    finally:go.set();q.stop()


def test_live_changed_source_invalidates_get_and_post_without_deliverables(reference_model,service_factory):
    service=service_factory(seeded=False);job,_status,snapshot=waiting(service,reference_model[0])
    source=service.queue.get(job).source;source.write_bytes(source.read_bytes()+b'\n')
    get=service.http.get(f'/jobs/{job}/reference-questions',headers=AUTH)
    post=service.http.post(f'/jobs/{job}/reference-answers',headers=AUTH,json=body(snapshot))
    assert get.status_code==post.status_code==409
    assert get.get_json()['reason']==post.get_json()['reason']=='stale_reference'
    assert not service.queue.get(job).files and not service.queue.get(job).reference_runs


def test_waiting_survives_maintenance_backup_restore_and_over_two_hundred_jobs(reference_model,service_factory,tmp_path):
    service=service_factory(seeded=False);job,_status,snapshot=waiting(service,reference_model[0]);service.queue.stop()
    with (service.work/'jobs.jsonl').open('a') as handle:
        for i in range(201):
            name=f'{i:012x}';folder=service.work/name;folder.mkdir();(folder/'model.step').write_bytes(b'old evidence')
            handle.write(json.dumps({'job':name,'state':'crashed','source':'model.step','files':[]})+'\n')
    assert maintenance.inspect(service.work)['index']['damaged_lines']==0
    backup=tmp_path/'backup';restored=tmp_path/'restored'
    maintenance.backup(service.work,backup);maintenance.restore(backup,restored)
    q=JobQueue(restored)
    assert len(q.listing())==202
    assert q.get(job).state==WAITING_REFERENCE and q.get(job).reference_snapshot['basis_sha']==snapshot['basis_sha']
    assert all((restored/f'{i:012x}'/'model.step').read_bytes()==b'old evidence' for i in range(201))


def test_graphical_answer_rerender_keeps_the_same_reference_snapshot(reference_model,service_factory):
    service=service_factory(seeded=False);job,_status,snapshot=waiting(service,reference_model[0])
    service.http.post(f'/jobs/{job}/reference-answers',headers=AUTH,json=body(snapshot));status=wait(service,job,'drawn')
    initial=service.http.get(status['files']['json'],headers=AUTH).get_json()['reference_confirmations']
    questions=service.http.get(f'/jobs/{job}/questions',headers=AUTH).get_json()['questions'];assert questions
    response=service.http.post(f'/jobs/{job}/answers',headers=AUTH,
        json={'reviewer':'unit-answer','answers':[{'question_id':questions[0]['id'],'option_id':None}]})
    assert response.status_code==202,response.data
    status=wait(service,job,'drawn');latest=service.queue.get(job).answer_runs[-1]
    document=json.loads(dict(latest.files)['json'].read_text())
    assert document['reference_confirmations']==initial
    assert service.http.get('/review/stats',headers=AUTH).get_json()['total']==0

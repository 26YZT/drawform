"""Publication/restart controls using synthetic files, without native CAD calls."""
from dataclasses import asdict, replace
import hashlib
import io
import json
from pathlib import Path
import time
import threading
import zipfile

import ezdxf
import pytest

from drawform.delivery import ALLOW, BLOCK, DeliveryBlocked, Verdict
from drawform.geometry.references import BASIS_VERSION, ReferenceRequired
from drawform.pipeline import Part
from drawform.review import ReviewError
from drawform.service.answers import Answer, AnswerError
from drawform.service.interaction import submit_review
from drawform.service.interaction import ReviewConflict
from drawform.service.journal import Journal, STAGES, validate
from drawform.service.jobs import JobQueue, StaleBase, versions_of, current_files
from drawform.service.questions import Option, Question


QUESTION = Question('q-local','special_tolerance','synthetic','Synthetic','Fixture',
                    'Requires explicit choice',(Option('H7','H7',None),))


def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def contents(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def render(source, directory, *, stem, questions_in=(), reference_in=None, **kwargs):
    directory = Path(directory); directory.mkdir(parents=True,exist_ok=True)
    if source.read_bytes().startswith(b'reference') and reference_in is None:
        raise ReferenceRequired({'version':BASIS_VERSION,'source_sha256':sha(source),
            'basis_sha':'a'*64,'questions':[{'id':'ref-q','options':[{'id':'ref-o'}]}],
            'planes':{},'preview':[]})
    doc=ezdxf.new('R2013');doc.modelspace().add_line((0,0),(20,0))
    doc.saveas(directory/f'{stem}.dxf')
    (directory/f'{stem}.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><text x="10" y="10" font-size="3">20</text></svg>')
    (directory/f'{stem}.pdf').write_bytes(b'%PDF-1.4 synthetic publication control')
    questions=() if questions_in else (QUESTION,)
    (directory/f'{stem}.json').write_text(json.dumps({'questions':[asdict(q) for q in questions],'synthetic':True}))
    return Part(Verdict(ALLOW),tuple((kind,directory/f'{stem}.{kind}') for kind in ('dxf','svg','pdf','json')),questions=questions)


def setup(tmp_path, *, reference=False):
    source=tmp_path/'input.step';source.write_bytes(b'reference fixture' if reference else b'synthetic STEP source')
    work=tmp_path/'work';q=JobQueue(work,render=render,auto_resume=True)
    jid=q.submit(source);q._execute(jid)
    return q,jid,source,work


def wait(q,jid,state='drawn'):
    limit=time.monotonic()+5
    while time.monotonic()<limit:
        try:job=q.get(jid)
        except KeyError:
            time.sleep(.01);continue
        if job.state==state:return job
        time.sleep(.01)
    raise AssertionError(q.get(jid))


def fail(stage):
    def checkpoint(current):
        if current==stage:raise OSError('controlled interruption at '+stage)
    return checkpoint


@pytest.mark.parametrize('stage',STAGES)
def test_initial_publication_recovers_each_stage_in_fresh_queue_without_native_calls(tmp_path,stage):
    source=tmp_path/'source.step';source.write_bytes(b'synthetic STEP source')
    work=tmp_path/'work';q=JobQueue(work,render=render,auto_resume=True);q._checkpoint=fail(stage)
    if stage=='prepared':
        with pytest.raises(OSError):q.submit(source)
        jid=q._journal.export()[0]['job_id']
    else:
        jid=q.submit(source);q._execute(jid)
    prior=contents(work)
    restarted=JobQueue(work,render=render,auto_resume=True)
    restarted.start()
    try:job=wait(restarted,jid)
    finally:restarted.stop()
    assert set(dict(job.files))=={'dxf','svg','pdf','json'}
    assert all((work/name).read_bytes()==value for name,value in prior.items() if name not in ('jobs.jsonl','reviews.jsonl'))
    record=restarted._journal.export([jid])[0]
    assert record['stage']=='complete'
    assert list(dict.fromkeys(e['stage'] for e in record['events']))==list(STAGES)
    assert validate(work)=={'operations':1,'complete':1,'pending':0}
    assert not (work/'reviews.jsonl').exists()


@pytest.mark.parametrize('stage',STAGES)
def test_manual_dxf_recovery_has_one_version_and_replays_only_original_amended_audit(tmp_path,stage):
    q,jid,source,work=setup(tmp_path);job=q.get(jid)
    base=dict(job.files)['dxf'];original=contents(job.directory);q._checkpoint=fail(stage)
    with pytest.raises(OSError):
        q.publish_amended(jid,base.read_bytes(),sha(base),reviewer='Operator',note='Explicit CAD upload')
    restarted=JobQueue(work,render=render,auto_resume=True);restarted.start()
    try:wait(restarted,jid)
    finally:restarted.stop()
    # A drawn parent may precede worker reconciliation; inspect terminal journal.
    if any(r['stage']!='complete' for r in restarted._journal.export([jid])):
        for r in restarted._journal.export([jid]):
            if r['stage']!='complete':restarted._recover_operation(r['operation_id'])
    job=restarted.get(jid)
    assert [p.name for p in versions_of(job)]==['amended-1.dxf']
    assert all((job.directory/name).read_bytes()==value for name,value in original.items())
    rows=restarted._audit.for_job(jid)
    assert len(rows)==1 and rows[0]['decision']=='amended'
    assert rows[0]['reviewer']=='Operator' and rows[0]['note']=='Explicit CAD upload'
    assert 'accepted' not in [r['decision'] for r in rows]
    before=contents(work)
    repeated=restarted.publish_amended(jid,base.read_bytes(),sha(base),reviewer='Operator',note='Explicit CAD upload')
    assert repeated[0].number==1 and contents(work)==before
    validate(work)


@pytest.mark.parametrize('stage',STAGES)
def test_answer_publication_recovers_each_stage_without_overwriting_the_draft(tmp_path,stage):
    q,jid,source,work=setup(tmp_path);job=q.get(jid);original=contents(job.directory)
    q._checkpoint=fail(stage)
    if stage=='prepared':
        with pytest.raises(OSError):q.submit_answers(jid,(Answer(QUESTION.id,None),),'Chooser')
    else:
        q.submit_answers(jid,(Answer(QUESTION.id,None),),'Chooser');q._execute(jid)
    restarted=JobQueue(work,render=render,auto_resume=True);restarted.start()
    try:
        limit=time.monotonic()+5
        while not versions_of(restarted.get(jid)) and time.monotonic()<limit:time.sleep(.01)
    finally:restarted.stop()
    job=restarted.get(jid)
    assert len(versions_of(job))==1 and job.answer_runs[-1].state=='drawn'
    assert all((job.directory/name).read_bytes()==value for name,value in original.items())
    assert (job.directory/'amended-1'/'request.json').is_file()
    rows=restarted._audit.entries_for_job(jid)
    assert sum(r.get('state')=='queued' for r in rows)==1
    assert sum(r.get('state')=='drawn' for r in rows)==1
    assert all('decision' not in r for r in rows)
    assert restarted._audit.stats()['total']==0
    validate(work)


@pytest.mark.parametrize('stage',STAGES)
def test_reference_publication_recovers_explicit_choice_but_never_reviews_it(tmp_path,stage):
    q,jid,source,work=setup(tmp_path,reference=True);job=q.get(jid)
    assert job.state=='waiting_reference'
    q._checkpoint=fail(stage)
    body={'basis_sha':job.reference_snapshot['basis_sha'],'answers':[{'question_id':'ref-q','option_id':'ref-o'}]}
    if stage=='prepared':
        with pytest.raises(OSError):q.submit_reference_answers(jid,body,'Chooser')
    else:
        q.submit_reference_answers(jid,body,'Chooser');q._execute(jid)
    restarted=JobQueue(work,render=render,auto_resume=True);restarted.start()
    try:job=wait(restarted,jid)
    finally:restarted.stop()
    assert job.reference_runs[-1]['submission']=={'basis_sha':'a'*64,'answers':[{'question_id':'ref-q','option_id':'ref-o','free_note':''}]}
    assert restarted._audit.for_job(jid)==[]
    validate(work)


@pytest.mark.parametrize('stage',STAGES)
def test_explicit_review_five_stage_replay_is_exact_and_retry_safe(tmp_path,stage):
    q,jid,source,work=setup(tmp_path);q._checkpoint=fail(stage)
    body={'decision':'rejected','reviewer':'Human','reasons':['missing_dimension'],'submission_id':'stable-review-key-12345'}
    with pytest.raises(OSError):submit_review(q,q._audit,jid,body)
    restarted=JobQueue(work,render=render,auto_resume=True);restarted.start();restarted.stop()
    rows=restarted._audit.for_job(jid)
    assert len(rows)==1 and rows[0]['decision']=='rejected' and rows[0]['reviewer']=='Human'
    before=contents(work)
    assert submit_review(restarted,restarted._audit,jid,body)==rows[0]
    assert contents(work)==before
    validate(work)


def test_unknown_reference_is_terminal_and_never_guessed_on_restart(tmp_path):
    q,jid,source,work=setup(tmp_path,reference=True)
    q.submit_reference_answers(jid,{'basis_sha':'a'*64,'answers':[{'question_id':'ref-q','option_id':None}]},'Unknown')
    restarted=JobQueue(work,render=render,auto_resume=True);restarted.start();restarted.stop()
    assert restarted.get(jid).state=='refused' and not restarted.get(jid).files
    assert all(r['stage']=='complete' for r in restarted._journal.export([jid]))
    assert restarted._audit.for_job(jid)==[]


def test_default_queue_load_keeps_old_interrupted_contract_and_explicit_start_flag_controls_resume(tmp_path):
    source=tmp_path/'input.step';source.write_bytes(b'synthetic STEP source');work=tmp_path/'work'
    q=JobQueue(work,render=render);jid=q.submit(source)
    loaded=JobQueue(work,render=render)
    assert loaded.get(jid).state=='crashed'
    loaded.start();loaded.stop()
    assert loaded.get(jid).state=='crashed' and not loaded.get(jid).files
    resumed=JobQueue(work,render=render,auto_resume=True);resumed.start()
    try:wait(resumed,jid)
    finally:resumed.stop()


def test_unmanaged_old_queued_job_is_not_guessed_or_overwritten(tmp_path):
    work=tmp_path/'work';jobdir=work/'123456abcdef';jobdir.mkdir(parents=True)
    (jobdir/'old.step').write_bytes(b'old source');(jobdir/'orphan.svg').write_bytes(b'old render residue')
    (work/'jobs.jsonl').write_text(json.dumps({'job':jobdir.name,'state':'running','source':'old.step','files':[]})+'\n')
    before=contents(work);q=JobQueue(work,render=render,auto_resume=True);q.start();q.stop()
    assert q.get(jobdir.name).state=='crashed' and contents(work)==before


def test_failed_attempt_is_retained_and_rerender_uses_a_new_attempt(tmp_path):
    source=tmp_path/'input.step';source.write_bytes(b'synthetic STEP source');work=tmp_path/'work'
    attempts=[]
    def interrupted(source,directory,**kwargs):
        attempts.append(Path(directory));(Path(directory)/'partial.svg').write_bytes(b'partial evidence')
        raise OSError('worker killed')
    q=JobQueue(work,render=interrupted,auto_resume=True);jid=q.submit(source);q._execute(jid)
    before=contents(attempts[0]);restarted=JobQueue(work,render=render,auto_resume=True);restarted.start()
    try:wait(restarted,jid)
    finally:restarted.stop()
    assert contents(attempts[0])==before
    assert len(list(attempts[0].parent.iterdir()))==2


@pytest.mark.parametrize('damage',['staged','published','event_gap','source','intent'])
def test_journal_validation_rejects_tamper_without_repair_or_writes(tmp_path,damage):
    q,jid,source,work=setup(tmp_path);op=q._journal.export([jid])[0]
    if damage=='staged':(work/op['data']['artifacts'][0]['staged']).write_bytes(b'changed')
    elif damage=='published':(work/op['data']['artifacts'][0]['path']).write_bytes(b'changed')
    elif damage=='event_gap':(q._journal.root/op['operation_id']/'events'/'000002.json').unlink()
    elif damage=='source':q.get(jid).source.write_bytes(b'changed')
    else:(q._journal.root/op['operation_id']/'intent.json').write_text('{}')
    before=contents(work)
    with pytest.raises(ValueError):validate(work)
    assert contents(work)==before


def test_same_request_admission_before_and_after_restart_is_one_job_and_one_journal(tmp_path):
    source=tmp_path/'input.step';source.write_bytes(b'synthetic STEP source');work=tmp_path/'work'
    q=JobQueue(work,render=render,auto_resume=True)
    jid=q.submit_once(source,request_id='same-input',expected_sha=sha(source));q._execute(jid)
    restarted=JobQueue(work,render=render,auto_resume=True);before=contents(work)
    assert restarted.submit_once(source,request_id='same-input',expected_sha=sha(source))==jid
    assert len(restarted._journal.export())==1 and contents(work)==before
    with pytest.raises(ValueError):restarted.submit_once(source,request_id='same-input',expected_sha=sha(source),sheet='A2')


def test_frozen_answer_cannot_be_promoted_during_capture_or_restart(tmp_path):
    q,jid,source,work=setup(tmp_path)
    q.submit_answers(jid,(Answer(QUESTION.id,None),),'Chooser')
    q._audit.record(job_id=jid,decision='rejected',reviewer='Human',reasons=['wrong_value'])
    q._execute(jid)
    assert q.get(jid).state=='refused' and not versions_of(q.get(jid))
    restarted=JobQueue(work,render=render,auto_resume=True);restarted.start();restarted.stop()
    assert restarted.get(jid).state=='refused' and not versions_of(restarted.get(jid))
    assert restarted._audit.stats()['decisions']=={'rejected':1}


def test_orphan_version_is_preserved_and_cannot_be_reused(tmp_path):
    q,jid,source,work=setup(tmp_path);job=q.get(jid)
    orphan=job.directory/'amended-1.dxf';orphan.write_bytes(b'unregistered human evidence')
    before=contents(work)
    with pytest.raises(StaleBase):q.put_version(jid,b'new version')
    assert contents(work)==before


def test_legacy_package_snapshot_binds_every_byte_and_labels_manual_dxf_only(tmp_path):
    q,jid,source,work=setup(tmp_path);job=q.get(jid);base=dict(job.files)['dxf']
    q.publish_amended(jid,base.read_bytes(),sha(base),reviewer='Human')
    captured=q.package_snapshot(jid);manifest=json.loads(captured[jid+'/manifest.json'])
    assert manifest['effective_edition']==1
    assert manifest['drawing_sets'][1]['mode']=='dxf_only'
    assert manifest['drawing_sets'][0]['mode']=='complete'
    for entry in manifest['files']:
        assert hashlib.sha256(captured[entry['path']]).hexdigest()==entry['sha256']
    before=dict(captured);base.write_bytes(b'changed after capture')
    assert captured==before


def test_empty_legacy_journal_validation_creates_no_directory(tmp_path):
    assert validate(tmp_path)=={'operations':0,'complete':0,'pending':0}
    assert list(tmp_path.iterdir())==[]


def test_pending_review_same_key_cannot_change_the_original_decision(tmp_path):
    q,jid,source,work=setup(tmp_path);q._checkpoint=fail('prepared')
    body={'decision':'rejected','reviewer':'Human','reasons':['missing_dimension'],'submission_id':'stable-review-key-12345'}
    with pytest.raises(OSError):submit_review(q,q._audit,jid,body)
    q._checkpoint=lambda stage:None
    with pytest.raises(ReviewConflict):submit_review(q,q._audit,jid,{**body,'decision':'accepted','reasons':[]})
    assert q._audit.for_job(jid)==[]


def test_pending_accepted_review_is_not_recovered_after_a_numeric_freeze(tmp_path):
    q,jid,source,work=setup(tmp_path);q._checkpoint=fail('prepared')
    with pytest.raises(OSError):submit_review(q,q._audit,jid,{'decision':'accepted','reviewer':'Original Human'})
    q._audit.record(job_id=jid,decision='rejected',reviewer='Later Human',reasons=['wrong_value'])
    restarted=JobQueue(work,render=render,auto_resume=True);restarted.start();restarted.stop()
    assert restarted.get(jid).state=='drawn'
    assert restarted._audit.stats()['decisions']=={'rejected':1}
    assert any(row['status']=='interrupted' for row in restarted._journal.export([jid]))


def test_http_old_bodies_and_optional_idempotency_share_the_publication_gate(tmp_path,monkeypatch):
    from drawform.service import app as module
    original=module.JobQueue;queues=[]
    class ControlledQueue(original):
        def __init__(self,*args,**kwargs):
            super().__init__(*args,render=render,**kwargs);queues.append(self)
    monkeypatch.setattr(module,'JobQueue',ControlledQueue)
    app=module.create_app(tmp_path/'work',token='journal-test-token',auto_resume=True)
    auth={'Authorization':'Bearer journal-test-token','Idempotency-Key':'stable-http-input'}
    try:
        with app.test_client() as http:
            def upload():return http.post('/jobs',headers=auth,data={'step':(io.BytesIO(b'synthetic STEP source'),'part.step')},content_type='multipart/form-data')
            first=upload();assert first.status_code==202
            jid=first.get_json()['job_id'];wait(queues[0],jid)
            assert upload().get_json()['job_id']==jid
            state=http.get('/jobs/'+jid,headers=auth).get_json()
            data=http.get(state['files']['dxf'],headers=auth).data
            amendment=http.post('/jobs/'+jid+'/amended',headers=auth,
                    data={'file':(io.BytesIO(data),'part.dxf'),'reviewer':'Human','draft_sha':hashlib.sha256(data).hexdigest()},
                    content_type='multipart/form-data')
            assert amendment.status_code==201,amendment.data
            package=http.get('/jobs/'+jid+'/package',headers=auth)
            assert package.status_code==200
            with zipfile.ZipFile(io.BytesIO(package.data)) as archive:
                manifest=json.loads(archive.read(jid+'/manifest.json'))
                assert manifest['effective_edition']==1
                assert manifest['drawing_sets'][1]['mode']=='dxf_only'
                for entry in manifest['files']:
                    assert hashlib.sha256(archive.read(entry['path'])).hexdigest()==entry['sha256']
            reviewed=http.post('/jobs/'+jid+'/review',headers=auth,json={'reviewer':'Human','decision':'accepted'})
            assert reviewed.status_code==201,reviewed.data
            assert {op['kind'] for op in queues[0]._journal.export([jid])}=={'submission','amendment','review'}
            assert app.config['DRAWFORM_AUTO_RESUME'] is True
    finally:
        queues[0].stop()
        model=app.extensions.get('drawform_modeling')
        if model:model.stop()


@pytest.mark.parametrize('damaged',[b'{"job":"half-written',b'{broken}\n'])
def test_damaged_audit_blocks_publication_without_appending_or_marking_complete(tmp_path,damaged):
    q,jid,source,work=setup(tmp_path);job=q.get(jid);base=dict(job.files)['dxf']
    audit=work/'reviews.jsonl';audit.write_bytes(damaged)
    original=contents(job.directory)
    with pytest.raises(ReviewError,match='损坏'):
        q.publish_amended(jid,base.read_bytes(),sha(base),reviewer='Human')
    assert audit.read_bytes()==damaged
    assert not versions_of(q.get(jid))
    assert all((job.directory/name).read_bytes()==value for name,value in original.items())
    operation=next(row for row in q._journal.export([jid]) if row['kind']=='amendment')
    assert operation['stage']=='artifacts_committed'
    assert not any(event['stage']=='complete' for event in operation['events'])
    restarted=JobQueue(work,render=render,auto_resume=True);restarted.start();restarted.stop()
    assert audit.read_bytes()==damaged
    assert not versions_of(restarted.get(jid))
    assert restarted._journal.get(operation['operation_id'])['stage']!='complete'


def test_captured_answer_keeps_all_companion_slots_for_second_round_and_restart(tmp_path):
    second=replace(QUESTION,id='q-second')
    def two_questions(source,directory,**kwargs):
        part=render(source,directory,**kwargs)
        answered={a.question_id for a in kwargs.get('questions_in',())}
        remaining=tuple(q for q in (QUESTION,second) if q.id not in answered)
        path=dict(part.files)['json'];document=json.loads(path.read_text())
        document['questions']=[asdict(q) for q in remaining];path.write_text(json.dumps(document))
        return replace(part,questions=remaining)
    source=tmp_path/'input.step';source.write_bytes(b'synthetic STEP source');work=tmp_path/'work'
    q=JobQueue(work,render=two_questions,auto_resume=True);jid=q.submit(source);q._execute(jid)
    q.submit_answers(jid,(Answer(QUESTION.id,None),),'First Chooser');q._execute(jid)
    first=q.get(jid);parent=versions_of(first)[0];original=contents(first.directory)
    assert set(current_files(first))=={'dxf','svg','pdf','json'}
    assert all(path.is_file() for path in current_files(first).values())
    restarted=JobQueue(work,render=two_questions,auto_resume=True)
    assert set(current_files(restarted.get(jid)))=={'dxf','svg','pdf','json'}
    run=restarted.submit_answers(jid,(Answer(second.id,None),),'Second Chooser')
    assert run.state=='queued';restarted._execute(jid)
    completed=restarted.get(jid)
    assert completed.state=='drawn' and len(versions_of(completed))==2
    assert set(current_files(completed))=={'dxf','svg','pdf','json'}
    assert all(path.is_file() for path in current_files(completed).values())
    assert versions_of(completed)[0]==parent
    assert all((completed.directory/name).read_bytes()==value for name,value in original.items())
    validate(work)


def test_waiting_reference_is_published_atomically_before_confirmation_can_queue(tmp_path):
    source=tmp_path/'input.step';source.write_bytes(b'reference fixture');work=tmp_path/'work'
    q=JobQueue(work,render=render,auto_resume=True);jid=q.submit(source)
    captured=threading.Event();release=threading.Event();started=threading.Event();posted=[];errors=[]
    def checkpoint(stage):
        if stage=='captured':
            captured.set();assert release.wait(5)
    q._checkpoint=checkpoint
    worker=threading.Thread(target=q._execute,args=(jid,));worker.start()
    assert captured.wait(5)
    # Read the durable state without acquiring the publication lock held by the
    # corrected implementation; the gate must not expose WAITING prematurely.
    visible=json.loads((work/'jobs.jsonl').read_text().splitlines()[-1])['state']
    body={'basis_sha':'a'*64,'answers':[{'question_id':'ref-q','option_id':'ref-o'}]}
    def confirm():
        started.set()
        try:posted.append(q.submit_reference_answers(jid,body,'Concurrent Chooser'))
        except Exception as error:errors.append(error)
    submission=threading.Thread(target=confirm);submission.start();assert started.wait(5)
    try:
        time.sleep(.05)
    finally:
        release.set();worker.join(5);submission.join(5)
    assert not worker.is_alive() and not submission.is_alive()
    assert visible=='running'
    assert not errors and posted[0]['state']=='queued'
    job=q.get(jid)
    assert job.state=='queued' and len(job.reference_runs)==1
    assert job.reference_runs[0]['submission']['answers'][0]['option_id']=='ref-o'
    q._checkpoint=lambda stage:None;q._execute(jid)
    assert q.get(jid).state=='drawn' and len(q.get(jid).reference_runs)==1
    before=contents(work)
    assert q.submit_reference_answers(jid,body,'Concurrent Chooser')['state']=='drawn'
    assert contents(work)==before
    validate(work)


@pytest.mark.parametrize('current_state',['queued','drawn'])
def test_stale_captured_waiting_target_cannot_overwrite_a_new_reference_request(tmp_path,current_state):
    q,jid,source,work=setup(tmp_path,reference=True);waiting=q.get(jid)
    original=q._journal.export([jid])[0]
    op=q._journal.prepare('submission','stale-waiting-fixture',jid,original['payload'])
    op=q._journal.advance(op,'captured',{'artifacts':[],'job_row':q._row(waiting),'audits':[]})
    q.submit_reference_answers(jid,{'basis_sha':'a'*64,'answers':[{'question_id':'ref-q','option_id':'ref-o'}]},'New Chooser')
    if current_state=='drawn':q._execute(jid)
    before=contents(work)
    with pytest.raises(StaleBase):q._commit(op)
    assert contents(work)==before
    assert q.get(jid).state==current_state and len(q.get(jid).reference_runs)==1
    assert q._journal.get(op['operation_id'])['stage']=='captured'
    preserved=contents(q.get(jid).directory);index=(work/'jobs.jsonl').read_bytes()
    q._recover_operation(op['operation_id'])
    assert q.get(jid).state==current_state and len(q.get(jid).reference_runs)==1
    assert contents(q.get(jid).directory)==preserved and (work/'jobs.jsonl').read_bytes()==index

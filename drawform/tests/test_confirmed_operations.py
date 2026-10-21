"""告警阈值、去重、复查和副本校验；不发送外部消息。"""

from collections import namedtuple
import json

import pytest

from drawform.service import maintenance, operations
from drawform.eval.pilot import create_book, register_case
from tests.test_local_maintenance import work, content, JOB

GiB = 1024**3


@pytest.mark.parametrize('used,free,level', [(79,21,None),(80,20,'warning'),(90,10,'critical'),
                                          (40,9,'warning'),(40,4,'critical'),(40,10,None),(40,5,'warning')])
def test_disk_equality_and_absolute_capacity_are_both_enforced(used, free, level):
    issue = operations.disk_issue(100*GiB,used*GiB,free*GiB)
    assert (issue['level'] if issue else None) == level


@pytest.fixture
def monitored(work, tmp_path, monkeypatch):
    usage = namedtuple('usage','total used free')
    monkeypatch.setattr(maintenance.shutil,'disk_usage',lambda p:usage(100*GiB,50*GiB,50*GiB))
    monkeypatch.setattr(operations,'_probe',lambda url,ca:True)
    return work,tmp_path/'ops/state.json'


def run(monitored, now, **kwargs):
    work,state=monitored
    return operations.check(work,state,'https://127.0.0.1:8443',now=now,**kwargs)


def test_three_health_failures_then_one_recovery_persist_across_checks(monitored,monkeypatch):
    monkeypatch.setattr(operations,'_probe',lambda url,ca:False)
    assert run(monitored,0)['events']==[]
    assert run(monitored,60)['events']==[]
    assert run(monitored,120)['events'][0]['incident']=='service_unavailable'
    assert run(monitored,121)['events']==[]
    assert run(monitored,1920)['events'][0]['event']=='reminder'
    monkeypatch.setattr(operations,'_probe',lambda url,ca:True)
    assert run(monitored,1980)['events'][0]['event']=='recovered'
    assert run(monitored,2040)['events']==[]


def test_maintenance_suppresses_failure_without_fabricating_recovery(monitored,monkeypatch):
    monkeypatch.setattr(operations,'_probe',lambda url,ca:False)
    for n in (0,60,120):run(monitored,n)
    result=run(monitored,180,maintenance_until='1970-01-01T01:00:00+00:00')
    assert result['maintenance'] and not result['events']
    assert 'service_unavailable' in result['active']
    monkeypatch.setattr(operations,'_probe',lambda url,ca:True)
    assert run(monitored,3600)['events'][0]['event']=='recovered'


def test_program_failure_and_process_exit_are_immediate_but_refusal_is_not(monitored,monkeypatch):
    work,state=monitored
    row=json.loads((work/'jobs.jsonl').read_text().splitlines()[-1])
    row['state']='refused'
    with (work/'jobs.jsonl').open('a') as f:f.write(json.dumps(row)+'\n')
    assert run(monitored,0)['events']==[]
    row['state']='crashed'
    with (work/'jobs.jsonl').open('a') as f:f.write(json.dumps(row)+'\n')
    monkeypatch.setattr(operations,'_process_exists',lambda pid:False)
    ids={e['incident'] for e in run(monitored,1,pid=12345)['events']}
    assert ids=={'job_crashed:'+JOB,'process_exited'}


def test_bad_online_audit_is_rechecked_then_recovers_once(monitored):
    work,state=monitored
    path=work/'reviews.jsonl';original=path.read_bytes()
    path.write_bytes(original+b'{broken\n')
    assert run(monitored,0)['integrity_recheck_pending']
    assert run(monitored,299)['events']==[]
    assert run(monitored,300)['events'][0]['incident']=='data_integrity'
    path.write_bytes(original)
    assert run(monitored,600)['events'][0]['event']=='recovered'
    assert run(monitored,900)['events']==[]


def test_active_write_does_not_confirm_integrity_damage(monitored):
    work,state=monitored
    row=json.loads((work/'jobs.jsonl').read_text().splitlines()[-1]);row['state']='running'
    with (work/'jobs.jsonl').open('a') as f:f.write(json.dumps(row)+'\n')
    with (work/'reviews.jsonl').open('a') as f:f.write('{partial\n')
    assert run(monitored,0)['events']==[]
    assert run(monitored,300)['events']==[]


def test_restore_marker_alert_requires_absent_restore_lock(monitored):
    work,state=monitored
    lease=maintenance.lock_work_directory(work)
    (work/'.restore-in-progress').write_text('interrupted fixture')
    try:
        assert run(monitored,0)['events']==[]
    finally:
        lease.close()
    assert run(monitored,1)['events'][0]['incident']=='restore_interrupted'


def test_warning_four_hours_critical_escalation_and_acknowledgement(monitored):
    work,state_path=monitored
    state={};warning={'disk':operations._issue('warning','low')}
    assert operations._notifications(state,warning,0)[0]['event']=='opened'
    assert operations._notifications(state,warning,14399)==[]
    assert operations._notifications(state,warning,14400)[0]['event']=='reminder'
    critical={'disk':operations._issue('critical','lower')}
    assert operations._notifications(state,critical,14401)[0]['event']=='opened'
    state_path.parent.mkdir();state_path.write_text(json.dumps(state))
    operations.acknowledge(state_path,'disk','运维夹具','已人工处置并记录原因')
    saved=json.loads(state_path.read_text())
    assert operations._notifications(saved,critical,18000)[0]['event']=='reminder'
    assert operations._notifications(saved,{},18001)[0]['event']=='recovered'


def test_failed_backup_verification_emits_immediate_local_event_then_recovers(monitored,tmp_path):
    work,state=monitored
    backup=tmp_path/'backup'
    book=tmp_path/'trial.json'
    create_book(book,title='备份演练',source='rehearsal')
    register_case(book,work,JOB,'part')
    operations.storage_action('backup',work,backup,state,book)
    target=backup/'data'/JOB/'drawing.dxf';original=target.read_bytes()
    target.write_bytes(b'broken')
    with pytest.raises(ValueError,match='校验失败'):
        operations.storage_action('verify',backup,None,state)
    events=[json.loads(x) for x in next(state.parent.glob('state.events.*.log')).read_text().splitlines()]
    assert events[-1]['level']=='critical' and events[-1]['event']=='opened'
    target.write_bytes(original)
    operations.storage_action('verify',backup,None,state)
    events=[json.loads(x) for x in next(state.parent.glob('state.events.*.log')).read_text().splitlines()]
    assert events[-1]['event']=='recovered'


def test_recovery_check_preserves_source_and_does_not_write_state_into_backup(monitored,tmp_path):
    work,state=monitored
    original=content(work)
    backup=tmp_path/'backup';restored=tmp_path/'restored'
    book=tmp_path/'trial.json'
    create_book(book,title='备份演练',source='rehearsal')
    register_case(book,work,JOB,'part')
    operations.storage_action('backup',work,backup,state,book)
    operations.storage_action('restore-check',backup,restored,state)
    assert content(work)==original
    restored_files=content(restored)
    assert restored_files.pop(maintenance.PILOT_LEDGER)==book.read_bytes()
    assert restored_files==original
    with pytest.raises(ValueError,match='目录之外'):
        operations.storage_action('verify',backup,None,backup/'state.json')
    assert not (backup/'state.json').exists()


def test_probe_refuses_plaintext_or_url_embedded_credentials(monkeypatch):
    monkeypatch.setenv('DRAWFORM_TOKEN','local-test-only')
    for url in ('http://localhost','https://user:pass@localhost','https://localhost?token=secret'):
        with pytest.raises(ValueError,match='HTTPS'):
            operations._probe(url,None)


def test_events_roll_daily_without_truncating_earlier_evidence(tmp_path):
    state=tmp_path/'state.json'
    first={'at':0,'incident':'test','event':'opened','level':'warning'}
    operations._emit(state,[first])
    old=operations._event_log(state,0)
    before=old.read_bytes()
    operations._emit(state,[{**first,'at':86400,'event':'reminder'}])
    assert old.read_bytes()==before
    assert operations._event_log(state,86400).is_file()
    assert len(list(tmp_path.glob('*.log')))==2

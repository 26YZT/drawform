"""在合成任务副本上核验台账备份及真实 HTTPS 停机/重启告警，不改原业务文件。"""

import argparse
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time

from drawform.eval.pilot import create_book, register_case, report
from drawform.service import maintenance, operations
from drawform.service.retention import evidence_plan
from verify_local import certificate
from verify_maintenance import contents, verify as verify_restored_https


def verify(work, runtime, output):
    work, runtime, output = map(lambda p: Path(p).resolve(), (work, runtime, output))
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    maintenance.inspect(work)
    before = contents(work)
    rows, damaged = maintenance._index_rows((work/'jobs.jsonl').read_bytes())
    assert not damaged
    jobs = {row['job']: row for _line, row in rows if (work/row['job']).is_dir()}
    first = next(row for row in jobs.values() if row['state'] == 'drawn')
    book = output/'rehearsal-book.json'
    create_book(book, title='A方案本机验证，非人工审图', source='rehearsal')
    register_case(book, work, first['job'], 'synthetic-evidence-case')
    state, backup, restored = output/'ops/state.json', output/'backup', output/'restored'
    operations.storage_action('backup',work,backup,state,book)
    operations.storage_action('verify',backup,None,state)
    restore = operations.storage_action('restore-check',backup,restored,state)
    restored_book = Path(restore['restored_book'])
    assert restored_book.read_bytes() == book.read_bytes()
    assert evidence_plan(restored_book,restored)['protected_jobs'] == [first['job']]
    pilot = report(restored_book,restored,rapid_seconds=5)
    assert pilot['source']=='rehearsal' and pilot['baseline']=='not_a_real_parts_baseline'
    (output/'pilot-report.json').write_text(json.dumps(pilot,ensure_ascii=False,indent=2))
    live_restore = verify_restored_https(restored,runtime,output/'https-restore')

    cert,key=certificate(output)
    token=secrets.token_urlsafe(32)
    previous_token=os.environ.get('DRAWFORM_TOKEN')
    os.environ['DRAWFORM_TOKEN']=token
    source=Path(__file__).resolve().parents[1]/'src'
    env={**os.environ,'PYTHONPATH':os.pathsep.join((str(source),str(runtime)))}
    with socket.socket() as probe:
        probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
    url=f'https://127.0.0.1:{port}'
    command=[sys.executable,'-m','drawform.service.production','--work',str(restored),
             '--cert',str(cert),'--key',str(key),'--port',str(port)]
    process=None

    def stop():
        if process is not None and process.poll() is None:
            process.terminate()  # 仅本函数启动的进程。
            try:process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    def start(number):
        with (output/f'monitor-server-{number}.log').open('w') as log:
            child=subprocess.Popen(command,env=env,stdout=log,stderr=subprocess.STDOUT)
        return child

    def ready():
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            if process.poll() is not None:raise RuntimeError('监测演练服务提前退出')
            if operations._probe(url,cert):return
            time.sleep(.1)
        raise TimeoutError('监测演练服务未启动')

    try:
        process=start(1);ready()
        stamp=time.time()
        normal=operations.check(restored,state,url,cert,process.pid,now=stamp)
        assert 'service_unavailable' not in normal['active']
        assert 'process_exited' not in normal['active']
        stop()
        checks=[operations.check(restored,state,url,cert,process.pid,now=stamp+n*60) for n in (1,2,3)]
        assert 'process_exited' in checks[0]['active']
        assert 'service_unavailable' not in checks[1]['active']
        assert 'service_unavailable' in checks[2]['active']
        process=start(2);ready()
        recovered=operations.check(restored,state,url,cert,process.pid,now=stamp+240)
        recoveries={e['incident'] for e in recovered['events'] if e['event']=='recovered'}
        assert {'process_exited','service_unavailable'} <= recoveries
        steady=operations.check(restored,state,url,cert,process.pid,now=stamp+241)
        assert not any(e['event']=='recovered' for e in steady['events'])
    finally:
        stop()
        if previous_token is None:os.environ.pop('DRAWFORM_TOKEN',None)
        else:os.environ['DRAWFORM_TOKEN']=previous_token
    assert contents(work)==before
    result={'scope':'local_synthetic_policy_a_rehearsal','original_business_files_unchanged':True,
            'external_ledger_restored_byte_for_byte':True,'ledger_step_fingerprint_verified':True,
            'restored_https':live_restore,'process_exit_detected':True,
            'third_https_failure_alerted':True,'recovery_once':True,'owned_servers_stopped':True,
            'timing_qualification':'真实HTTPS启停；采样时刻由验证脚本推进，不是实测三分钟停机SLA',
            'human_review_or_company_acceptance':False}
    (output/'verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work',required=True,type=Path)
    parser.add_argument('--runtime',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    print(json.dumps(verify(args.work,args.runtime,args.output),ensure_ascii=False,indent=2))

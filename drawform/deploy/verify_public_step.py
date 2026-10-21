"""许可已筛选的公开 STEP 经本机 HTTPS 验证；accepted 只模拟，不代表人工审核。"""

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import secrets
import signal
import socket
import ssl
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPSHandler, ProxyHandler, Request, build_opener
import zipfile

from verify_local import certificate, multipart

SIM_REVIEWER = "公开STEP技术验证：模拟审核，无人工签字"
SIM_NOTE = "simulated_acceptance=true; human_review=false; production_release=false；仅测试状态与导出流程"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def _inside(base, name):
    if not isinstance(name, str) or not name or Path(name).is_absolute() or '..' in Path(name).parts:
        raise ValueError('清单路径须位于样本目录内')
    path = base / name
    if path.is_symlink() or not path.resolve().is_relative_to(base) or not path.is_file():
        raise ValueError('清单文件缺失或越界')
    return path


def read_catalog(path):
    path = Path(path).resolve()
    catalog = json.loads(path.read_text())
    if (catalog.get('scope') != 'public_step_technical_validation' or catalog.get('human_review') is not False
            or catalog.get('production_release') is not False or catalog.get('selection_locked_before_render') is not True):
        raise ValueError('必须显式标记公开技术验证、无人工审核、未生产放行，并在出图前锁定选样')
    license_record = catalog.get('license_evidence', {})
    evidence = _inside(path.parent, license_record.get('file'))
    if sha(evidence.read_bytes()) != license_record.get('sha256'):
        raise ValueError('许可证据指纹不符')
    models = catalog.get('models')
    if not isinstance(models, list) or not models:
        raise ValueError('模型清单为空')
    identities, hashes = set(), set()
    for model in models:
        if (model.get('license') != 'CC-BY-3.0' or model.get('license_screening') != 'included'
                or not model.get('author_attribution') or not model.get('source_url', '').startswith('https://')
                or not re.fullmatch(r'[0-9a-f]{40}', model.get('commit', ''))):
            raise ValueError('模型缺少已筛选的许可、署名或固定来源')
        data = _inside(path.parent, model.get('file')).read_bytes()
        digest = sha(data)
        if digest != model.get('sha256') or len(data) != model.get('bytes'):
            raise ValueError('公开 STEP 指纹或大小不符')
        if model.get('id') != 'public-' + digest[:12] or model['id'] in identities or digest in hashes:
            raise ValueError('样本标识不匹配或重复，不能抬高样本数')
        identities.add(model['id']); hashes.add(digest)
    return catalog


def answer_payload(questions):
    answers = []
    selected_fit = False
    for question in questions:
        options = question['options']
        ids = [option['id'] for option in options]
        if not options or len(ids) != len(set(ids)):
            raise ValueError('引擎候选为空或有重复 ID')
        chosen = None
        if not selected_fit and question['kind'] == 'special_tolerance' and 'H7' in ids:
            chosen = 'H7'; selected_fit = True
        answers.append({'question_id':question['id'], 'option_id':chosen,
                        'free_note':'公开模型自动演练选择，不是工程师生产确认'})
    return {'reviewer':SIM_REVIEWER,'answers':answers}


def summary(rows, requested):
    counts = {name:sum(r.get('outcome') == name for r in rows) for name in
              ('simulated_accepted','engine_refused','engine_crashed','boundary_drawn_not_accepted','error')}
    return {'scope':'public_step_technical_validation','requested':requested,'processed':len(rows),**counts,
            'true_human_approved':0,'production_release':False,'real_parts_first_pass_rate':None,
            'qualification':'公开许可CAD模型；无独立人工制图审核或金标准。模拟accepted仅验证状态流程，不是实际采纳率。'}


def verify(catalog_path, runtime, output):
    catalog_path, runtime, output = map(lambda p:Path(p).resolve(), (catalog_path,runtime,output))
    catalog = read_catalog(catalog_path)
    catalog_sha = sha(catalog_path.read_bytes())
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    source = Path(__file__).resolve().parents[1]/'src'
    source_hashes = {str(p.relative_to(source)):sha(p.read_bytes()) for p in source.rglob('*.py')}
    (output/'source-hashes.json').write_text(json.dumps(source_hashes,indent=2))
    cert,key=certificate(output)
    token=secrets.token_urlsafe(32)
    env={**os.environ,'DRAWFORM_TOKEN':token,'PYTHONPATH':os.pathsep.join((str(source),str(runtime))),
         'PYTHONFAULTHANDLER':'1'}
    with socket.socket() as probe:
        probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
    url=f'https://127.0.0.1:{port}'
    work=output/'jobs'
    command=[sys.executable,'-m','drawform.service.production','--work',str(work),
             '--cert',str(cert),'--key',str(key),'--port',str(port)]
    context=ssl.create_default_context(cafile=str(cert))
    client=build_opener(ProxyHandler({}),HTTPSHandler(context=context))
    rows=[];process=None;workers=[];protocol={}

    def call(route,data=None,headers=None,status=200,raw=False,authorized=True):
        sent={'Authorization':'Bearer '+token} if authorized else {}
        sent.update(headers or {})
        if isinstance(data,dict):data=json.dumps(data).encode();sent['Content-Type']='application/json'
        request=Request(url+route,data=data,headers=sent)
        try:response=client.open(request,timeout=30)
        except HTTPError as error:response=error
        with response:
            body=response.read()
            if response.status != status:raise AssertionError(f'{route}: HTTP {response.status}, expected {status}; {body[:500]!r}')
        return body if raw else json.loads(body)

    def wait(job):
        deadline=time.monotonic()+90
        while time.monotonic()<deadline:
            row=call('/jobs/'+job)
            if row['state'] in ('drawn','refused','crashed'):return row
            time.sleep(.2)
        raise TimeoutError('90秒内未进入终态，停止继续提交，不丢弃该失败记录')

    def stop():
        if process is not None and process.poll() is None:
            # 仅本脚本用 start_new_session 创建的进程组，含自己的应用 worker。
            os.killpg(process.pid,signal.SIGTERM)
            try:process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid,signal.SIGKILL);process.wait(timeout=5)

    def start(number):
        with (output/f'server-{number}.log').open('w') as log:
            child=subprocess.Popen(command,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        return child

    def ready():
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            if process.poll() is not None:raise RuntimeError('WSGI退出，见服务器日志')
            try:
                call('/jobs')
                children=subprocess.check_output(['pgrep','-P',str(process.pid)],text=True).split()
                assert len(children)==1,'不允许多个应用worker'
                workers.append(children[0]);return
            except (URLError,TimeoutError,ConnectionError):time.sleep(.2)
        raise TimeoutError('HTTPS服务未就绪')

    try:
        process=start(1);ready()
        call('/jobs',status=401,authorized=False,raw=True)
        with socket.create_connection(('127.0.0.1',port),timeout=5) as connection:
            with context.wrap_socket(connection,server_hostname='127.0.0.1') as secure:protocol['tls_version']=secure.version()
        untrusted=build_opener(ProxyHandler({}),HTTPSHandler(context=ssl.create_default_context()))
        try:untrusted.open(url+'/',timeout=5)
        except URLError as error:
            assert isinstance(error.reason,ssl.SSLCertVerificationError)
            protocol['untrusted_certificate_rejected']=True
        else:raise AssertionError('未显式信任的自签证书不应通过校验')
        for model in catalog['models']:
            row={'id':model['id'],'source_url':model['source_url'],'license':model['license'],
                 'step_sha256':model['sha256'],'geometry':model['geometry'],'human_review':False}
            tick=time.monotonic();halt=False
            folder=output/'cases'/model['id'];folder.mkdir(parents=True)
            try:
                path=catalog_path.parent/model['file']
                data,headers=multipart('step',path)
                job=row['job_id']=call('/jobs',data,headers,status=202)['job_id']
                initial=wait(job)
                row.update(initial_state=initial['state'],gate_failed=initial['failed'],gate_reasons=initial['reasons'])
                if initial['state'] != 'drawn':
                    call(f'/jobs/{job}/review',{'reviewer':SIM_REVIEWER,'decision':'accepted','note':SIM_NOTE},status=409)
                    row.update(outcome='engine_refused' if initial['state']=='refused' else 'engine_crashed',
                               engine_error=initial['error'],simulated_review_rejected=True)
                else:
                    original={kind:call(route,raw=True) for kind,route in initial['files'].items()}
                    for kind,body in original.items():(folder/f'original.{kind}').write_bytes(body)
                    row['original_hashes']={kind:sha(body) for kind,body in original.items()}
                    questions=call(f'/jobs/{job}/questions')
                    assert type(questions['truncated']) is bool
                    cards=questions['questions'];answer_body=answer_payload(cards)
                    row.update(question_count=len(cards),truncated=questions['truncated'])
                    # 第二个独立任务验证同一STEP的候选ID，不能只重读同一JSON假装稳定。
                    repeat=call('/jobs',data,headers,status=202)['job_id']
                    assert wait(repeat)['state']=='drawn'
                    repeated=call(f'/jobs/{repeat}/questions')
                    assert repeated['questions']==cards and repeated['truncated']==questions['truncated']
                    row['candidate_repeat_job']=repeat;row['stable_candidates']=True
                    if not model['geometry']['valid'] or model['geometry']['solids'] != 1:
                        row.update(outcome='boundary_drawn_not_accepted',scope_risk='引擎出图，但样件非有效单实体；不模拟生产件采用')
                    else:
                        if cards:
                            bad={'reviewer':SIM_REVIEWER,'answers':[{'question_id':cards[0]['id'],'option_id':'NOT_AN_ENGINE_OPTION','value':123.456}]}
                            call(f'/jobs/{job}/answers',bad,status=400)
                            call(f'/jobs/{job}/answers',answer_body,status=202)
                            current=wait(job)
                            if current['state']!='drawn':raise AssertionError(f'答案重出图未成功：{current}')
                            version=current['version']
                            retry=call(f'/jobs/{job}/answers',answer_body,status=202)
                            assert retry['version']==version
                            assert call(f'/jobs/{job}/questions')['questions']==[]
                        else:
                            current=initial;version=0
                        current_files={kind:call(route,raw=True) for kind,route in current['files'].items() if kind in ('dxf','svg','pdf','json')}
                        before=json.loads(original['json']);after=json.loads(current_files['json'])
                        assert [c['value'] for c in before['constraints']]==[c['value'] for c in after['constraints']]
                        assert current_files['pdf'].startswith(b'%PDF')
                        assert all(call(initial['files'][kind],raw=True)==body for kind,body in original.items())
                        for kind,body in current_files.items():(folder/f'current.{kind}').write_bytes(body)
                        accepted=call(f'/jobs/{job}/review',{'reviewer':SIM_REVIEWER,'decision':'accepted','note':SIM_NOTE},status=201)['review']
                        assert accepted['reviewer']==SIM_REVIEWER and accepted['note']==SIM_NOTE
                        (folder/'simulated-review.json').write_text(json.dumps(accepted,ensure_ascii=False,indent=2))
                        if version:
                            package=call(f'/jobs/{job}/package',raw=True)
                            with zipfile.ZipFile(io.BytesIO(package)) as archive:
                                assert archive.testzip() is None
                                audit=[json.loads(line) for line in archive.read(job+'/review.jsonl').decode().splitlines()]
                                assert audit[-1]['note']==SIM_NOTE
                            (folder/'technical-delivery.zip').write_bytes(package)
                        else:
                            call(f'/jobs/{job}/package',status=409,raw=True)
                        row.update(outcome='simulated_accepted',version=version,answers=answer_body['answers'],
                                   original_unchanged=True,current_hashes={kind:sha(body) for kind,body in current_files.items()},
                                   package_exported=bool(version),dimension_values_unchanged=True)
            except Exception as error:
                row.update(outcome='error',error=f'{type(error).__name__}: {error}')
                halt=isinstance(error,(TimeoutError,URLError,ConnectionError))
            row['seconds']=time.monotonic()-tick
            rows.append(row)
            with (output/'cases.jsonl').open('a',encoding='utf-8') as journal:
                journal.write(json.dumps(row,ensure_ascii=False)+'\n');journal.flush();os.fsync(journal.fileno())
            print(f"{len(rows)}/{len(catalog['models'])} {row['id']} {row['outcome']}",flush=True)
            if halt:break
        stop();process=start(2);ready()
        for row in rows:
            if row['outcome']!='simulated_accepted':continue
            job=row['job_id'];restored=call('/jobs/'+job)
            assert restored['state']=='drawn' and restored.get('version',0)==row['version']
            for kind,digest in row['current_hashes'].items():assert sha(call(restored['files'][kind],raw=True))==digest
            for kind,digest in row['original_hashes'].items():assert sha(call(f'/jobs/{job}/files/{kind}',raw=True))==digest
            audits=call(f'/jobs/{job}/review')['reviews']
            assert audits[-1]['note']==SIM_NOTE and audits[-1]['reviewer']==SIM_REVIEWER
            row['restart_preserved']=True
    finally:
        stop()
    result=summary(rows,len(catalog['models']))
    result.update(protocol=protocol,worker_pids=workers,owned_server_stopped=True,
                  input_unchanged=all(sha((catalog_path.parent/m['file']).read_bytes())==m['sha256'] for m in catalog['models']),
                  catalog_unchanged=sha(catalog_path.read_bytes())==catalog_sha,
                  source_unchanged=all(sha((source/p).read_bytes())==digest for p,digest in source_hashes.items()),
                  restart_verified=sum(r.get('restart_preserved',False) for r in rows),cases=rows)
    (output/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog',required=True,type=Path)
    parser.add_argument('--runtime',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    result=verify(args.catalog,args.runtime,args.output)
    print(json.dumps({k:v for k,v in result.items() if k!='cases'},ensure_ascii=False,indent=2))
    return int(result['error']>0 or result['engine_crashed']>0 or result['processed']!=result['requested']
               or not all(result[k] for k in ('input_unchanged','catalog_unchanged','source_unchanged')))


if __name__=='__main__':
    raise SystemExit(main())

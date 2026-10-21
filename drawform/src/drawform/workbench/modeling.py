"""本地板件建模控制面：SQLite短事务、不可变产物、明确采用、唯一出图队列。"""
from __future__ import annotations
import atexit
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import queue as queues
import signal
import sqlite3
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timezone
from .plate_spec import parse_plate, SpecError

ROOT = Path(__file__).resolve().parents[4]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save_json(path, data):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix+'.tmp')
    with tmp.open('w', encoding='utf-8') as f:
        json.dump(data,f,ensure_ascii=False,indent=2); f.flush(); os.fsync(f.fileno())
    os.replace(tmp,path)
    fd=os.open(path.parent,os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)


class Conflict(ValueError): pass


class ModelingService:
    def __init__(self, work, job_queue, *, auto_resume=False):
        self.auto_resume=auto_resume
        self.root = Path(work)/'workbench'; self.root.mkdir(parents=True,exist_ok=True)
        self.db = self.root/'workspace.sqlite'; self.jobs=job_queue
        self.python = Path(os.environ.get('DRAWFORM_MODEL_PYTHON',str(ROOT/'runtimes/cadgen_bridge/.venv/bin/python')))
        self.runner=ROOT/'runtimes/cadgen_bridge/plate.py'
        self.source_root=ROOT/'drawform/src';self.write_json=save_json
        self.lock=threading.RLock(); self.pending=queues.Queue(); self.stopped=threading.Event(); self.active=set()
        with self.connect() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS inputs(id TEXT PRIMARY KEY,owner TEXT NOT NULL,text TEXT NOT NULL,sha TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,owner TEXT NOT NULL,request_key TEXT NOT NULL,payload_sha TEXT NOT NULL,state TEXT NOT NULL,data TEXT NOT NULL,UNIQUE(owner,request_key));''')
            rows=db.execute("SELECT id,state,data FROM sessions WHERE state IN ('queued','building','analyzing','cancel_requested')").fetchall()
            for row in rows:
                data=json.loads(row['data']); data['error']='服务重启中断了生成，正在核对恢复依据。'
                data['interrupted_from_state']=row['state']
                if row['state']=='cancel_requested':data['interrupted_reason']='cancelled'
                db.execute("UPDATE sessions SET state='interrupted',data=? WHERE id=?",(json.dumps(data,ensure_ascii=False),row['id']))
            db.commit()
        from .drawing_workflow import DrawingWorkflow
        self.drawings=DrawingWorkflow(self)
        from .revisions import ModelRevisions
        self.revisions=ModelRevisions(self)
        from .clarifications import Clarifications
        self.clarifications=Clarifications(self)
        from .projects import Projects
        self.projects=Projects(self)
        from .attempts import ModelAttempts
        self.attempts=ModelAttempts(self)
        if self.auto_resume:self.attempts.recover()
        self.worker=threading.Thread(target=self.run,name='modeling-control',daemon=True); self.worker.start(); atexit.register(self.stop)

    def connect(self):
        db=sqlite3.connect(self.db,timeout=15); db.row_factory=sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON'); db.execute('PRAGMA synchronous=FULL')
        return closing(db)

    def capabilities(self):
        return {'available':self.python.is_file() and self.runner.is_file(),'mode':'local_simple_v2','scope':'明确文字及有限图纸的板件/圆盘/垫圈/二段实心阶梯轴','units':'mm','external_model_calls':False,'drawing_formats':['dxf','pdf','png','jpeg'],'simple_parts':['plate','washer','stepped_shaft'],'automatic_resume':self.auto_resume,'maximum_automatic_recoveries':3}

    def add_input(self,text,units,owner):
        if not isinstance(text,str) or not text.strip() or len(text)>2000 or units!='mm':
            raise SpecError('请填写最多2000字的描述，尺寸单位选择毫米。')
        identity=uuid.uuid4().hex; sha=hashlib.sha256(text.encode()).hexdigest()
        with self.connect() as db:
            db.execute('INSERT INTO inputs VALUES(?,?,?,?)',(identity,owner,text,sha)); db.commit()
        save_json(self.root/'inputs'/f'{identity}.json',{'schema_version':1,'text':text,'units':units,'text_sha256':sha})
        return {'bundle_id':identity,'text':text,'text_sha256':sha,'units':units}

    def create(self,bundle,sheet,key,owner):
        if sheet not in ('A3','A2') or not isinstance(key,str) or not 1<=len(key)<=128:
            raise SpecError('请选择A3/A2，并提供有效的Idempotency-Key。')
        if not isinstance(bundle,str) or len(bundle)!=32 or any(c not in '0123456789abcdef' for c in bundle):raise SpecError('无效的输入引用。')
        with self.lock,self.connect() as db:
            inp=db.execute('SELECT * FROM inputs WHERE id=? AND owner=?',(bundle,owner)).fetchone()
            if inp is None: raise KeyError(bundle)
            drawing=self.drawings.lookup(bundle,owner)
            extended=None;analysis=None
            if drawing:params={}
            else:
                try:params=parse_plate(inp['text'])
                except SpecError as original:
                    from .simple_spec import parse_extended_text
                    try:extended=parse_extended_text(inp['text']);params=extended['parameters']
                    except SpecError:
                        from .text_intake import analyze_text
                        analysis=analyze_text(inp['text'])
                        params=analysis['parameters']
                        if not analysis['questions']:
                            from .simple_spec import spec_from_analysis
                            extended=spec_from_analysis(analysis);params=extended['parameters']
            payload=hashlib.sha256(json.dumps({'bundle_id':bundle,'sheet':sheet,'params':params},sort_keys=True).encode()).hexdigest()
            old=db.execute('SELECT id,payload_sha FROM sessions WHERE owner=? AND request_key=?',(owner,key)).fetchone()
            if old:
                if old['payload_sha']!=payload: raise Conflict('同一请求编号对应了不同输入，请新建请求。')
                return self.get(old['id'],owner)
            if not self.capabilities()['available']: raise SpecError('独立建模环境尚未就绪。','RUNTIME_UNAVAILABLE')
            sid=uuid.uuid4().hex
            data={'session_id':sid,'bundle_id':bundle,'text_sha256':inp['sha'],'text':inp['text'],'parameters':params,'sheet':sheet,'candidate_id':sid,'created_at':datetime.now(timezone.utc).isoformat(),'job_id':None,'error':'','backend':'local_plate_v1','human_review':'not_reviewed'}
            data.update(input_kind='drawing' if drawing else 'text',filename=drawing['filename'] if drawing else '',source_sha256=inp['sha'])
            if extended:data.update(spec=extended,model_kind=extended['kind'],holes=extended['holes'],backend='local_simple_v2')
            state='analyzing' if drawing else 'queued'
            if analysis is not None:
                from .clarifications import basis
                data.update(analysis=analysis,questions=analysis['questions'],basis_sha256=basis(analysis,1),
                            clarification_revision=1,clarifications=[],model_kind=analysis['kind'],
                            holes=analysis['holes'],backend='local_simple_v2')
                self.verify_input_source(data,owner)
                save_json(self.directory(sid)/'analysis/analysis.json',analysis)
                if analysis['questions']:state='needs_clarification'
            if self.auto_resume and state in ('queued','analyzing'):
                from .attempts import bind_intent
                bind_intent(data,'analysis' if state=='analyzing' else 'build')
            db.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?)',(sid,owner,key,payload,state,json.dumps(data,ensure_ascii=False))); db.commit()
        if state in ('queued','analyzing'):self.pending.put((sid,owner))
        return self.get(sid,owner)

    def raw(self,sid,owner):
        self.directory(sid)
        with self.connect() as db: row=db.execute('SELECT state,data FROM sessions WHERE id=? AND owner=?',(sid,owner)).fetchone()
        if row is None: raise KeyError(sid)
        data=json.loads(row['data']);data['state']=row['state']
        return data

    def get(self,sid,owner):
        data=self.raw(sid,owner)
        if data.get('step_sha256'):
            data['files']={k:f'/api/workbench/v1/modeling/sessions/{sid}/files/{k}' for k in ('step','preview','validation','drawform_validation')}
            try:
                self.check_artifacts(data,owner)
                data['artifact_integrity']={'status':'verified'}
            except (Conflict,SpecError,KeyError,OSError,ValueError) as error:
                data['artifact_integrity']={'status':'unavailable','error':str(error)}
        result=self.revisions.decorate(data,owner)
        result['project']=self.projects.get(result['model_version']['root_session_id'],owner)
        if data.get('execution_intent_sha256'):
            rows=self.attempts.history(sid,owner)
            result['execution']={'automatic_resume':self.auto_resume,'attempts':[{k:r.get(k) for k in ('attempt_id','kind','state','number','reason')} for r in rows],
                                 'current_attempt_id':data.get('current_attempt_id'),'recovery_blocked':data.get('recovery_blocked',False)}
        return result

    def update(self,sid,owner,state,**values):
        with self.lock,self.connect() as db:
            row=db.execute('SELECT data FROM sessions WHERE id=? AND owner=?',(sid,owner)).fetchone()
            if row is None: raise KeyError(sid)
            data=json.loads(row['data']);data.update(values)
            if self.auto_resume and state in ('queued','analyzing'):
                from .attempts import bind_intent
                bind_intent(data,'analysis' if state=='analyzing' else 'build')
            db.execute('UPDATE sessions SET state=?,data=? WHERE id=?',(state,json.dumps(data,ensure_ascii=False),sid));db.commit()

    def directory(self,sid):
        if not isinstance(sid,str) or len(sid)!=32 or any(c not in '0123456789abcdef' for c in sid): raise KeyError(sid)
        return self.root/'sessions'/sid

    def file(self,sid,owner,kind):
        data=self.raw(sid,owner)
        names={'step':data.get('step_filename','plate.step'),'preview':'preview.json','validation':'validation.json','drawform_validation':'drawform-validation.json'}
        if kind not in names or data['state'] not in ('awaiting_adoption','adopted'): raise KeyError(kind)
        self.check_artifacts(data,owner)
        from .paths import model_relative
        return self.directory(sid)/model_relative(data)/names[kind]

    def verify_input_source(self,data,owner):
        if data.get('input_kind')=='drawing':
            self.drawings.verify_source(data,owner)
            return
        path=self.root/'inputs'/(data['bundle_id']+'.json')
        if path.is_symlink() or not path.is_file():raise Conflict('原始文字记录缺失或已改变。')
        try:stored=json.loads(path.read_text())
        except (OSError,ValueError):raise Conflict('原始文字记录无法读取。')
        sha=hashlib.sha256(data['text'].encode()).hexdigest()
        if stored.get('text')!=data['text'] or stored.get('text_sha256')!=sha or data['text_sha256']!=sha:
            raise Conflict('原始文字与模型来源不一致。')

    def check_artifacts(self,data,owner):
        self.check_local_artifacts(data,owner)
        self.revisions.verify_lineage(data,owner)

    def check_local_artifacts(self,data,owner):
        from .revisions import normalized_spec
        try:normalized_spec(data)
        except (SpecError,Conflict):raise Conflict('模型规格与保存参数不一致。')
        try:self.verify_input_source(data,owner)
        except OSError:raise Conflict('模型原始输入无法读取。')
        if data.get('artifact_hashes',{}).get('step')!=data.get('step_sha256'):raise Conflict('模型STEP摘要记录不一致。')
        names={'step':data.get('step_filename','plate.step'),'preview':'preview.json','validation':'validation.json','drawform_validation':'drawform-validation.json'}
        for kind,name in names.items():
            from .paths import model_relative
            path=self.directory(data['session_id'])/model_relative(data)/name
            expected=data.get('artifact_hashes',{}).get(kind)
            try:valid=not path.is_symlink() and path.is_file() and bool(expected) and digest(path)==expected
            except OSError:valid=False
            if not valid:
                raise Conflict('保存产物缺失或已改变，不能下载、修改或继续出图。')

    def subprocess(self,argv,cwd,log,*,source_path=None,timeout=180):
        env={k:v for k,v in os.environ.items() if k in ('PATH','TMPDIR','LANG','LC_ALL','SYSTEMROOT')}
        env.update(CADGEN_DAEMON='0',CADGEN_UPDATE_CHECK='0',CADGEN_TELEMETRY='0',DO_NOT_TRACK='1',CADGEN_CACHE_DIR=str(cwd/'cache'))
        if source_path:env['PYTHONPATH']=str(source_path)
        with log.open('wb') as out:
            with self.lock:
                if self.stopped.is_set():raise RuntimeError('服务已停止。')
                command=([sys.executable,str(Path(__file__).with_name('child_guard.py')),str(os.getpid()),*argv] if self.auto_resume else argv)
                p=subprocess.Popen(command,cwd=cwd,env=env,stdout=out,stderr=subprocess.STDOUT,start_new_session=True)
                self.active.add(p)
                if self.auto_resume:
                    from .attempts import process_identity
                    save_json(cwd/'process.json',{'pid':p.pid,'identity':process_identity(p.pid),'cwd':str(cwd.resolve())})
            try: code=p.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                os.killpg(p.pid,signal.SIGTERM)
                try:p.wait(timeout=5)
                except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);p.wait()
                raise RuntimeError('建模或检查超时，旧模型与图纸未改变。')
            finally:
                with self.lock:self.active.discard(p)
        if code:raise RuntimeError(f'模型生成或保存文件检查失败（退出码{code}），请查看本地任务日志。')

    def run(self):
        while True:
            item=self.pending.get()
            if item is None:return
            sid,owner=item
            identity=None
            try:
                data=self.get(sid,owner)
                if self.stopped.is_set():continue
                if data['state']=='analyzing':
                    if self.auto_resume:
                        identity,directory=self.attempts.begin(sid,owner,'analysis')
                        self.drawings.analyze(sid,owner,data,attempt_id=identity,attempt_directory=directory)
                    else:self.drawings.analyze(sid,owner,data)
                    continue
                if data['state']!='queued':continue
                if self.auto_resume:identity,directory=self.attempts.begin(sid,owner,'build')
                else:
                    self.update(sid,owner,'building')
                    directory=self.directory(sid);directory.mkdir(parents=True,exist_ok=True)
                output=(directory/'artifacts').resolve()
                generic=bool(data.get('spec'))
                self.verify_input_source(data,owner)
                self.revisions.verify_lineage(data,owner)
                request={'schema_version':2,'spec':data['spec'],'output_dir':str(output)} if generic else {'schema_version':1,**data['parameters'],'output_dir':str(output)}
                runner=ROOT/'runtimes/cadgen_bridge/simple.py' if generic else self.runner
                save_json(directory/'request.json',request)
                self.subprocess([str(self.python),str(runner),str(directory/'request.json')],directory,directory/'model.log')
                result=json.loads((output/'result.json').read_text())
                if result.get('status')!='succeeded':raise RuntimeError('建模进程未提供成功产物。')
                step_filename='model.step' if generic else 'plate.step'
                step=output/step_filename
                if generic and result.get('spec')!=data['spec']:raise RuntimeError('返回的模型规格与当前图纸依据不一致。')
                sha=digest(step)
                if sha!=result.get('step_sha256'):raise RuntimeError('STEP摘要与建模回执不一致。')
                inspection={'step':str(step),'output':str(output/'drawform-validation.json')}
                inspection['spec' if generic else 'parameters']=data['spec'] if generic else data['parameters']
                save_json(directory/'inspection-request.json',inspection)
                self.subprocess([sys.executable,'-m','drawform.workbench.inspect_simple' if generic else 'drawform.workbench.inspect_plate',str(directory/'inspection-request.json')],directory,directory/'readback.log',source_path=ROOT/'drawform/src')
                checked=json.loads((output/'drawform-validation.json').read_text())
                preview=json.loads((output/'preview.json').read_text())
                if not checked.get('passed') or checked.get('step_sha256')!=sha or preview.get('step_sha256')!=sha:raise RuntimeError('跨内核STEP读回或预览版本核验失败。')
                validation=json.loads((output/'validation.json').read_text())
                if not validation.get('passed') or validation.get('step_sha256')!=sha:raise RuntimeError('建模端独立检查未通过。')
                hashes={k:digest(output/name) for k,name in {'step':step_filename,'preview':'preview.json','validation':'validation.json','drawform_validation':'drawform-validation.json'}.items()}
                if hashes['step']!=sha:raise RuntimeError('检查完成后STEP发生变化，拒绝发布候选。')
                save_json(directory/'manifest.json',{'schema_version':1,'parameters':data['parameters'],'text_sha256':data['text_sha256'],'artifacts':hashes,'runtime':result.get('runtime'),'spec':data.get('spec'),'input_kind':data.get('input_kind','text'),'source_sha256':data.get('source_sha256'),'revision_provenance':data.get('revision_provenance'),'human_review':'not_reviewed'})
                with self.lock:
                    if self.get(sid,owner)['state']=='building' and not self.stopped.is_set():
                        self.verify_input_source(data,owner)
                        self.revisions.verify_lineage(data,owner)
                        values={'step_sha256':sha,'step_filename':step_filename,'artifact_hashes':hashes,'validation':checked}
                        if identity:
                            values['model_attempt_id']=identity
                            self.attempts.capture(sid,owner,identity,directory,{'state':'awaiting_adoption','values':values})
                            self.attempts.complete(sid,owner,identity,'awaiting_adoption',values)
                        else:self.update(sid,owner,'awaiting_adoption',**values)
            except Exception as error:
                with self.lock:
                    if self.get(sid,owner)['state'] in ('queued','building','analyzing'):
                        state='interrupted' if self.stopped.is_set() else 'failed'
                        if identity:self.attempts.set(identity,state,reason='service_shutdown' if self.stopped.is_set() else str(error))
                        self.update(sid,owner,state,error=str(error))
            finally:self.pending.task_done()

    def stop(self):
        self.stopped.set()
        with self.lock:active=list(self.active)
        for process in active:
            if process.poll() is None:
                try:os.killpg(process.pid,signal.SIGTERM)
                except ProcessLookupError:pass
                try:process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    try:os.killpg(process.pid,signal.SIGKILL)
                    except ProcessLookupError:pass
                    process.wait(timeout=3)
        self.pending.put(None)
        if threading.current_thread() is not self.worker:self.worker.join(timeout=5)

    def adopt(self,sid,owner,sha):
        with self.lock:
            data=self.get(sid,owner)
            if data['state'] not in ('awaiting_adoption','adopted') or sha!=data.get('step_sha256'):
                raise Conflict('候选模型已改变或尚未就绪，请重新核对。')
            for kind in ('step','preview','validation','drawform_validation'):self.file(sid,owner,kind)
            save_json(self.directory(sid)/'adoption.json',{'candidate_id':sid,'step_sha256':sha,'parameters':data['parameters'],'adopted_by_seat':owner[:8],'human_review':'not_reviewed'})
            self.update(sid,owner,'adopted')
            return self.get(sid,owner)

    def drawing(self,sid,owner,sha):
        with self.lock:
            data=self.get(sid,owner)
            if data['state']!='adopted' or sha!=data.get('step_sha256'):raise Conflict('请先核对并采用当前模型，再生成工程图。')
            path=self.file(sid,owner,'step')
            if not data.get('job_id'):
                name=('图纸派生的' if data.get('input_kind')=='drawing' else '文字生成的')+({'washer':'垫圈圆盘','stepped_shaft':'二段阶梯轴'}.get(data.get('model_kind'),'板件'))
                if data.get('revision_provenance'):name+='-模型版本'+str(data['revision_provenance']['number'])
                job=self.jobs.submit_once(path,request_id=f'model:{owner}:{sid}',expected_sha=sha,display_name=name+'.step',sheet=data['sheet'])
                save_json(self.directory(sid)/'drawing-binding.json',{'job_id':job,'step_sha256':sha,'session_id':sid,'text_sha256':data['text_sha256'],'parameters':data['parameters'],'human_review':'not_reviewed'})
                self.update(sid,owner,'adopted',job_id=job)
            return {'job_id':self.get(sid,owner)['job_id'],'step_sha256':sha,'review_url':f'/?job={self.get(sid,owner)["job_id"]}'}

"""Durable, source-bound and bounded recovery of local modeling operations."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import time
import uuid
from datetime import datetime,timezone
from .modeling import Conflict,digest,save_json
from .revisions import canonical_sha,normalized_spec


def intent(data,kind):
    result={'schema_version':1,'kind':kind,'bundle_id':data['bundle_id'],
            'input_kind':data.get('input_kind','text'),'source_sha256':data['source_sha256'],'sheet':data['sheet']}
    if kind=='build':
        result.update(spec=normalized_spec(data),revision_provenance=data.get('revision_provenance'),
                      analysis_basis_sha256=data.get('basis_sha256'),clarification_revision=data.get('clarification_revision'))
    return result


def bind_intent(data,kind):
    data['execution_intent']=intent(data,kind)
    data['execution_intent_sha256']=canonical_sha(data['execution_intent'])
    return data


def process_identity(pid):
    result=subprocess.run(['ps','-p',str(pid),'-o','lstart=','-o','command='],capture_output=True,text=True,timeout=5)
    return result.stdout.strip() if result.returncode==0 else ''


class ModelAttempts:
    MAX_RECOVERIES=3

    def __init__(self,service):
        self.s=service
        with service.connect() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS model_attempts(
id TEXT PRIMARY KEY,session_id TEXT NOT NULL REFERENCES sessions(id),owner TEXT NOT NULL,
kind TEXT NOT NULL,state TEXT NOT NULL,basis_sha TEXT NOT NULL,data TEXT NOT NULL);''');db.commit()

    def _checkpoint(self,stage):pass

    def history(self,sid,owner):
        self.s.raw(sid,owner)
        with self.s.connect() as db:
            rows=db.execute('SELECT * FROM model_attempts WHERE session_id=? AND owner=? ORDER BY rowid',(sid,owner)).fetchall()
        return [{'attempt_id':r['id'],'kind':r['kind'],'state':r['state'],'basis_sha256':r['basis_sha'],
                 **json.loads(r['data'])} for r in rows]

    def set(self,identity,state,**values):
        with self.s.connect() as db:
            row=db.execute('SELECT data FROM model_attempts WHERE id=?',(identity,)).fetchone()
            data=json.loads(row['data']);data.update(values)
            db.execute('UPDATE model_attempts SET state=?,data=? WHERE id=?',(state,json.dumps(data,ensure_ascii=False),identity));db.commit()

    def _runtime(self,kind,data):
        host=self.s.source_root/'drawform/workbench'
        names=('drawing_intake.py','shaft_input.py','simple_spec.py','child_guard.py') if kind=='analysis' else (
            'inspect_simple.py','inspect_plate.py','simple_spec.py','plate_spec.py','child_guard.py')
        paths=[host/name for name in names]
        if kind=='build':paths.extend(sorted(self.s.runner.parent.glob('*.py')))
        return canonical_sha({p.name:digest(p) for p in paths})

    def verify(self,data,owner,kind):
        self.s.verify_input_source(data,owner)
        self.s.revisions.verify_lineage(data,owner)
        frozen=intent(data,kind)
        if data.get('execution_intent')!=frozen or data.get('execution_intent_sha256')!=canonical_sha(frozen):
            raise Conflict('续算输入、规格或父版本依据已改变，已停止自动恢复。')
        return frozen

    def begin(self,sid,owner,kind):
        s=self.s
        with s.lock:
            data=s.raw(sid,owner);frozen=self.verify(data,owner,kind)
            identity=uuid.uuid4().hex
            with s.connect() as db:
                number=db.execute('SELECT count(*) FROM model_attempts WHERE session_id=? AND kind=?',(sid,kind)).fetchone()[0]+1
                if number>self.MAX_RECOVERIES+1:raise Conflict('自动续算已达到3次上限，请核对记录后新建任务。')
                record={'number':number,'directory':'attempts/'+identity,'frozen':frozen,'runtime_sha256':self._runtime(kind,data),
                        'created_at':datetime.now(timezone.utc).isoformat(),'reason':''}
                directory=s.directory(sid)/record['directory'];directory.mkdir(parents=True,exist_ok=False)
                save_json(directory/'intent.json',record)
                db.execute('INSERT INTO model_attempts VALUES(?,?,?,?,?,?,?)',(identity,sid,owner,kind,'prepared',canonical_sha(frozen),json.dumps(record,ensure_ascii=False)));db.commit()
            s.update(sid,owner,'analyzing' if kind=='analysis' else 'building',current_attempt_id=identity,error='')
            self.set(identity,'running');self._checkpoint('running')
            return identity,directory

    def active(self,sid,owner,identity):
        data=self.s.raw(sid,owner)
        return not self.s.stopped.is_set() and data.get('current_attempt_id')==identity and data['state'] in ('building','analyzing')

    def capture(self,sid,owner,identity,directory,result):
        files={str(p.relative_to(directory)):digest(p) for p in directory.rglob('*') if p.is_file() and p.name!='process.json'}
        self.set(identity,'validated',files=files,result=result)
        self._checkpoint('validated')

    def complete(self,sid,owner,identity,state,values):
        s=self.s
        with s.lock,s.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT state,data FROM sessions WHERE id=? AND owner=?',(sid,owner)).fetchone()
            data=json.loads(row['data'])
            if s.stopped.is_set() or data.get('current_attempt_id')!=identity or row['state'] not in ('building','analyzing'):
                raise Conflict('执行尝试已变化，迟到结果不会发布。')
            kind='analysis' if row['state']=='analyzing' else 'build';self.verify(data,owner,kind)
            data.update(values);data['error']=''
            if state=='queued':bind_intent(data,'build')
            db.execute('UPDATE sessions SET state=?,data=? WHERE id=?',(state,json.dumps(data,ensure_ascii=False),sid))
            db.execute("UPDATE model_attempts SET state='complete' WHERE id=?",(identity,));db.commit()
        self._checkpoint('committed')

    def _reap(self,directory):
        path=directory/'process.json'
        if not path.is_file() or path.is_symlink():return
        record=json.loads(path.read_text());pid=record.get('pid')
        if (type(pid) is not int or pid<=1 or Path(record.get('cwd','')).resolve()!=directory.resolve()
                or process_identity(pid)!=record.get('identity') or not record.get('identity')):return
        try:
            if os.getpgid(pid)!=pid:return
            os.killpg(pid,signal.SIGTERM)
            for _ in range(30):
                if process_identity(pid)!=record['identity']:return
                time.sleep(.1)
            if process_identity(pid)==record['identity']:os.killpg(pid,signal.SIGKILL)
        except ProcessLookupError:pass

    def recover(self):
        """Called before the sole model worker starts; never run CAD here."""
        s=self.s
        with s.connect() as db:
            rows=db.execute("SELECT * FROM sessions WHERE state IN ('queued','building','analyzing','interrupted')").fetchall()
        for row in rows:
            data=json.loads(row['data']);sid=row['id'];owner=row['owner']
            if (not data.get('execution_intent_sha256') or data.get('step_sha256')
                    or data.get('interrupted_reason')=='cancelled' or data.get('recovery_blocked')):continue
            kind=data.get('execution_intent',{}).get('kind')
            if kind not in ('build','analysis'):continue
            try:
                interrupted=data.get('interrupted_from_state',row['state'])
                expected_kind='build' if interrupted in ('queued','building') else 'analysis' if interrupted=='analyzing' else kind
                if kind!=expected_kind:raise Conflict('中断阶段与冻结意图不一致，停止恢复并保留已确认答案。')
                self.verify(data,owner,kind)
                history=self.history(sid,owner)
                relevant=[a for a in history if a['kind']==kind]
                for attempt in history:
                    if attempt['state'] in ('prepared','running','validated'):
                        directory=s.directory(sid)/attempt['directory'];self._reap(directory)
                        self.set(attempt['attempt_id'],'interrupted',reason='service_restart',
                                 retained_files={str(p.relative_to(directory)):digest(p) for p in directory.rglob('*') if p.is_file()})
                if relevant and relevant[-1]['runtime_sha256']!=self._runtime(kind,data):raise Conflict('运行协议已改变，不能自动续算旧任务。')
                if len(relevant)>self.MAX_RECOVERIES:raise Conflict('自动续算已达到3次上限。')
                if relevant and relevant[-1]['state'] in ('failed','blocked'):continue
                s.update(sid,owner,'analyzing' if kind=='analysis' else 'queued',error='服务中断后正在自动续算，历史尝试保留。')
                s.pending.put((sid,owner))
            except (OSError,ValueError,KeyError) as error:
                s.update(sid,owner,'interrupted',error=str(error),recovery_blocked=True)

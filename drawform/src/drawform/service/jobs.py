"""任务表与单 worker 线程。

选异步的代价写在这里，不藏在代码里：

* **任务表是盘上 `jobs.jsonl` 的缓存**：重启后已出图的任务、以及工程师回写出来的
  生效版本链，都还能查回来。这条不是洁癖 —— 生效版本是人做出来的东西，
  丢了要比"重新上传一个 STEP"贵得多。目录被清理掉的任务不会被装回来。
* 只有一个 worker：OCCT 在多线程下的安全性没有验证过，串行既避开这个问题，
  也让"排队"是真的在排队而不是装饰。重启时还没跑完的任务被记成 `crashed`
  并写明"请重新提交" —— 让它一直显示"排队中"是骗人，显示"已出图"是假账。
* 产物按任务号落目录，按"已结束的个数"保留，默认 200 个。
"""

from __future__ import annotations

import hashlib
import base64
import os
import json
import pathlib
import queue
import shutil
import threading
import uuid
from dataclasses import asdict, dataclass, replace
from contextlib import contextmanager
from datetime import datetime, timezone

from drawform.delivery import DeliveryBlocked
from drawform.pipeline import render_part
from drawform.review import ReviewLog
from drawform.service.journal import Journal, STAGES, digest, relative_file, sync_directory, write_new, validate as validate_publications
from drawform.service.answers import (Answer, AnswerError, drawing_annotations, drawing_changes,
                                      validate_answers)
from drawform.service.questions import Option, Question
from drawform.geometry.references import (ReferenceRequired, ReferenceInputError,
                                          validate_submission, BASIS_VERSION)

QUEUED = "queued"
RUNNING = "running"
DRAWN = "drawn"
#: 门拦下了。这是**业务结论**，与 CRASHED 分开：工程师要看到的是"哪一项没过"，
#: 而不是一个笼统的"出图失败"。
REFUSED = "refused"
CRASHED = "crashed"
WAITING_REFERENCE = "waiting_reference"

#: 生效版本的文件名前缀。`amended-1.dxf`、`amended-2.dxf` … 按上传顺序排。
VERSION_PREFIX = "amended-"

#: 任务表在盘上的那一份。内存里的那张只是它的缓存。
INDEX_NAME = "jobs.jsonl"
#: 重启时还没跑完的任务会被记成这个状态 ——  worker 线程已经死了，
#: 让它一直显示"排队中"等于骗人，让它显示"已出图"更是假账。
INTERRUPTED = "服务重启时这个任务还没跑完，请重新提交"


class StaleBase(Exception):
    """改后的图不是基于**当前头版**改的。

    不拦的话：工程师拿着三个月前下载的底稿改完传回来，中间那一版就被无声盖掉，
    而审计里两条记录都写着"我已生效"。这张图到底哪一版算数就再也问不出来了。
    """


@dataclass(frozen=True)
class Version:
    """一次回写落成的那一版，连它相对谁算出来的。"""

    number: int
    path: pathlib.Path
    parent: pathlib.Path
    """直接父版：上一版生效图，第一版的父版是机器底稿。差异只与它比。"""


@dataclass(frozen=True)
class AnswerRun:
    key: str
    reviewer: str
    answers: tuple[Answer, ...]
    questions: tuple[Question, ...]
    base_sha: str
    base_version: int
    token_hint: str = ""
    token_user: str = ""
    state: str = QUEUED
    version: int = 0
    files: tuple = ()
    diff: tuple = ()
    error: str = ""
    reason: str = ""


@dataclass(frozen=True)
class Job:
    id: str
    state: str
    directory: pathlib.Path
    source: pathlib.Path
    files: tuple = ()
    failed: tuple = ()
    reasons: tuple = ()
    error: str = ""
    questions: tuple[Question, ...] = ()
    answer_runs: tuple[AnswerRun, ...] = ()
    reference_snapshot: dict | None = None
    reference_runs: tuple[dict, ...] = ()
    display_name: str = ""
    created_at: str = ""
    source_sha256: str = ""
    sheet: str = "A3"

    @property
    def finished(self) -> bool:
        return self.state in (DRAWN, REFUSED, CRASHED)


def versions_of(job: Job) -> tuple[pathlib.Path, ...]:
    """这份底稿上已经生效的版本，按上传顺序。"""
    return tuple(path for kind, path in job.files if kind.startswith(VERSION_PREFIX))


def head_of(job: Job) -> pathlib.Path | None:
    """当前生效版本。还没人回写过就没有"生效版本"这回事，返回 None 而不是底稿。"""
    versions = versions_of(job)
    return versions[-1] if versions else None


def latest_answers(job: Job) -> AnswerRun | None:
    return next((run for run in reversed(job.answer_runs) if run.state == DRAWN), None)


def answers_of(job: Job) -> tuple[Answer, ...]:
    return tuple(answer for run in job.answer_runs if run.state == DRAWN for answer in run.answers)


def reference_submission(job: Job):
    """图后重算复用已确认的来源快照；仍由流水线重新核验几何。"""
    return next((run['submission'] for run in reversed(job.reference_runs)
                 if run.get('state')==DRAWN),None)


def current_files(job: Job) -> dict:
    latest = latest_answers(job)
    return dict(latest.files if latest is not None else job.files)


def _with_run(job, run):
    return replace(job, answer_runs=tuple(run if item.key == run.key else item
                                         for item in job.answer_runs))


def _questions_from(rows):
    return tuple(Question(**{**item, "options": tuple(Option(**option) for option in item["options"])})
                 for item in rows)


def _run_from(row, directory):
    run = AnswerRun(**{**row, "answers": tuple(Answer(**answer) for answer in row["answers"]),
                       "questions": _questions_from(row["questions"]),
                       "files": tuple((kind, directory / name) for kind, name in row["files"]
                                      if (directory / name).is_file()),
                       "diff": tuple(tuple(pair) for pair in row["diff"])})
    if run.state in (QUEUED, RUNNING):
        return replace(run, state=CRASHED, error=INTERRUPTED, reason="interrupted")
    return run


def _captured_run_from(row, directory):
    """Decode an exact publication target before its future files are installed.

    Legacy loading deliberately filters missing files and interrupts active runs.
    Applying those recovery rules to a captured target would discard companions
    that the publication is about to create, then persist that incomplete target.
    """
    return AnswerRun(**{**row, 'answers':tuple(Answer(**answer) for answer in row['answers']),
                        'questions':_questions_from(row['questions']),
                        'files':tuple((kind,directory/name) for kind,name in row['files']),
                        'diff':tuple(tuple(pair) for pair in row['diff'])})


class JobQueue:
    """一个后台线程按提交顺序取任务。`render` 参数只为测试可替换而存在。"""

    def __init__(self, work_dir, render=render_part, review_log=None, *, auto_resume=False):
        self._work = pathlib.Path(work_dir).resolve()
        self._render = render
        self._jobs: dict[str, Job] = {}
        self._lock = threading.RLock()
        self._pending: queue.Queue = queue.Queue()
        self._worker: threading.Thread | None = None
        self._stopping = threading.Event()
        self._index = self._work / INDEX_NAME
        self._audit = review_log if review_log is not None else ReviewLog(self._work / "reviews.jsonl")
        self._journal = Journal(self._work)
        self._auto_resume = bool(auto_resume)
        self._load()

    # ---------------------------------------------------------------- 盘上那一份

    def _row(self, job):
        """把任务当前状态追加进索引。**调用方必须已经持有 `self._lock`**：
        追加写靠这把锁串行化，自己再取一次锁会死锁。
        """
        row = {"job": job.id, "state": job.state, "source": job.source.name,
               "files": [[kind, str(path.relative_to(job.directory))] for kind, path in job.files],
               "failed": list(job.failed), "reasons": list(job.reasons), "error": job.error,
               "questions": [asdict(question) for question in job.questions],
               "answer_runs": [{**asdict(run), "files": [
                   [kind, str(path.relative_to(job.directory))] for kind, path in run.files]}
                   for run in job.answer_runs]}
        row.update(display_name=job.display_name, created_at=job.created_at, source_sha256=job.source_sha256, sheet=job.sheet)
        if job.reference_snapshot is not None or job.reference_runs:
            row['reference_snapshot'] = job.reference_snapshot
            row['reference_runs'] = list(job.reference_runs)
        return row

    def _record(self, job: Job) -> None:
        self._work.mkdir(parents=True, exist_ok=True)
        row = self._row(job)
        with self._index.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            handle.flush(); os.fsync(handle.fileno())

    def _load(self) -> None:
        """重启后从盘上把任务表装回来。装不回来的宁可不装：
        挂着找不到的文件比说"这张图没了"更坏 —— 交付包会导出一个空 zip。
        """
        if not self._index.exists():
            return
        rows: dict[str, dict] = {}
        for line in self._index.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue        # 断电时的半截 JSON：跳过这一行，别丢整张表
            if isinstance(row, dict) and row.get("job"):
                rows[row["job"]] = row
        for job_id, row in rows.items():
            sheet = row.get('sheet', 'A3')
            if sheet not in ('A3', 'A2'):
                raise ValueError(f'任务 {job_id} 的纸幅记录无效，不能猜测恢复')
            directory = self._work / job_id
            if not directory.is_dir():
                continue        # 被清理掉的目录不诈尸
            source = directory / str(row.get("source", ""))
            files = [(str(kind), directory / name) for kind, name in
                     (row.get("files") or ()) if (directory / str(name)).exists()
                     or str(kind).startswith(VERSION_PREFIX)]
            state = str(row.get("state", ""))
            error = str(row.get("error", ""))
            if state in (QUEUED, RUNNING):
                state, error = CRASHED, INTERRUPTED
            elif state == DRAWN and not any(kind == "dxf" for kind, _ in files):
                state, error = CRASHED, "重启后机器底稿已不在盘上，请重新出图"
            if any(not path.is_file() for kind,path in files if kind.startswith(VERSION_PREFIX)):
                state,error = CRASHED,'历史生效图缺失，保留版本槽位；请核对备份，不能重编号或覆盖'
            snapshot = row.get('reference_snapshot')
            reference_runs = row.get('reference_runs',[])
            if (not isinstance(reference_runs,list) or any(not isinstance(r,dict)
                    or r.get('state') not in (QUEUED,RUNNING,DRAWN,REFUSED,CRASHED)
                    or not isinstance(r.get('key'),str) or not isinstance(r.get('submission'),dict)
                    for r in reference_runs)):
                state,error,reference_runs = CRASHED,'图前参考记录损坏，不能猜测恢复',[]
            if state==WAITING_REFERENCE and (not isinstance(snapshot,dict)
                    or not isinstance(snapshot.get('questions'),list) or not snapshot['questions']
                    or not source.is_file()):
                state,error = CRASHED,'等待参考确认的来源或候选缺失，请重新提交'
            reference_runs = [{**r,'state':CRASHED,'error':INTERRUPTED}
                              if r.get('state') in (QUEUED,RUNNING) else r for r in reference_runs]
            self._jobs[job_id] = Job(id=job_id, state=state, directory=directory,
                                     source=source if source.exists() else directory,
                                     files=tuple(files),
                                     failed=tuple(row.get("failed") or ()), error=error,
                                     reasons=tuple(row.get("reasons") or ()),
                                     questions=_questions_from(row.get("questions", ())),
                                     answer_runs=tuple(_run_from(run, directory)
                                                       for run in row.get("answer_runs", ())),
                                     reference_snapshot=snapshot if isinstance(snapshot,dict) else None,
                                     reference_runs=tuple(reference_runs),
                                     display_name=str(row.get("display_name", "")),
                                     created_at=str(row.get("created_at", "")),
                                     source_sha256=str(row.get("source_sha256", "")), sheet=sheet)

    def _checkpoint(self, stage):
        """Fault-injection seam; production leaves it empty."""

    def _operation(self, job_id, kind, key=None):
        rows = [row for row in self._journal.export([job_id]) if row['kind'] == kind
                and (key is None or row['request_key'] == key)]
        return next((row for row in rows if row['stage'] != 'complete'), rows[-1] if rows else None)

    def _job_from_row(self, row):
        directory = self._work / row['job']
        return Job(id=row['job'], state=row['state'], directory=directory,
                   source=directory / row['source'], files=tuple((k, directory / n) for k,n in row['files']),
                   failed=tuple(row.get('failed', ())), reasons=tuple(row.get('reasons', ())),
                   error=row.get('error', ''), questions=_questions_from(row.get('questions', ())),
                   answer_runs=tuple(_captured_run_from(run, directory) for run in row.get('answer_runs', ())),
                   reference_snapshot=row.get('reference_snapshot'), reference_runs=tuple(row.get('reference_runs', ())),
                   display_name=row.get('display_name', ''), created_at=row.get('created_at', ''),
                   source_sha256=row.get('source_sha256', ''), sheet=row.get('sheet', 'A3'))

    def _prepare(self, kind, key, job, payload):
        old = self._journal.lookup(kind, key)
        if old is not None:
            if kind=='review' and ({k:v for k,v in old['payload']['audit'].items() if k!='at'}
                    != {k:v for k,v in payload['audit'].items() if k!='at'}):
                raise ValueError('发布请求编号对应不同复核内容')
            return old
        op = self._journal.prepare(kind, key, job.id if isinstance(job, Job) else job, payload)
        self._checkpoint('prepared')
        return op

    def _attempt(self, operation):
        directory = self._journal.root / operation['operation_id'] / 'attempts' / uuid.uuid4().hex
        directory.mkdir(parents=True, exist_ok=False)
        sync_directory(directory.parent)
        sync_directory(directory.parent.parent)
        return directory

    def _capture(self, operation, target, files=(), audits=(), extra=None):
        """Bind staging bytes, all target file slots and exact audit rows before publication."""
        artifacts = []
        for staged, destination in files:
            if staged.is_symlink() or not staged.is_file(): raise StaleBase('发布暂存产物缺失或为链接')
            with staged.open('rb') as handle: os.fsync(handle.fileno())
            artifacts.append({'staged': staged.relative_to(self._work).as_posix(),
                              'path': destination.relative_to(self._work).as_posix(),
                              'sha256': hashlib.sha256(staged.read_bytes()).hexdigest()})
        op = self._journal.advance(operation, 'captured',
                                   {'artifacts': artifacts, 'job_row': self._row(target), 'audits': list(audits), **(extra or {})})
        self._checkpoint('captured')
        return self._commit(op)

    def _commit(self, operation):
        with self.review_guard():
            op = self._journal.get(operation['operation_id'])
            target = self._job_from_row(op['data']['job_row'])
            current = self._jobs.get(target.id)
            expected = dict(target.files)
            already = bool(current and expected and current.state == target.state
                           and all(dict(current.files).get(k) == p for k,p in expected.items()))
            if not expected:
                already = bool(current and self._row(current) == self._row(target))
            payload = op['payload']
            if target.state == WAITING_REFERENCE and not already:
                target_requests = {r['key']:digest(r['submission']) for r in target.reference_runs}
                current_requests = {r['key']:digest(r['submission']) for r in current.reference_runs} if current else {}
                if (current is None or current.state not in (RUNNING,WAITING_REFERENCE,CRASHED)
                        or any(target_requests.get(key)!=value for key,value in current_requests.items())
                        or (current.state==WAITING_REFERENCE and current.reference_snapshot
                            and (current.reference_snapshot or {}).get('basis_sha')
                                != (target.reference_snapshot or {}).get('basis_sha'))):
                    raise StaleBase('参考确认请求或候选依据已变化，旧等待结果不能覆盖新状态')
            if op['kind'] == 'review':
                parent = head_of(current) or dict(current.files).get('dxf') if current else None
                already = bool(current and len(versions_of(current)) == payload['base_version']
                               and parent and parent.is_file()
                               and hashlib.sha256(parent.read_bytes()).hexdigest() == payload['base_sha'])
                if not already: raise StaleBase('复核对象版本已变化，不能自动提交旧复核')
                audit = payload['audit']
                existing = any(r.get('publication_id') == op['operation_id'] for r in self._audit.for_job(target.id))
                if audit.get('decision') == 'accepted' and not existing and target.id in self._audit.frozen():
                    raise StaleBase('复核对象已冻结，不能恢复为直接用')
            if target.state in (DRAWN,WAITING_REFERENCE) and (target.source.is_symlink() or not target.source.is_file()
                    or hashlib.sha256(target.source.read_bytes()).hexdigest() != (payload.get('source_sha256') or target.source_sha256)):
                raise StaleBase('恢复发布来源已变化，历史记录保留，不能覆盖')
            if target.state == DRAWN and op['kind'] in ('answers', 'amendment', 'review') and not already:
                if current is None: raise StaleBase('发布父任务缺失')
                parent = head_of(current) or dict(current.files).get('dxf')
                if (parent is None or not parent.is_file() or parent.is_symlink()
                        or hashlib.sha256(parent.read_bytes()).hexdigest() != payload.get('base_sha')
                        or len(versions_of(current)) != payload.get('base_version')):
                    raise StaleBase('发布父版已变化，不能恢复覆盖新版本')
                if op['kind'] == 'answers' and target.id in self._audit.frozen():
                    raise AnswerError('恢复期间收到数值级错误复核，答案不能生效', 'frozen', 409)
            for entry in op['data'].get('artifacts', []):
                staged = relative_file(self._work, entry['staged'])
                destination = relative_file(self._work, entry['path'])
                if not staged.is_file() or hashlib.sha256(staged.read_bytes()).hexdigest() != entry['sha256']:
                    raise StaleBase('发布暂存产物失配，不能猜测恢复')
                if destination.exists() or destination.is_symlink():
                    if destination.is_symlink() or not destination.is_file() or hashlib.sha256(destination.read_bytes()).hexdigest() != entry['sha256']:
                        raise StaleBase('目标产物已存在且不同，禁止覆盖历史')
                else: write_new(destination, staged.read_bytes())
            if STAGES.index(op['stage']) < 2:
                op = self._journal.advance(op, 'artifacts_committed'); self._checkpoint('artifacts_committed')
            for number, row in enumerate(op['data'].get('audits', [])):
                self._audit.append_once(row, op['operation_id'], number)
            if STAGES.index(op['stage']) < 3:
                op = self._journal.advance(op, 'audit_committed'); self._checkpoint('audit_committed')
            if not already:
                self._record(target)
                self._jobs[target.id] = target
            if op['stage'] != 'complete':
                self._journal.advance(op, 'complete'); self._checkpoint('complete')
            return self._jobs[target.id]

    def _recover_operation(self, operation_id):
        op = self._journal.get(operation_id)
        if op['stage'] == 'complete': return
        try:
            if STAGES.index(op['stage']) >= 1:
                self._commit(op); return
            job = self._jobs.get(op['job_id'])
            if op['kind'] == 'submission':
                job = self._admit(op)
                self._execute(job.id)
            elif op['kind'] == 'answers' and job is not None:
                run = _run_from(op['payload']['run'], job.directory)
                run = replace(run, state=QUEUED, error='', reason='')
                with self._lock:
                    self._audit.append_once(op['payload']['queued_audit'],op['operation_id'],-1)
                    restored = _with_run(job,run)
                    if not any(old.key == run.key for old in restored.answer_runs):
                        restored = replace(restored,answer_runs=restored.answer_runs+(run,))
                    self._jobs[job.id] = replace(restored, state=QUEUED, error='')
                    self._record(self._jobs[job.id])
                self._execute_answers(job.id, run)
            elif op['kind'] == 'reference' and job is not None:
                run = op['payload']['run']
                with self._lock:
                    runs = tuple(run if old['key'] == run['key'] else old for old in job.reference_runs)
                    if not any(old['key'] == run['key'] for old in runs): runs += (run,)
                    job = replace(job, state=run['state'], error='', reference_runs=runs)
                    self._jobs[job.id] = job; self._record(job)
                if run['state'] == REFUSED: self._capture(op,job)
                else: self._execute(job.id)
            elif op['kind'] == 'amendment':
                self._finish_amendment(op)
            elif op['kind'] == 'review':
                # Replays the original explicit decision; never creates a decision from geometry.
                self._capture(op, job, audits=(op['payload']['audit'],))
        except Exception as error:
            self._journal.interrupted(op, error)
            current=self._jobs.get(op['job_id'])
            owns=False
            if current is not None:
                if op['kind']=='submission':
                    owns=not current.reference_runs and not current.answer_runs and not versions_of(current)
                elif op['kind']=='reference':
                    owns=(bool(current.reference_runs) and current.reference_runs[-1]['key']==op['payload']['run']['key']
                          and not current.answer_runs and not versions_of(current))
                elif op['kind']=='answers':
                    owns=(bool(current.answer_runs) and current.answer_runs[-1].key==op['payload']['run']['key']
                          and len(versions_of(current))<=op['payload']['base_version']+1)
            # An older operation may be blocked after a newer confirmation or
            # publication. Record its failure without changing the newer Job.
            if owns:
                self._set(op['job_id'], state=CRASHED, error=f'恢复发布被阻断：{error}')

    def start(self) -> None:
        if self._worker is not None:
            return
        self._worker = threading.Thread(target=self._run, name="drawform-worker",
                                        daemon=True)
        if self._auto_resume:
            for operation in self._journal.export():
                if operation['stage'] != 'complete':
                    self._pending.put(('publication', operation['operation_id']))
        # 恢复等待任务后在同一几何worker重新枚举，HTTP/构造函数不调用OCCT。
        for job in self._jobs.values():
            if job.state==WAITING_REFERENCE:
                self._pending.put(job.id)
        self._worker.start()

    def stop(self, timeout: float = 30.0) -> None:
        self._stopping.set()
        self._pending.put(None)
        if self._worker is not None:
            self._worker.join(timeout)
            self._worker = None

    def submit_once(self, step_path, *, request_id, expected_sha, display_name="", sheet="A3"):
        """稳定受理receipt；创建Job前保存分配ID，重试沿同一ID完成。"""
        if not isinstance(request_id,str) or not request_id or len(request_id)>256 or sheet not in ('A3','A2'):
            raise ValueError('无效的受理请求或纸幅')
        data=pathlib.Path(step_path).read_bytes()
        sha=hashlib.sha256(data).hexdigest()
        if not expected_sha or sha!=expected_sha:raise StaleBase('STEP已改变，请重新核对模型')
        fingerprint=hashlib.sha256(json.dumps({'sha':sha,'sheet':sheet,'name':display_name},sort_keys=True).encode()).hexdigest()
        receipts=self._work/'job-submissions'
        path=receipts/(hashlib.sha256(request_id.encode()).hexdigest()+'.json')
        def durable(row):
            receipts.mkdir(parents=True,exist_ok=True)
            temporary=path.with_suffix('.tmp')
            with temporary.open('w',encoding='utf-8') as handle:
                json.dump(row,handle,ensure_ascii=False);handle.flush();os.fsync(handle.fileno())
            os.replace(temporary,path)
            fd=os.open(receipts,os.O_RDONLY)
            try:os.fsync(fd)
            finally:os.close(fd)
        with self._lock:
            previous = self._journal.lookup('submission', request_id)
            if path.exists():
                row=json.loads(path.read_text())
                if row.get('kind')!='job_submission' or row.get('payload_sha')!=fingerprint:
                    raise ValueError('受理请求编号对应不同输入')
            else:
                row={'kind':'job_submission','job_id':previous['job_id'] if previous else uuid.uuid4().hex[:12],
                     'payload_sha':fingerprint,'state':'prepared','source_sha256':sha,'sheet':sheet}
            job_id=row['job_id']
            if previous and previous['payload'].get('fingerprint') != fingerprint:
                raise ValueError('受理请求编号对应不同输入')
            op = previous or self._prepare('submission', request_id, job_id,
                    {'source_path':job_id+'/source.step','source_sha256':sha,
                     'source_bytes':base64.b64encode(data).decode(),'fingerprint':fingerprint,
                     'display_name':display_name,'sheet':sheet,
                     'created_at':datetime.now(timezone.utc).isoformat(timespec='seconds')})
            if not path.exists(): durable(row)
            if job_id in self._jobs:
                job=self._jobs[job_id]
                if not job.source.is_file() or hashlib.sha256(job.source.read_bytes()).hexdigest()!=sha:
                    raise ValueError('已受理任务源文件损坏，禁止重建覆盖')
                if row['state']!='complete':row['state']='complete';durable(row)
                return job_id
            job=self._admit(op)
            row['state']='complete';durable(row)
            self._pending.put(job_id)
            return job_id

    def _admit(self, op):
        payload = op['payload']; job_id = op['job_id']
        directory = self._work / job_id
        if not directory.exists() and self._index.is_file():
            for line in self._index.read_text(encoding='utf-8').splitlines():
                try: row = json.loads(line)
                except json.JSONDecodeError: continue
                if row.get('job') == job_id: raise StaleBase('已登记任务目录缺失，不能自动重建历史')
        directory.mkdir(parents=True, exist_ok=True)
        stored = relative_file(self._work, payload['source_path'])
        data = base64.b64decode(payload['source_bytes'], validate=True)
        if hashlib.sha256(data).hexdigest() != payload['source_sha256']:
            raise StaleBase('受理来源内容损坏')
        if stored.exists() or stored.is_symlink():
            if stored.is_symlink() or not stored.is_file() or hashlib.sha256(stored.read_bytes()).hexdigest() != payload['source_sha256']:
                raise StaleBase('已分配任务来源不一致，禁止覆盖')
        else: write_new(stored, data)
        existing = self._jobs.get(job_id)
        if existing and existing.state not in (CRASHED, QUEUED, RUNNING): return existing
        job = Job(id=job_id,state=QUEUED,directory=directory,source=stored,
                  display_name=payload['display_name'],created_at=payload['created_at'],
                  source_sha256=payload['source_sha256'],sheet=payload['sheet'])
        self._record(job); self._jobs[job_id] = job
        return job

    def submit(self, step_path, stem: str | None = None, *, display_name: str = "", sheet: str = "A3") -> str:
        if sheet not in ('A3', 'A2'):
            raise ValueError('纸幅只支持 A3 或 A2')
        source = pathlib.Path(step_path); data = source.read_bytes()
        job_id = uuid.uuid4().hex[:12]
        with self._lock:
            op = self._prepare('submission', 'legacy:'+uuid.uuid4().hex, job_id,
                    {'source_path':job_id+'/'+source.name,'source_sha256':hashlib.sha256(data).hexdigest(),
                     'source_bytes':base64.b64encode(data).decode(), 'display_name':display_name,'sheet':sheet,
                     'created_at':datetime.now(timezone.utc).isoformat(timespec='seconds')})
            self._admit(op)
        self._pending.put(job_id)
        return job_id

    @contextmanager
    def review_guard(self):
        """Use one queue → audit lock order for version publication and review."""
        with self._lock, self._audit._lock:
            yield

    def get(self, job_id: str) -> Job:
        with self._lock:
            return self._jobs[job_id]

    def submit_reference_answers(self, job_id, submission, reviewer, token_hint='', token_user=''):
        if not isinstance(reviewer,str) or not reviewer.strip() or len(reviewer)>128:
            raise ReferenceInputError('请填写最多128字的操作者显示名；显示名不代表审图签字')
        with self._lock:
            job=self._jobs[job_id]
            snapshot=job.reference_snapshot
            if not isinstance(snapshot,dict) or snapshot.get('version')!=BASIS_VERSION:
                raise ReferenceInputError('当前没有有效的图前参考快照，请刷新或重新提交','stale_reference',409)
            accepted=validate_submission(snapshot,submission)
            try:source_sha=hashlib.sha256(job.source.read_bytes()).hexdigest()
            except OSError:
                raise ReferenceInputError('原始STEP已不可读，请重新提交','stale_reference',409) from None
            if source_sha!=snapshot['source_sha256']:
                raise ReferenceInputError('STEP来源已变化，旧参考不能用于新输入','stale_reference',409)
            key=hashlib.sha256(json.dumps({'submission':accepted,'reviewer':reviewer.strip(),
                'token_user':token_user,'source_sha256':source_sha},sort_keys=True,
                separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
            existing=next((run for run in job.reference_runs if run['key']==key),None)
            if existing is not None:
                return dict(existing)
            if job.state!=WAITING_REFERENCE:
                raise ReferenceInputError('该参考已提交或任务状态已变化，请刷新','reference_conflict',409)
            unknown=any(a['option_id'] is None for a in accepted['answers'])
            state=REFUSED if unknown else QUEUED
            run={'key':key,'submission':accepted,'source_sha256':source_sha,
                 'reviewer':reviewer.strip(),'token_hint':token_hint,'token_user':token_user,
                 'created_at':datetime.now(timezone.utc).isoformat(),'state':state,
                 'error':'工程师无法确认斜孔参考，未生成交付图' if unknown else ''}
            op = self._prepare('reference', job_id+':'+key, job,
                    {'source_path':job.source.relative_to(self._work).as_posix(),
                     'source_sha256':source_sha, 'run':run})
            job=replace(job,state=state,reference_runs=(*job.reference_runs,run),
                        failed=('reference_unconfirmed',) if unknown else (),
                        reasons=(run['error'],) if unknown else (),error='')
            if unknown: self._capture(op, job)
            else: self._jobs[job_id]=job;self._record(job)
            if not unknown:self._pending.put(job_id)
            return dict(run)

    def _reference_result(self, job_id, key, run_state, run_error='', **changes):
        with self._lock:
            job=self._jobs[job_id]
            runs=tuple({**r,'state':run_state,'error':run_error} if r['key']==key else r
                       for r in job.reference_runs)
            job=replace(job,reference_runs=runs,**changes)
            self._jobs[job_id]=job;self._record(job)

    def submit_answers(self, job_id, answers, reviewer, token_hint="", token_user="") -> AnswerRun:
        """在队列锁内校验当前问题、捕获父版并入队；OCCT 仍只在单 worker 中运行。"""
        answers = tuple(answers)
        if not all(isinstance(answer, Answer) for answer in answers):
            raise AnswerError("answers 中的每一项必须是 Answer")
        encoded = json.dumps({"reviewer": reviewer, "token_hint": token_hint,
                              "answers": sorted((asdict(a) for a in answers),
                                                key=lambda a: a["question_id"])},
                             sort_keys=True, ensure_ascii=False).encode()
        request_key = hashlib.sha256(encoded).hexdigest()
        with self._lock:
            job = self._jobs[job_id]
            previous = next((run for run in job.answer_runs if run.key == request_key), None)
            if previous is not None:
                if previous.state in (QUEUED, RUNNING, DRAWN):
                    return previous
                raise AnswerError(previous.error or "这份答案上次未能出图，请查看拒绝原因后重新提交零件",
                                  previous.reason or "answer_failed", 409)
            if job.state != DRAWN:
                raise AnswerError("这张图现在不可回答，请等待出图成功或重新提交 STEP", f"job_{job.state}", 409)
            if job_id in self._audit.frozen():
                raise AnswerError("这张图已有数值级错误记录，请修正并重新出图后再回答", "frozen", 409)
            latest = latest_answers(job)
            if len(versions_of(job)) != (latest.version if latest is not None else 0):
                raise StaleBase("当前生效版本含人工 CAD 修改，请重新出图后再回答，避免覆盖人工修改")
            sources = current_files(job)
            parent = head_of(job) or sources.get("dxf")
            document = sources.get("json")
            if parent is None or not parent.is_file() or document is None or not document.is_file():
                raise AnswerError("缺少当前图纸依据，请重新出图后再回答", "missing_basis", 409)
            accepted, duplicate_notes = validate_answers(job.questions, answers)
            if duplicate_notes:
                raise AnswerError("；".join(duplicate_notes) + "；请只保留首条答案后重新提交", "duplicate_question")
            catalog = {question.id: question for run in job.answer_runs for question in run.questions}
            catalog.update({question.id: question for question in job.questions})
            run = AnswerRun(request_key, reviewer, accepted,
                            tuple(catalog.values()), hashlib.sha256(parent.read_bytes()).hexdigest(),
                            len(versions_of(job)), token_hint, token_user)
            if not accepted:
                return replace(run, state=DRAWN, version=len(versions_of(job)))
            audit = self._audit_answer(job_id, run, persist=False)
            op = self._prepare('answers', job_id+':'+run.key, job,
                    {'source_path':job.source.relative_to(self._work).as_posix(),
                     'source_sha256':job.source_sha256 or hashlib.sha256(job.source.read_bytes()).hexdigest(),
                     'base_sha':run.base_sha,'base_version':run.base_version,'run':asdict(run),'queued_audit':audit})
            self._audit.append_once(op['payload']['queued_audit'], op['operation_id'], -1)
            job = replace(job, state=QUEUED, failed=(), reasons=(), error="",
                          answer_runs=job.answer_runs + (run,))
            self._jobs[job_id] = job
            self._record(job)
        self._pending.put(job_id)
        return run

    def put_version(self, job_id: str, data: bytes, base_sha: str = "", *,
                    answer_run=None, questions=()) -> Version:
        """收下改后的图纸，登记为下一个生效版本。

        验基线、分配版本号、落盘三步都在同一把锁里：分开做的话，两个并发回写会都拿到
        1 号，后写的那个把前一张生效图覆盖掉，而两条审计行都还写着"我已生效"。
        机器底稿永远是第 0 版，不参与覆盖。
        """
        if answer_run is None:
            return self._manual_version(job_id, data, base_sha)[0]
        with self.review_guard():
            job = self._jobs[job_id]
            run_row = {**asdict(answer_run), 'files': [[k,p.relative_to(job.directory).as_posix()] for k,p in answer_run.files]}
            queued_audit = self._audit_answer(job_id,replace(answer_run,state=QUEUED),persist=False)
            op = self._operation(job_id,'answers',job_id+':'+answer_run.key) or self._prepare(
                    'answers',job_id+':'+answer_run.key,job,
                    {'source_path':job.source.relative_to(self._work).as_posix(),
                     'source_sha256':job.source_sha256 or hashlib.sha256(job.source.read_bytes()).hexdigest(),
                     'base_sha':answer_run.base_sha,'base_version':answer_run.base_version,
                     'run':run_row,'queued_audit':queued_audit})
            parent = head_of(job) or dict(job.files).get('dxf')
            if op['stage']=='prepared':
                self._publish_answer(op,answer_run,questions,answer_run.files[0][1].parent)
            elif op['stage']!='complete': self._commit(op)
            number=answer_run.base_version+1
            return Version(number,job.directory/f'amended-{number}.dxf',parent)

    def _manual_version(self, job_id, data, base_sha='', review=None):
        with self.review_guard():
            job = self._jobs[job_id]
            parent = head_of(job) or dict(job.files).get('dxf')
            if parent is None or not parent.is_file(): raise StaleBase('这份任务没有可回写的底稿')
            actual = hashlib.sha256(parent.read_bytes()).hexdigest()
            key = digest({'job': job_id, 'base': base_sha or actual,
                          'data': hashlib.sha256(data).hexdigest(), 'review': review})
            previous = self._journal.lookup('amendment', key)
            if previous:
                op = previous
            else:
                if job.state != DRAWN: raise StaleBase('任务正在出图或不可回写，请等待出图成功再提交')
                if base_sha and actual != base_sha: raise StaleBase('改后图对应旧版本，请重新下载最新的那一版再改')
                self._check_slots(job)
                if reference_submission(job) is not None:
                    from drawform.output.references import require_integrity
                    require_integrity(parent, data)
                op = self._prepare('amendment', key, job,
                        {'source_path':job.source.relative_to(self._work).as_posix(),
                         'source_sha256':job.source_sha256 or hashlib.sha256(job.source.read_bytes()).hexdigest(), 'base_sha':actual,
                         'base_version':len(versions_of(job)), 'parent_path':parent.relative_to(self._work).as_posix(),
                         'data':base64.b64encode(data).decode(), 'review':review})
            if op['stage'] == 'prepared': self._finish_amendment(op)
            elif op['stage'] != 'complete': self._commit(op)
            op = self._journal.get(op['operation_id'])
            number = op['payload']['base_version'] + 1
            version = Version(number, job.directory / f'amended-{number}.dxf',
                              relative_file(self._work,op['payload']['parent_path']))
            rows = [r for r in self._audit.entries_for_job(job_id) if r.get('publication_id') == op['operation_id']]
            return version, rows[0] if rows else None, op['data'].get('amend_diff', {})

    def _check_slots(self, job):
        if any(not p.is_file() or p.is_symlink() for p in versions_of(job)):
            raise StaleBase('历史生效图缺失或为链接，不能重编号或覆盖')
        numbers = [int(k[len(VERSION_PREFIX):]) for k,_ in job.files
                   if k.startswith(VERSION_PREFIX) and k[len(VERSION_PREFIX):].isdigit()]
        disk = [int(p.stem[len(VERSION_PREFIX):]) for p in job.directory.glob('amended-*.dxf')
                if p.stem[len(VERSION_PREFIX):].isdigit()]
        if set(disk)-set(numbers) or set(numbers) != set(range(1,max(numbers,default=0)+1)):
            raise StaleBase('历史版本存在未登记文件或缺号，不能覆盖或猜测重编号')

    def _finish_amendment(self, op):
        job = self._jobs[op['job_id']]; payload = op['payload']
        number = payload['base_version'] + 1
        data = base64.b64decode(payload['data'], validate=True)
        directory = self._attempt(op); staged = directory / 'candidate.dxf'
        write_new(staged, data)
        destination = job.directory / f'amended-{number}.dxf'
        target = replace(job, files=job.files + ((f'amended-{number}', destination),))
        audits = (); diffdata = {}
        if payload.get('review') is not None:
            from drawform.eval.amend_diff import compare
            diff = compare(relative_file(self._work,payload['parent_path']), staged)
            actions = diff.as_dict(); changes = [dict(item) for item in diff.items]
            values = payload['review']
            audit = self._audit.record(job_id=job.id, decision='amended', reviewer=values['reviewer'],
                    reasons=diff.reasons, note=values['note'], draft_sha=payload['base_sha'],
                    effective_sha=hashlib.sha256(data).hexdigest(),diff=actions,changes=changes,
                    token_hint=values['token_hint'],token_user=values['token_user'],version=number,_persist=False)
            audits = (audit,); diffdata = {'actions':actions,'changes':changes,'blocking':diff.blocking}
        self._capture(op, target, ((staged,destination),), audits, {'amend_diff':diffdata})

    def publish_amended(self, job_id, data, base_sha, *, reviewer, note='', token_hint='', token_user=''):
        return self._manual_version(job_id,data,base_sha,{'reviewer':reviewer,'note':note,
                                                        'token_hint':token_hint,'token_user':token_user})

    def publish_review(self, job_id, row, *, key):
        with self.review_guard():
            job = self._jobs[job_id]; parent = head_of(job) or dict(job.files).get('dxf')
            op = self._prepare('review', key, job,
                    {'source_path':job.source.relative_to(self._work).as_posix(), 'source_sha256':job.source_sha256 or hashlib.sha256(job.source.read_bytes()).hexdigest(),
                     'base_sha':hashlib.sha256(parent.read_bytes()).hexdigest(),
                     'base_version':len(versions_of(job)), 'audit':row})
            if op['stage'] == 'prepared': self._capture(op, job, audits=(op['payload']['audit'],))
            elif op['stage'] != 'complete': self._commit(op)
            return next(r for r in self._audit.entries_for_job(job_id) if r.get('publication_id') == op['operation_id'])

    def package_snapshot(self, job_id):
        """Capture bytes and audit under the same lock; later ZIP creation is read-only."""
        with self.review_guard():
            job = self._jobs[job_id]
            if head_of(job) is None: raise StaleBase('没有生效版本，交付包不导出')
            operations = self._journal.export([job_id])
            try: validate_publications(self._work,[job_id])
            except ValueError as error:raise StaleBase(str(error)) from error
            if any(row['stage'] in ('artifacts_committed','audit_committed') for row in operations):
                raise StaleBase('存在尚未完成的发布，请恢复核对后再交付')
            content = {}; entries = []
            def capture(path,kind,edition,role):
                if path.is_symlink() or not path.is_file(): raise StaleBase('交付文件缺失或为链接，历史数据保留')
                name=job.id+'/'+path.relative_to(job.directory).as_posix()
                data=path.read_bytes()
                if name in content and content[name] != data: raise StaleBase('交付文件路径冲突')
                if name in content:return
                content[name]=data
                entries.append({'path':name,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),
                                'kind':kind,'drawing_edition':edition,'role':role})
            capture(job.source,'step',None,'source')
            for kind,path in job.files:
                if kind.startswith('amended-'):
                    edition=int(kind.split('-')[1]);role='effective_dxf'
                elif kind.startswith('answer-'):
                    edition=int(kind.split('-')[1]);role='answer_companion'
                else:edition=0;role='machine_draft'
                capture(path,kind,edition,role)
            sets=[]
            for number in range(len(versions_of(job))+1):
                seal=job.directory/f'drawing-edition-{number}.json'
                if seal.exists():
                    if seal.is_symlink():raise StaleBase('图纸套件封存为链接，禁止交付')
                    try:
                        record=json.loads(seal.read_text())
                        if (record['job_id']!=job.id or record['drawing_edition']!=number
                                or record['source_step_sha256']!=job.source_sha256
                                or set(record['files'])!={'dxf','svg','pdf','json'}):raise ValueError
                        for entry in record['files'].values():
                            path=relative_file(job.directory,entry['path'])
                            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=entry['sha256']:raise ValueError
                    except (ValueError,TypeError,KeyError,OSError):raise StaleBase('图纸套件内容已变化，不能重新计算摘要冒充一致版本') from None
                    capture(seal,'drawing_seal',number,'edition_record')
                sets.append({'drawing_edition':number,'mode':'complete' if seal.exists() else 'legacy_unsealed' if number==0 else 'dxf_only',
                             'companions_current':bool(seal.exists())})
            rows=self._audit.entries_for_job(job_id)
            for name,data,kind in (
                    ('review.jsonl',(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows)).encode(),'audit'),
                    ('legacy-publications.json',json.dumps(operations,ensure_ascii=False,sort_keys=True).encode(),'publication_journal')):
                name=job.id+'/'+name;content[name]=data
                entries.append({'path':name,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),
                                'kind':kind,'drawing_edition':None,'role':kind})
            manifest={'schema_version':1,'job_id':job.id,'source_step_sha256':hashlib.sha256(job.source.read_bytes()).hexdigest(),
                      'effective_edition':len(versions_of(job)),'drawing_sets':sets,'files':entries,
                      'engineering_review':'original_explicit_records_only'}
            content[job.id+'/manifest.json']=json.dumps(manifest,ensure_ascii=False,sort_keys=True,indent=2).encode()
            return content

    def listing(self) -> tuple[Job, ...]:
        """按提交顺序给出当前在册的任务，给页面做历史列表。

        返回快照而不是活的字典，避免调用方遍历时新任务改变字典大小。
        """
        with self._lock:
            return tuple(self._jobs.values())

    def _set(self, job_id: str, **changes) -> None:
        with self._lock:
            if changes.get('state')==DRAWN and 'files' in changes:
                self._seal_drawing(self._jobs[job_id],changes['files'],0)
            self._jobs[job_id] = replace(self._jobs[job_id], **changes)
            self._record(self._jobs[job_id])

    def _seal_drawing(self,job,files,edition):
        """Capture all companions once; later publication may only verify them."""
        selected=dict(files)
        if not {'dxf','svg','pdf','json'}<=selected.keys():return
        values={k:{'path':str(selected[k].relative_to(job.directory)),
                   'sha256':hashlib.sha256(selected[k].read_bytes()).hexdigest()} for k in ('dxf','svg','pdf','json')}
        record={'schema_version':1,'job_id':job.id,'drawing_edition':edition,
                'source_step_sha256':job.source_sha256,'files':values}
        path=job.directory/f'drawing-edition-{edition}.json'
        if path.exists() or path.is_symlink():
            if path.is_symlink() or json.loads(path.read_text())!=record:raise StaleBase('同版图纸套件已改变，禁止覆盖发布依据')
            return
        from drawform.workbench.modeling import save_json
        save_json(path,record)

    def _run(self) -> None:
        while not self._stopping.is_set():
            job_id = self._pending.get()
            if job_id is None:
                return
            try:
                if isinstance(job_id, tuple): self._recover_operation(job_id[1])
                else: self._execute(job_id)
            except Exception as error:
                if isinstance(job_id,str) and job_id in self._jobs:
                    self._set(job_id,state=CRASHED,error=f'{type(error).__name__}: {error}')

    def _seal_artifact(self, job, files, edition, attempt, sources):
        selected = dict(files)
        if not {'dxf','svg','pdf','json'} <= selected.keys(): return ()
        values = {k:{'path':selected[k].relative_to(job.directory).as_posix(),
                     'sha256':hashlib.sha256(staged.read_bytes()).hexdigest()}
                  for k,staged in sources.items() if k in ('dxf','svg','pdf','json')}
        record={'schema_version':1,'job_id':job.id,'drawing_edition':edition,
                'source_step_sha256':job.source_sha256,'files':values}
        staged = attempt / f'drawing-edition-{edition}.json'
        write_new(staged,json.dumps(record,ensure_ascii=False,sort_keys=True).encode())
        return ((staged,job.directory / staged.name),)

    def _publish_initial(self, op, part, reference_key=None):
        job = self.get(op['job_id'])
        final = tuple((kind,job.directory / path.name) for kind,path in part.files)
        pairs = tuple((path,dict(final)[kind]) for kind,path in part.files)
        pairs += self._seal_artifact(job,final,0,part.files[0][1].parent,dict(part.files))
        runs = job.reference_runs
        if reference_key:
            runs = tuple({**r,'state':DRAWN,'error':''} if r['key']==reference_key else r for r in runs)
        target = replace(job,state=DRAWN,files=final,failed=part.verdict.failed,
                         reasons=part.verdict.reasons,questions=part.questions,error='',reference_runs=runs)
        return self._capture(op,target,pairs)

    def _execute(self, job_id: str) -> None:
        # 恢复等待项与新提交可能各入队一次；在锁内认领有效操作，终态的旧项直接跳过。
        with self._lock:
            old=self._jobs[job_id]
            pending=next((run for run in old.answer_runs if run.state==QUEUED),None)
            if pending is None:
                if old.state not in (QUEUED,WAITING_REFERENCE):
                    return
                rechecking=old.state==WAITING_REFERENCE
                reference_run=next((run for run in old.reference_runs if run['state']==QUEUED),None)
                job=replace(old,state=RUNNING)
                self._jobs[job_id]=job;self._record(job)
        if pending is not None:
            self._execute_answers(job_id, pending)
            return
        job = self.get(job_id)
        op = self._operation(job_id,'reference',job_id+':'+reference_run['key']) if reference_run else self._operation(job_id,'submission')
        if op and op['stage']=='complete': op = None
        try:
            if job.source.is_symlink() or hashlib.sha256(job.source.read_bytes()).hexdigest()!=job.source_sha256:
                raise ReferenceInputError('出图来源STEP已变化，请保留记录并重新提交',
                                          'stale_reference' if rechecking else 'stale_source',409)
            if rechecking and hashlib.sha256(job.source.read_bytes()).hexdigest()!=job.reference_snapshot['source_sha256']:
                raise ReferenceInputError('等待中的STEP已变化，请重新提交','stale_reference',409)
            arguments={'stem':job.id,'with_questions':True}
            if job.sheet != 'A3':arguments['sheet']=job.sheet
            if reference_run is not None:
                self._reference_result(job_id,reference_run['key'],RUNNING)
                arguments['reference_in']=reference_run['submission']
            destination = self._attempt(op) if op else job.directory
            part = self._render(job.source, destination, **arguments)
            if job.source.is_symlink() or hashlib.sha256(job.source.read_bytes()).hexdigest()!=job.source_sha256:
                raise ReferenceInputError('出图期间来源STEP已变化，结果不发布','stale_source',409)
            if op:
                self._publish_initial(op,part,reference_run['key'] if reference_run else None)
                return
            self._seal_drawing(job,part.files,0)
        except ReferenceRequired as waiting:
            changes=dict(state=WAITING_REFERENCE,files=(),questions=(),error='',
                         failed=waiting.failed,reasons=waiting.verdict.reasons,
                         reference_snapshot=waiting.snapshot)
            if op:
                # WAITING is externally actionable: capture and publish it under
                # one gate before a confirmation can enqueue the next operation.
                with self.review_guard():
                    current=self.get(job_id)
                    if reference_run is not None:
                        runs=tuple({**r,'state':REFUSED,'error':'参考依据已重新枚举，请重新确认'}
                                   if r['key']==reference_run['key'] else r for r in current.reference_runs)
                        current=replace(current,reference_runs=runs)
                    self._capture(op,replace(current,**changes))
                return
            if reference_run is not None:
                self._reference_result(job_id,reference_run['key'],REFUSED,
                                       '参考依据已重新枚举，请重新确认',**changes)
            else:self._set(job_id,**changes)
        except ReferenceInputError as error:
            changes=dict(state=REFUSED,failed=(error.reason,),reasons=(str(error),),error='')
            if reference_run is not None:
                self._reference_result(job_id,reference_run['key'],REFUSED,str(error),**changes)
            else:self._set(job_id,**changes)
        except DeliveryBlocked as refused:
            changes=dict(state=REFUSED,failed=refused.failed,reasons=refused.verdict.reasons)
            if reference_run is not None:
                self._reference_result(job_id,reference_run['key'],REFUSED,str(refused),**changes)
            else:self._set(job_id,**changes)
        except Exception as error:
            if op and self._journal.get(op['operation_id'])['stage']=='complete': return
            message=f'{type(error).__name__}: {error}'
            if op: self._journal.interrupted(op,error)
            if reference_run is not None:
                self._reference_result(job_id,reference_run['key'],CRASHED,message,
                                       state=CRASHED,error=message)
            # 兜底不是偷懒：worker 里漏出一个异常，队列就永久停在"排队中"，
            # 而接口看起来完全正常。
            else:self._set(job_id,state=CRASHED,error=message)
        else:
            changes=dict(state=DRAWN,files=part.files,failed=part.verdict.failed,
                         reasons=part.verdict.reasons,questions=part.questions)
            if reference_run is not None:
                self._reference_result(job_id,reference_run['key'],DRAWN,**changes)
            else:self._set(job_id,**changes)
        if op and self.get(job_id).state==REFUSED:
            self._capture(op,self.get(job_id))

    def _set_answer_run(self, job_id, run, **changes):
        with self._lock:
            job = replace(_with_run(self._jobs[job_id], run), **changes)
            self._jobs[job_id] = job
            self._record(job)

    def _audit_answer(self, job_id, run, effective_sha="", *, persist=True):
        return self._audit.record_answers(job_id=job_id, request_key=run.key, reviewer=run.reviewer,
                                   answers=run.answers, state=run.state,
                                   draft_sha=run.base_sha, base_version=run.base_version,
                                   version=run.version, effective_sha=effective_sha,
                                   diff=dict(run.diff), token_hint=run.token_hint,
                                   token_user=run.token_user, error=run.error, _persist=persist)

    def _execute_answers(self, job_id, run):
        run = replace(run, state=RUNNING)
        self._set_answer_run(job_id, run, state=RUNNING)
        job = self.get(job_id)
        op = self._operation(job_id,'answers',job_id+':'+run.key)
        try:
            if job.source.is_symlink() or hashlib.sha256(job.source.read_bytes()).hexdigest()!=job.source_sha256:
                raise ReferenceInputError('重出图来源STEP已变化','stale_source',409)
            directory = self._attempt(op) if op else job.directory / f"{VERSION_PREFIX}{run.base_version + 1}"
            if not op: directory.mkdir()
            else:
                # Reserve the existing visible edition directory, but render only
                # in a fresh attempt. No prior artifact is opened for writing.
                (job.directory / f'{VERSION_PREFIX}{run.base_version+1}').mkdir(exist_ok=True)
            (directory / "request.json").write_text(json.dumps({
                "reviewer": run.reviewer, "answers": [asdict(answer) for answer in run.answers],
                "base_sha": run.base_sha, "request_key": run.key}, ensure_ascii=False), encoding="utf-8")
            arguments = dict(stem=job.id,with_questions=True,
                             questions_in=answers_of(job)+run.answers,questions_ref=run.questions)
            if job.sheet != 'A3':arguments['sheet']=job.sheet
            reference = reference_submission(job)
            if reference is not None:arguments['reference_in']=reference
            part = self._render(job.source, directory, **arguments)
            if job.source.is_symlink() or hashlib.sha256(job.source.read_bytes()).hexdigest()!=job.source_sha256:
                raise ReferenceInputError('重出图期间来源STEP已变化','stale_source',409)
            files = dict(part.files)
            changes = drawing_changes(current_files(job)["svg"], files["svg"])
            document = json.loads(files["json"].read_text(encoding="utf-8"))
            document["annotations"] = drawing_annotations(current_files(job)["svg"], files["svg"])
            document["annotation_base_version"] = run.base_version
            files["json"].write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
            result = replace(run, files=part.files, diff=tuple(changes.items()))
            if op:
                self._publish_answer(op,result,part.questions,directory)
                return
            self.put_version(job_id, files["dxf"].read_bytes(), run.base_sha,
                             answer_run=result, questions=part.questions)
        except DeliveryBlocked as error:
            failed = replace(run, state=REFUSED, error=str(error), reason="refused")
            self._answer_failure(op,job_id,failed,failed_codes=error.failed,reasons=error.verdict.reasons)
        except (StaleBase, AnswerError, ReferenceInputError) as error:
            reason = "stale_base" if isinstance(error, StaleBase) else error.reason
            failed = replace(run, state=REFUSED, error=str(error), reason=reason)
            if op and self._journal.get(op['operation_id'])['stage'] != 'prepared':
                self._journal.interrupted(op,error)
                self._set_answer_run(job_id,replace(failed,state=CRASHED),state=CRASHED,error=str(error))
            else: self._answer_failure(op,job_id,failed,failed_codes=(reason,),reasons=(str(error),),error=str(error))
        except Exception as error:
            if op and self._journal.get(op['operation_id'])['stage']=='complete': return
            message = f"{type(error).__name__}: {error}"
            failed = replace(run, state=CRASHED, error=message, reason="answer_failed")
            if op: self._journal.interrupted(op,error)
            self._audit_answer(job_id, failed)
            self._set_answer_run(job_id, failed,
                                 state=CRASHED, error=message)

    def _answer_failure(self,op,job_id,run,*,failed_codes=(),reasons=(),error=''):
        if op:
            job = replace(_with_run(self.get(job_id),run),state=REFUSED,
                          failed=tuple(failed_codes),reasons=tuple(reasons),error=error)
            audit = self._audit_answer(job_id,run,persist=False)
            self._capture(op,job,audits=(audit,))
        else:
            self._audit_answer(job_id,run)
            self._set_answer_run(job_id,run,state=REFUSED,failed=failed_codes,reasons=reasons,error=error)

    def _publish_answer(self,op,run,questions,directory):
        with self.review_guard():
            job = self.get(op['job_id'])
            if not any(r.key==run.key and r.state==RUNNING for r in job.answer_runs):
                raise StaleBase('答案对应任务已变化')
            parent = head_of(job) or dict(job.files).get('dxf')
            if len(versions_of(job))!=run.base_version or hashlib.sha256(parent.read_bytes()).hexdigest()!=run.base_sha:
                raise StaleBase('当前生效版本已变化，答案不能覆盖')
            if job.id in self._audit.frozen(): raise AnswerError('数值级错误冻结，答案未生效','frozen',409)
            self._check_slots(job)
            number = run.base_version+1
            final = tuple((kind,job.directory / f'amended-{number}' / path.name) for kind,path in run.files)
            pairs = tuple((path,dict(final)[kind]) for kind,path in run.files)
            root_dxf = job.directory / f'amended-{number}.dxf'
            pairs += ((dict(run.files)['dxf'],root_dxf),
                      (directory/'request.json',job.directory/f'amended-{number}'/'request.json'))
            companions = tuple((k,p) for k,p in final if k!='dxf')
            completed = replace(run,state=DRAWN,version=number,files=(('dxf',root_dxf),)+companions)
            target = replace(_with_run(job,completed),state=DRAWN,questions=tuple(questions),error='',
                    files=job.files+((f'amended-{number}',root_dxf),)+tuple((f'answer-{number}-{k}',p) for k,p in companions))
            pairs += self._seal_artifact(job,completed.files,number,directory,dict(run.files))
            audit = self._audit_answer(job.id,completed,hashlib.sha256(dict(run.files)['dxf'].read_bytes()).hexdigest(),persist=False)
            return self._capture(op,target,pairs,(audit,))


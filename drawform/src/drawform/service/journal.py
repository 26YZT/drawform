"""Durable legacy publications. Intents and stage events are never overwritten.

The journal records an operation, not an engineering approval. A captured audit
row can only be replayed from the exact original request that produced it.
"""
from __future__ import annotations

import hashlib
import base64
import json
import os
from pathlib import Path
import re
import threading

DIRECTORY = 'legacy-publications'
STAGES = ('prepared', 'captured', 'artifacts_committed', 'audit_committed', 'complete')
KINDS = {'submission', 'reference', 'answers', 'amendment', 'review'}
_SHA = re.compile(r'[0-9a-f]{64}')


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)


def write_new(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as handle:
        handle.write(data); handle.flush(); os.fsync(handle.fileno())
    sync_directory(path.parent)
    sync_directory(path.parent.parent)


def _json(path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result: raise ValueError('发布日志含重复JSON字段')
            result[key] = value
        return result
    path = Path(path)
    if path.is_symlink() or not path.is_file(): raise ValueError('发布日志文件缺失或为链接')
    try:
        return json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=pairs,
                          parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError('发布日志JSON损坏') from error


def relative_file(work, name):
    if (not isinstance(name, str) or not name or '\\' in name or '\0' in name
            or Path(name).is_absolute() or any(p in ('', '.', '..') for p in name.split('/'))):
        raise ValueError('发布日志文件路径越界')
    path = Path(work) / name
    for parent in (path, *path.parents):
        if parent == Path(work): break
        if parent.is_symlink(): raise ValueError('发布日志路径不能为链接')
    return path


class Journal:
    def __init__(self, work):
        self.work = Path(work).resolve()
        self.root = self.work / DIRECTORY
        self._lock = threading.RLock()

    def get(self, identity):
        if not isinstance(identity, str) or not _SHA.fullmatch(identity):
            raise ValueError('发布操作编号无效')
        directory = self.root / identity
        if directory.is_symlink(): raise ValueError('发布操作目录不能为链接')
        intent = _json(directory / 'intent.json')
        if (not isinstance(intent, dict) or intent.get('schema_version') != 1
                or intent.get('operation_id') != identity or intent.get('kind') not in KINDS
                or not isinstance(intent.get('request_key'),str) or not intent['request_key']
                or not isinstance(intent.get('job_id'),str) or not intent['job_id']
                or not isinstance(intent.get('payload'), dict)
                or intent.get('payload_sha256') != digest(intent['payload'])):
            raise ValueError('发布意图字段或摘要不一致')
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',intent['job_id']):
            raise ValueError('发布任务编号无效')
        if digest({'kind':intent['kind'],'key':intent['request_key']}) != identity:
            raise ValueError('发布意图与操作编号不一致')
        eventdir = directory / 'events'
        events = []
        if eventdir.exists():
            if eventdir.is_symlink() or not eventdir.is_dir(): raise ValueError('发布事件目录无效')
            files = sorted(eventdir.iterdir())
            for number, path in enumerate(files, 1):
                if path.name != f'{number:06d}.json': raise ValueError('发布事件序号缺失或含未知文件')
                event = _json(path)
                if (not isinstance(event, dict) or event.get('operation_id') != identity
                        or event.get('sequence') != number or event.get('stage') not in STAGES
                        or not isinstance(event.get('data'), dict)
                        or event.get('status') not in ('pending', 'interrupted', 'complete')):
                    raise ValueError('发布事件字段无效')
                expected = 0 if not events else STAGES.index(events[-1]['stage'])
                actual = STAGES.index(event['stage'])
                if actual not in (expected, expected + 1): raise ValueError('发布事件阶段跳跃或回退')
                if actual == 4 and event['status'] != 'complete': raise ValueError('完成事件状态无效')
                events.append(event)
        return {**intent, 'stage': events[-1]['stage'] if events else 'prepared',
                'status': events[-1]['status'] if events else 'pending',
                'data': events[-1]['data'] if events else {}, 'events': events}

    def prepare(self, kind, key, job_id, payload):
        if kind not in KINDS or not isinstance(key, str) or not key:
            raise ValueError('发布类型或请求编号无效')
        identity = digest({'kind': kind, 'key': key})
        with self._lock:
            directory = self.root / identity
            path = directory / 'intent.json'
            if path.exists() or path.is_symlink():
                old = self.get(identity)
                if old['payload_sha256'] != digest(payload): raise ValueError('发布请求编号对应不同输入')
                if not old['events']: self.advance(identity, 'prepared')
                return self.get(identity)
            directory.mkdir(parents=True, exist_ok=False)
            intent = {'schema_version': 1, 'operation_id': identity, 'kind': kind,
                      'job_id': job_id, 'request_key': key, 'payload': payload,
                      'payload_sha256': digest(payload)}
            write_new(path, json.dumps(intent, ensure_ascii=False, sort_keys=True,
                                      allow_nan=False).encode())
            sync_directory(directory.parent)
            sync_directory(self.work)
            self.advance(identity, 'prepared')
            return self.get(identity)

    def lookup(self, kind, key):
        identity = digest({'kind': kind, 'key': key})
        if not (self.root / identity / 'intent.json').exists(): return None
        return self.get(identity)

    def advance(self, operation, stage, data=None):
        identity = operation['operation_id'] if isinstance(operation, dict) else operation
        with self._lock:
            row = self.get(identity)
            current = STAGES.index(row['stage'])
            target = STAGES.index(stage)
            if row['events'] and target <= current and row['status'] != 'interrupted': return row
            if target not in (current, current + 1): raise ValueError('发布阶段不连续')
            merged = {**row['data'], **(data or {})}
            event = {'operation_id': identity, 'sequence': len(row['events']) + 1,
                     'stage': stage, 'status': 'complete' if stage == 'complete' else 'pending',
                     'data': merged}
            write_new(self.root / identity / 'events' / f"{event['sequence']:06d}.json",
                      json.dumps(event, ensure_ascii=False, sort_keys=True, allow_nan=False).encode())
            return self.get(identity)

    def interrupted(self, operation, error):
        identity = operation['operation_id'] if isinstance(operation, dict) else operation
        with self._lock:
            row = self.get(identity)
            if row['stage'] == 'complete': return
            event = {'operation_id': identity, 'sequence': len(row['events']) + 1,
                     'stage': row['stage'], 'status': 'interrupted',
                     'data': {**row['data'], 'last_error': str(error)}}
            write_new(self.root / identity / 'events' / f"{event['sequence']:06d}.json",
                      json.dumps(event, ensure_ascii=False, sort_keys=True).encode())

    def export(self, job_ids=None):
        if not self.root.exists(): return []
        if self.root.is_symlink() or not self.root.is_dir(): raise ValueError('发布日志根目录无效')
        selected = None if job_ids is None else set(job_ids)
        rows = []
        for directory in sorted(self.root.iterdir()):
            row = self.get(directory.name)
            if selected is None or row['job_id'] in selected: rows.append(row)
        return rows


def export(work, job_ids=None):
    return Journal(work).export(job_ids)


def validate(work, job_ids=None):
    """Read-only integrity gate for backup/restore. Never repairs or opens a worker."""
    work = Path(work).resolve()
    rows = Journal(work).export(job_ids)
    jobs = {}
    if (work/'jobs.jsonl').is_file():
        for line in (work/'jobs.jsonl').read_text(encoding='utf-8').splitlines():
            try: item=json.loads(line)
            except json.JSONDecodeError: continue  # The workspace validator reports damaged legacy lines.
            if isinstance(item,dict) and isinstance(item.get('job'),str):jobs[item['job']]=item
    for row in rows:
        payload, data = row['payload'], row['data']
        if not _SHA.fullmatch(str(payload.get('source_sha256',''))):
            raise ValueError('发布来源摘要无效')
        if 'source_bytes' in payload:
            try: source_bytes=base64.b64decode(payload['source_bytes'],validate=True)
            except (ValueError,TypeError):raise ValueError('发布原始来源内容无效') from None
            if hashlib.sha256(source_bytes).hexdigest()!=payload['source_sha256']:
                raise ValueError('发布原始来源与摘要不一致')
        source = payload.get('source_path')
        if source:
            path = relative_file(work, source)
            if path.parent != work/row['job_id']:raise ValueError('发布来源不属于目标任务')
            if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() != payload.get('source_sha256'):
                raise ValueError('发布来源STEP摘要失配')
            if row['stage'] == 'complete' and not path.is_file(): raise ValueError('已完成发布来源缺失')
        if row['stage'] != 'prepared':
            target=data.get('job_row')
            if (not isinstance(target,dict) or target.get('job')!=row['job_id']
                    or source!=row['job_id']+'/'+str(target.get('source',''))
                    or not isinstance(target.get('files'),list)):
                raise ValueError('发布目标任务快照失配')
            for kind,name in target['files']:
                relative_file(work,row['job_id']+'/'+name)
            if row['stage']=='complete':
                job=jobs.get(row['job_id'])
                if job is None or job.get('source')!=target['source']:
                    raise ValueError('已完成发布缺少任务索引')
                slots=dict(job.get('files',[]))
                if any(slots.get(k)!=name for k,name in target['files']):
                    raise ValueError('已完成发布与任务产物槽位不一致')
        destinations=set()
        for entry in data.get('artifacts', []):
            if not isinstance(entry, dict) or not _SHA.fullmatch(str(entry.get('sha256', ''))):
                raise ValueError('发布产物记录无效')
            staged = relative_file(work, entry.get('staged'))
            destination = relative_file(work, entry.get('path'))
            if (not entry['staged'].startswith(DIRECTORY+'/'+row['operation_id']+'/attempts/')
                    or not entry['path'].startswith(row['job_id']+'/') or entry['path'] in destinations):
                raise ValueError('发布产物越过操作或任务边界，或目标重复')
            destinations.add(entry['path'])
            if not staged.is_file() or hashlib.sha256(staged.read_bytes()).hexdigest() != entry['sha256']:
                raise ValueError('发布捕获产物缺失或摘要失配')
            if row['stage'] in STAGES[2:] and (not destination.is_file()
                    or hashlib.sha256(destination.read_bytes()).hexdigest() != entry['sha256']):
                raise ValueError('已发布产物缺失或摘要失配')
        if row['stage'] in STAGES[3:]:
            actual = []
            audit = work / 'reviews.jsonl'
            if audit.is_file():
                for line in audit.read_text(encoding='utf-8').splitlines():
                    try: actual.append(json.loads(line))
                    except json.JSONDecodeError: raise ValueError('发布审计日志损坏') from None
            for number, expected in enumerate(data.get('audits', [])):
                matches = [item for item in actual if item.get('publication_id') == row['operation_id']
                           and item.get('publication_entry') == number]
                if len(matches) != 1 or {k: v for k, v in matches[0].items()
                        if k not in ('publication_id', 'publication_entry')} != expected:
                    raise ValueError('发布审计缺失、重复或内容失配')
    return {'operations': len(rows), 'complete': sum(r['stage'] == 'complete' for r in rows),
            'pending': sum(r['stage'] != 'complete' for r in rows)}

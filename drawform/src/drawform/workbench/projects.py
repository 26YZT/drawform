"""Explicit project HEAD and durable, immutable delivery publications.

Lock order: modeling → queue → audit. A candidate/adoption never moves HEAD.
"""
from __future__ import annotations
import io
import json
from pathlib import Path
import re
import sqlite3
import stat
import uuid
import zipfile
from datetime import datetime, timezone
from .modeling import Conflict, digest, save_json
from .plate_spec import SpecError
from .revisions import canonical_sha
from .project_package import json_bytes, sha, verify_snapshot, MAX_BYTES, MAX_FILES


def _write(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as handle:
        handle.write(raw);handle.flush()
        import os
        os.fsync(handle.fileno())
    _sync(path.parent)


def _sync(path):
    import os
    fd = os.open(path, os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)


def _tree(directory):
    if directory.is_symlink() or not directory.is_dir(): raise Conflict('项目历史目录缺失或为链接。')
    result = []
    for path in sorted(directory.rglob('*')):
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode) or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise Conflict('项目历史含链接或特殊文件，不能交付。')
        if path.is_file(): result.append(path)
    return result


def _job_rows(work):
    rows = {}
    path = work / 'jobs.jsonl'
    if not path.exists(): return rows
    for line in path.read_text().splitlines():
        if not line.strip(): continue
        try: row = json.loads(line)
        except ValueError: raise Conflict('任务索引含损坏行，不能形成项目快照。')
        if not isinstance(row, dict) or not re.fullmatch('[0-9a-f]{12}', str(row.get('job', ''))):
            raise Conflict('任务索引身份无效。')
        rows[row['job']] = row
    return rows


class Projects:
    def __init__(self, service):
        self.s = service
        with service.connect() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS projects(
root_id TEXT PRIMARY KEY REFERENCES sessions(id),owner TEXT NOT NULL,
head_revision INTEGER NOT NULL DEFAULT 0,effective_json TEXT);
CREATE TABLE IF NOT EXISTS project_publications(
id TEXT PRIMARY KEY,root_id TEXT NOT NULL REFERENCES projects(root_id),owner TEXT NOT NULL,
request_key TEXT NOT NULL,payload_sha TEXT NOT NULL,state TEXT NOT NULL,data TEXT NOT NULL,
UNIQUE(owner,root_id,request_key));''')
            db.execute("UPDATE project_publications SET state='interrupted' WHERE state IN ('prepared','captured')")
            db.commit()

    def _checkpoint(self, stage):
        """Fault injection seam; no production actions."""

    def root(self, sid, owner):
        self.s.raw(sid, owner)
        link = self.s.revisions.link(sid, owner)
        return link['root_id'] if link else sid

    def ensure(self, root, owner):
        if self.root(root, owner) != root: raise KeyError(root)
        with self.s.connect() as db:
            if db.execute('SELECT 1 FROM projects WHERE root_id=? AND owner=?',(root,owner)).fetchone() is None:
                db.execute('INSERT OR IGNORE INTO projects(root_id,owner) VALUES(?,?)', (root, owner));db.commit()

    def snapshot(self, root, identity):
        self.s.directory(root)
        if not re.fullmatch('[0-9a-f]{32}', str(identity)): raise Conflict('快照身份无效。')
        return self.s.root / 'projects' / root / 'snapshots' / identity

    def _get(self, root, owner):
        if self.root(root, owner)!=root:raise KeyError(root)
        with self.s.connect() as db:
            row = db.execute('SELECT * FROM projects WHERE root_id=? AND owner=?', (root, owner)).fetchone()
        if row is None:
            return {'project_id':root,'root_session_id':root,'head_revision':0,'effective':None,
                    'integrity':{'status':'empty'},'human_review':'not_reviewed'}
        effective = json.loads(row['effective_json']) if row['effective_json'] else None
        integrity = {'status': 'empty'}
        if effective:
            try:
                verify_snapshot(self.snapshot(root, effective['snapshot_id']), effective['manifest_sha256'])
                integrity = {'status': 'verified'}
            except (OSError, ValueError, KeyError, TypeError) as error:
                integrity = {'status': 'unavailable', 'error': str(error)}
        return {'project_id': root, 'root_session_id': root, 'head_revision': row['head_revision'],
                'effective': effective, 'integrity': integrity, 'human_review': 'not_reviewed'}

    def get(self, root, owner):
        with self.s.lock: return self._get(root, owner)

    def listing(self, owner):
        with self.s.lock, self.s.connect() as db:
            roots = db.execute('SELECT id FROM sessions WHERE owner=? AND id NOT IN (SELECT child_id FROM model_revisions) ORDER BY rowid DESC', (owner,)).fetchall()
            return {'projects': [self._get(row['id'], owner) for row in roots]}

    def publications(self, root, owner):
        if self.root(root,owner)!=root:raise KeyError(root)
        with self.s.connect() as db:
            rows = db.execute('SELECT id,state,data FROM project_publications WHERE root_id=? AND owner=? ORDER BY rowid', (root, owner)).fetchall()
        return {'project_id': root, 'publications': [{'id': r['id'], 'state': r['state'], **json.loads(r['data'])} for r in rows]}

    def _family(self, root, owner):
        with self.s.connect() as db:
            links = [dict(r) for r in db.execute('SELECT * FROM model_revisions WHERE root_id=? AND owner=? ORDER BY number', (root, owner))]
            ids = [root] + [r['child_id'] for r in links]
            sessions = [dict(db.execute('SELECT * FROM sessions WHERE id=? AND owner=?', (sid, owner)).fetchone()) for sid in ids]
            bundles = sorted({json.loads(r['data'])['bundle_id'] for r in sessions})
            inputs = [dict(db.execute('SELECT * FROM inputs WHERE id=? AND owner=?', (bid, owner)).fetchone()) for bid in bundles]
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            extra = {}
            for table in ('drawing_inputs', 'clarification_receipts','model_attempts'):
                if table not in tables: continue
                # Select only rows belonging to this model family, without leaking another seat.
                rows = [dict(r) for r in db.execute('SELECT * FROM ' + table)]
                extra[table] = [r for r in rows if (r.get('bundle_id', r.get('id')) in bundles
                    or r.get('session_id', r.get('sid',r.get('session'))) in ids)]
        return {'root_session_id': root, 'sessions': sessions, 'inputs': inputs,
                'model_revisions': links, **extra}

    def _capture(self, root, owner, selected, revision, snapshot_id):
        from drawform.service.jobs import current_files, head_of, versions_of, latest_answers
        s = self.s;work = s.root.parent
        records = self._family(root, owner)
        paths = {};jobs = [];index = _job_rows(work)
        if any(r['state'] in ('queued','building','analyzing','cancel_requested') for r in records['sessions']):
            raise Conflict('同项目仍有模型正在生成，请完成或保留中断记录后再发布一致快照。')
        from .recovery import _validate_revisions, _validate_clarifications
        decoded={r['id']:{**r,'decoded':{**json.loads(r['data']),'state':r['state']}} for r in records['sessions']}
        try:
            _validate_revisions(s.root, records['model_revisions'], decoded)
            _validate_clarifications(s.root, records.get('clarification_receipts',[]), decoded)
        except ValueError as error:raise Conflict(str(error))
        def add(path):
            path = Path(path)
            if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(work.resolve()):
                raise Conflict('项目关联文件缺失或路径越界。')
            # Also reject a symbolic parent rather than following it into the project.
            if any(p.is_symlink() for p in (path, *path.parents) if p != work.parent):
                raise Conflict('项目关联路径含链接。')
            name = 'payload/' + path.relative_to(work).as_posix()
            paths[name] = path
            return name
        for row in records['inputs']:
            bid = row['id'];directory = s.root/'inputs'/bid
            for path in _tree(directory) if directory.exists() else [s.root/'inputs'/(bid+'.json')]: add(path)
        for row in records['sessions']:
            data = json.loads(row['data']);data['state'] = row['state']
            s.verify_input_source(data, owner)
            if data.get('step_sha256'):
                s.check_artifacts(data, owner)
                from .recovery import _validate_model
                try: _validate_model(s.root, data, owner)
                except ValueError as error: raise Conflict(str(error))
            directory = s.directory(row['id'])
            if directory.exists():
                for path in _tree(directory): add(path)
            if not data.get('job_id'): continue
            job = s.jobs.get(data['job_id']);jobrow = index.get(job.id)
            if job.state in ('queued','running','waiting_reference'):
                raise Conflict('同项目仍有工程图操作未完成，不能发布部分历史。')
            if (not jobrow or job.source_sha256 != data['step_sha256'] or digest(job.source) != data['step_sha256']):
                raise Conflict('模型STEP与历史出图来源不一致。')
            from .recovery import _validate_drawing_seals
            try:_validate_drawing_seals(work,jobrow,data['step_sha256'])
            except ValueError as error:raise Conflict(str(error))
            binding_path=s.directory(row['id'])/'drawing-binding.json'
            if binding_path.is_symlink() or not binding_path.is_file():raise Conflict('模型出图绑定记录缺失。')
            binding=json.loads(binding_path.read_text())
            if any(binding.get(k)!=data.get(k) for k in ('session_id','job_id','step_sha256','text_sha256','parameters')):
                raise Conflict('模型与出图绑定记录不一致。')
            # The raw append-only index retains missing slots filtered by legacy _load.
            for item in [jobrow, *jobrow.get('answer_runs', [])]:
                for _, name in item.get('files', []):
                    add(job.directory / name)
            for path in _tree(job.directory): add(path)
            jobs.append(jobrow)
        job = s.jobs.get(selected['job_id'])
        if job.state != 'drawn': raise Conflict('工程图尚未完整生成，不能设为项目有效版本。')
        selected_files = current_files(job)
        head = head_of(job)
        if head is not None and selected_files.get('dxf') != head:
            raise Conflict('当前人工DXF没有同版PDF/SVG/JSON，请先完成一致出图；旧图纸仍保留。')
        if not {'dxf', 'svg', 'pdf', 'json'} <= selected_files.keys():
            raise Conflict('当前图纸缺少完整DXF/SVG/PDF/JSON。')
        answer = latest_answers(job)
        edition = answer.version if answer else 0
        if head is not None and edition != len(versions_of(job)):
            raise Conflict('当前图纸套件与人工有效图版次不一致。')
        seal_path=job.directory/f'drawing-edition-{edition}.json'
        if seal_path.exists():
            seal=json.loads(seal_path.read_text())
            expected={k:{'path':str(selected_files[k].relative_to(job.directory)),'sha256':digest(selected_files[k])} for k in ('dxf','svg','pdf','json')}
            if seal_path.is_symlink() or seal.get('source_step_sha256')!=selected['step_sha256'] or seal.get('files')!=expected:
                raise Conflict('已发布图纸套件摘要或来源失配，不能交付。')
        model = s.revisions.decorate(dict(selected), owner)['model_version']['number']
        effective = {'session_id': selected['session_id'], 'job_id': job.id,
                     'step_sha256': selected['step_sha256'], 'model_version': model,
                     'snapshot_id': snapshot_id, 'head_revision': revision}
        from .paths import model_relative
        selected_names = {'step': add(s.directory(selected['session_id'])/model_relative(selected)/selected.get('step_filename','plate.step')),
                          'job_source': add(job.source)}
        selected_names.update({kind: add(selected_files[kind]) for kind in ('dxf','svg','pdf','json')})
        records['jobs'] = jobs
        from drawform.service.journal import export as export_publications
        records['legacy_publications']=export_publications(work,[j['job'] for j in jobs])
        for operation in records['legacy_publications']:
            for path in _tree(work/'legacy-publications'/operation['operation_id']):add(path)
        audit = [r for j in jobs for r in s.jobs._audit.entries_for_job(j['job'])]
        records['review_entries'] = audit
        raws = {'payload/project.json': json_bytes(records)}
        session_meta={row['id']:json.loads(row['data']) for row in records['sessions']}
        numbers={root:1,**{row['child_id']:row['number'] for row in records['model_revisions']}}
        job_meta={d['job_id']:(sid,d) for sid,d in session_meta.items() if d.get('job_id')}
        slots={}
        for j in jobs:
            for kind,name in j.get('files',[]):
                version=0
                match=re.fullmatch(r'amended-([1-9][0-9]*)',kind)
                answer_match=re.fullmatch(r'answer-([1-9][0-9]*)-(.+)',kind)
                if match:version=int(match[1]);kind='manual_dxf'
                if answer_match:version=int(answer_match[1]);kind=answer_match[2]
                slots['payload/'+j['job']+'/'+name]={'drawing_edition':version,'role':kind}
            for answer_row in j.get('answer_runs',[]):
                for kind,name in answer_row.get('files',[]):
                    slots['payload/'+j['job']+'/'+name]={'drawing_edition':answer_row.get('version') or None,'role':kind}
        captured = {}
        total = len(raws['payload/project.json'])
        for name, path in paths.items():
            size = path.stat().st_size;total += size
            if size > MAX_BYTES or total > MAX_BYTES or len(paths) > MAX_FILES: raise Conflict('项目历史超出单包交付范围。')
            raw = path.read_bytes()
            raws[name] = raw;captured[name] = sha(raw)
        directory = self.snapshot(root, snapshot_id)
        directory.mkdir(parents=True, exist_ok=False)
        entries = []
        for name, raw in sorted(raws.items()):
            _write(directory/name, raw)
            entry = {'path': name, 'bytes': len(raw), 'sha256': sha(raw), 'role': 'history',
                     'project_id':root,'session_id':None,'model_version':None,'job_id':None,'drawing_edition':None}
            parts=name.split('/')
            if len(parts)>4 and parts[1:3]==['workbench','sessions']:
                identity=parts[3];d=session_meta[identity]
                entry.update(session_id=identity,model_version=numbers[identity],job_id=d.get('job_id'),
                             source_step_sha256=d.get('step_sha256'),source_input_sha256=d['source_sha256'])
            elif len(parts)>2 and parts[1] in job_meta:
                identity,d=job_meta[parts[1]]
                entry.update(session_id=identity,model_version=numbers[identity],job_id=parts[1],
                             source_step_sha256=d.get('step_sha256'),source_input_sha256=d['source_sha256'])
                entry.update(slots.get(name,{'role':'drawing_evidence'}))
            elif parts[1:3]==['workbench','inputs']:entry['role']='original_input'
            elif name=='payload/project.json':entry['role']='project_records'
            if name in selected_names.values():
                entry.update(effective, source_step_sha256=effective['step_sha256'], drawing_edition=edition,
                             role=next(k for k,v in selected_names.items() if v==name))
            entries.append(entry)
        manifest = {'format': 'drawform-project-v1', 'project_id': root, 'snapshot_id': snapshot_id,
                    'head_revision': revision, 'effective': effective, 'drawing_edition': edition,
                    'selected_artifacts': selected_names, 'files': entries, 'human_review': 'not_reviewed',
                    'created_at': datetime.now(timezone.utc).isoformat()}
        _write(directory/'manifest.json', json_bytes(manifest))
        _sync(directory);_sync(directory.parent)
        for name, path in paths.items():
            if digest(path) != captured[name]: raise Conflict('交付期间来源文件发生变化，快照未采用。')
        verify_snapshot(directory)
        return effective | {'manifest_sha256': digest(directory/'manifest.json')}

    def publish(self, root, owner, body, key):
        if not isinstance(key, str) or not 1 <= len(key) <= 128: raise SpecError('请提供项目发布请求编号。')
        if set(body) != {'session_id', 'expected_head_revision', 'expected_step_sha256'} or type(body['expected_head_revision']) is not int or body['expected_head_revision'] < 0:
            raise SpecError('项目发布须绑定模型与当前有效版本。')
        payload = canonical_sha(body);s = self.s
        with s.lock, s.jobs.review_guard():
            self.ensure(root, owner)
            with s.connect() as db:
                old = db.execute('SELECT * FROM project_publications WHERE root_id=? AND owner=? AND request_key=?', (root, owner, key)).fetchone()
                if old:
                    if old['payload_sha'] != payload: raise Conflict('同一发布请求对应不同内容。')
                    if old['state'] == 'complete': return json.loads(old['data'])['result']
                current = self._get(root, owner)
                if current['integrity']['status']=='unavailable':raise Conflict('原有效快照损坏，请先核对备份；不能跳过历史发布。')
                if body['expected_head_revision'] != current['head_revision']: raise Conflict('项目有效版本已变化，请刷新比较后重新确认。')
                sid = body['session_id']
                if self.root(sid, owner) != root: raise Conflict('模型不属于该项目。')
                selected = s.raw(sid, owner)
                if selected['state'] != 'adopted' or selected.get('step_sha256') != body['expected_step_sha256'] or not selected.get('job_id'):
                    raise Conflict('请先采用模型并完成同一STEP的工程图。')
                s.check_artifacts(selected, owner)
                opid = old['id'] if old else uuid.uuid4().hex
                data = {'request': body, 'created_at': datetime.now(timezone.utc).isoformat()}
                if old: db.execute("UPDATE project_publications SET state='prepared',data=? WHERE id=?", (json.dumps(data,ensure_ascii=False),opid))
                else: db.execute('INSERT INTO project_publications VALUES(?,?,?,?,?,?,?)', (opid,root,owner,key,payload,'prepared',json.dumps(data,ensure_ascii=False)))
                db.commit()
            committed = False
            try:
                self._checkpoint('prepared')
                effective = self._capture(root, owner, selected, current['head_revision']+1, uuid.uuid4().hex)
                data['effective'] = effective
                with s.connect() as db:
                    db.execute("UPDATE project_publications SET state='captured',data=? WHERE id=?", (json.dumps(data,ensure_ascii=False),opid));db.commit()
                self._checkpoint('captured')
                result = {'project_id':root,'root_session_id':root,'head_revision':effective['head_revision'],
                          'effective':effective,'integrity':{'status':'verified'},'human_review':'not_reviewed'}
                data['result'] = result
                with s.connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    changed = db.execute('UPDATE projects SET head_revision=?,effective_json=? WHERE root_id=? AND owner=? AND head_revision=?',
                        (effective['head_revision'],json.dumps(effective,ensure_ascii=False),root,owner,current['head_revision'])).rowcount
                    if changed != 1: raise Conflict('项目有效版本已变化，已保存快照未采用。')
                    db.execute("UPDATE project_publications SET state='complete',data=? WHERE id=?", (json.dumps(data,ensure_ascii=False),opid));db.commit()
                committed = True
                self._checkpoint('committed')
                return result
            except BaseException as error:
                if not committed and isinstance(error, Exception):
                    data['error'] = str(error)
                    with s.connect() as db:
                        db.execute("UPDATE project_publications SET state='failed',data=? WHERE id=?", (json.dumps(data,ensure_ascii=False),opid));db.commit()
                if isinstance(error,ValueError) and not isinstance(error,(Conflict,SpecError)):raise Conflict(str(error)) from error
                raise

    def package(self, root, owner, revision=None):
        with self.s.lock, self.s.jobs.review_guard():
            current = self._get(root, owner)
            revision = current['head_revision'] if revision is None else revision
            if type(revision) is not int or revision < 1: raise Conflict('项目还没有有效交付版本。')
            with self.s.connect() as db:
                rows = db.execute("SELECT data FROM project_publications WHERE root_id=? AND owner=? AND state='complete'", (root,owner)).fetchall()
            effective = next((json.loads(r['data'])['effective'] for r in rows if json.loads(r['data'])['effective']['head_revision']==revision), None)
            if effective is None: raise KeyError(revision)
            directory = self.snapshot(root,effective['snapshot_id'])
            try: verify_snapshot(directory,effective['manifest_sha256'])
            except (ValueError,OSError,KeyError) as error: raise Conflict('有效交付快照无法验证：'+str(error))
            result = io.BytesIO()
            with zipfile.ZipFile(result,'w',zipfile.ZIP_DEFLATED) as archive:
                for path in sorted(directory.rglob('*')):
                    if path.is_file():
                        info=zipfile.ZipInfo(path.relative_to(directory).as_posix(),(1980,1,1,0,0,0))
                        info.compress_type=zipfile.ZIP_DEFLATED;info.external_attr=(stat.S_IFREG|0o600)<<16
                        archive.writestr(info,path.read_bytes())
            from .project_package import verify_package
            result.seek(0);verify_package(result);result.seek(0)
            return result


def validate_project_store(work):
    """Offline semantic gate used by maintenance; never instantiate workers."""
    work = Path(work);dbpath = work/'workbench/workspace.sqlite'
    if not dbpath.is_file(): return {'projects':0,'publications':0}
    with sqlite3.connect(dbpath.resolve().as_uri()+'?mode=ro&immutable=1',uri=True) as db:
        db.row_factory = sqlite3.Row
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'projects' not in tables: return {'projects':0,'publications':0}
        projects = db.execute('SELECT * FROM projects').fetchall()
        publications = db.execute('SELECT * FROM project_publications').fetchall()
        complete = {}
        owners = {r['root_id']:r['owner'] for r in projects}
        for row in publications:
            data = json.loads(row['data'])
            if owners.get(row['root_id'])!=row['owner']:raise ValueError('项目发布所有权不一致。')
            if row['state'] not in ('prepared','captured','complete','failed','interrupted'): raise ValueError('项目发布阶段无效。')
            if canonical_sha(data['request']) != row['payload_sha']: raise ValueError('项目发布回执与请求不一致。')
            if row['state'] != 'complete': continue
            e = data['effective'];root = row['root_id']
            session=db.execute('SELECT * FROM sessions WHERE id=?',(e['session_id'],)).fetchone()
            if not session or session['owner']!=row['owner'] or session['state']!='adopted':raise ValueError('有效模型身份或所有权不一致。')
            live=json.loads(session['data'])
            link=db.execute('SELECT * FROM model_revisions WHERE child_id=?',(session['id'],)).fetchone()
            live_root=link['root_id'] if link else session['id']
            number=link['number'] if link else 1
            if live_root!=root or e['model_version']!=number or live.get('step_sha256')!=e['step_sha256'] or live.get('job_id')!=e['job_id']:
                raise ValueError('有效模型与项目版本或出图绑定不一致。')
            if not re.fullmatch('[0-9a-f]{32}',str(e.get('snapshot_id'))): raise ValueError('快照身份无效。')
            path = work/'workbench/projects'/root/'snapshots'/e['snapshot_id']
            checked = verify_snapshot(path,e['manifest_sha256'])
            if checked['project_id']!=root:raise ValueError('项目快照所属项目不一致。')
            inside = {k:v for k,v in e.items() if k != 'manifest_sha256'}
            if checked['effective'] != inside or data['result']['effective'] != e: raise ValueError('发布回执与快照身份不一致。')
            key = (root,e['head_revision'])
            if key in complete: raise ValueError('项目有效版本重复。')
            complete[key] = e
        for row in projects:
            root = row['root_id'];session = db.execute('SELECT owner FROM sessions WHERE id=?',(root,)).fetchone()
            if not session or session['owner'] != row['owner']: raise ValueError('项目所有权与模型不一致。')
            if db.execute('SELECT 1 FROM model_revisions WHERE child_id=?',(root,)).fetchone():raise ValueError('项目根不能是模型子版本。')
            revision = row['head_revision'];e = json.loads(row['effective_json']) if row['effective_json'] else None
            if type(revision) is not int or revision < 0: raise ValueError('项目有效版本号无效。')
            if revision == 0:
                if e is not None: raise ValueError('初始项目不应有有效版本。')
            elif complete.get((root,revision)) != e: raise ValueError('项目HEAD与已提交发布回执不一致。')
            if {n for r,n in complete if r==root} != set(range(1,revision+1)):
                raise ValueError('项目有效版本历史缺失或超前。')
        return {'projects':len(projects),'publications':len(publications),'effective_versions':len(complete)}

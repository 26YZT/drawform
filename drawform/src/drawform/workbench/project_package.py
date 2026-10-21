"""Read-only verification of a self-contained project delivery snapshot."""
from __future__ import annotations
import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import stat
import unicodedata
import zipfile

MAX_FILES = 10000
MAX_BYTES = 512 * 1024 * 1024


def load_json(raw):
    def unique(pairs):
        value={}
        for k,v in pairs:
            if k in value:raise ValueError('JSON包含重复字段。')
            value[k]=v
        return value
    def finite(value):raise ValueError('JSON包含非有限数值。')
    return json.loads(raw,object_pairs_hook=unique,parse_constant=finite)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False).encode()


def safe_name(name):
    if (not isinstance(name, str) or not name or '\\' in name or ':' in name
            or '\x00' in name or PurePosixPath(name).is_absolute()
            or any(p in ('', '.', '..') for p in name.split('/'))):
        raise ValueError('交付路径无效或越界。')
    return name


def _verify(manifest, read, names):
    if (not isinstance(manifest, dict) or manifest.get('format') != 'drawform-project-v1'
            or not isinstance(manifest.get('files'), list) or not manifest['files']
            or len(manifest['files']) > MAX_FILES):
        raise ValueError('项目交付清单无效。')
    entries = {}
    folded = set()
    total = 0
    for entry in manifest['files']:
        name = safe_name(entry.get('path'))
        fold = unicodedata.normalize('NFC', name).casefold()
        if fold in folded or name == 'manifest.json':
            raise ValueError('交付清单有重复或歧义路径。')
        folded.add(fold)
        size = entry.get('bytes')
        if (type(size) is not int or size < 0 or size > MAX_BYTES
                or not re.fullmatch('[0-9a-f]{64}', str(entry.get('sha256', '')))):
            raise ValueError('交付文件大小或摘要无效。')
        total += size
        if total > MAX_BYTES:
            raise ValueError('交付文件总大小超出限制。')
        raw = read(name)
        if len(raw) != size or sha(raw) != entry['sha256']:
            raise ValueError('交付文件校验失败：' + name)
        entries[name] = entry
    if names != set(entries) | {'manifest.json'}:
        raise ValueError('交付文件集合与清单不一致。')
    effective = manifest.get('effective', {})
    selected = manifest.get('selected_artifacts', {})
    if (set(selected) != {'step', 'job_source', 'dxf', 'svg', 'pdf', 'json'}
            or type(manifest.get('head_revision')) is not int or manifest['head_revision'] < 1
            or effective.get('head_revision') != manifest['head_revision']
            or effective.get('snapshot_id') != manifest.get('snapshot_id')):
        raise ValueError('项目有效版本或文件绑定不完整。')
    for kind, name in selected.items():
        if name not in entries:
            raise ValueError('有效版本缺少文件：' + kind)
        entry = entries[name]
        if (entry.get('session_id') != effective.get('session_id')
                or entry.get('job_id') != effective.get('job_id')
                or entry.get('model_version') != effective.get('model_version')
                or entry.get('source_step_sha256') != effective.get('step_sha256')
                or entry.get('drawing_edition') != manifest.get('drawing_edition')):
            raise ValueError('文件版本或来源STEP不一致：' + kind)
    if any(entries[selected[k]]['sha256'] != effective.get('step_sha256') for k in ('step', 'job_source')):
        raise ValueError('模型STEP与出图来源不一致。')
    records = load_json(read('payload/project.json'))
    _verify_relations(records,entries,read)
    if records.get('root_session_id') != manifest.get('project_id'):
        raise ValueError('项目记录与交付清单不一致。')
    sessions = {r['id']: r for r in records.get('sessions', [])}
    selected_session = sessions.get(effective.get('session_id'))
    if selected_session is None:
        raise ValueError('有效模型记录缺失。')
    data = load_json(selected_session['data'])
    if (data.get('step_sha256') != effective['step_sha256']
            or data.get('job_id') != effective['job_id'] or selected_session['state'] != 'adopted'):
        raise ValueError('模型记录与有效版本绑定不一致。')
    jobs = {r['job']: r for r in records.get('jobs', [])}
    job = jobs.get(effective['job_id'])
    if (not job or job.get('state') != 'drawn'
            or job.get('source_sha256') != effective['step_sha256']):
        raise ValueError('出图记录与STEP来源不一致。')
    edition=manifest.get('drawing_edition')
    if type(edition) is not int or edition<0:raise ValueError('图纸版次无效。')
    if edition:
        runs=[r for r in job.get('answer_runs',[]) if r.get('state')=='drawn' and r.get('version')==edition]
        if len(runs)!=1:raise ValueError('同版图纸套件记录缺失或重复。')
        slots=dict(runs[0]['files'])
    else:slots=dict(job.get('files',[]))
    for kind in ('dxf','svg','pdf','json'):
        expected='payload/'+effective['job_id']+'/'+safe_name(slots.get(kind))
        if selected[kind]!=expected:raise ValueError('图纸文件不属于当前同版套件：'+kind)
    from .paths import model_relative
    expected_step='payload/workbench/sessions/'+effective['session_id']+'/'+model_relative(data)+'/'+safe_name(data.get('step_filename','plate.step'))
    if selected['step']!=expected_step or selected['job_source']!='payload/'+effective['job_id']+'/'+safe_name(job.get('source')):
        raise ValueError('有效STEP文件槽与模型/Job记录不一致。')
    if manifest.get('human_review') != 'not_reviewed':
        raise ValueError('交付包不能自行升级人工工程验收。')
    return {'verified': True, 'project_id': manifest['project_id'],
            'head_revision': manifest['head_revision'], 'snapshot_id': manifest['snapshot_id'],
            'files': len(entries), 'bytes': total, 'effective': effective}


def _verify_relations(records,entries,read):
    """Hashes of the package cannot replace the saved model/source dependencies."""
    from .revisions import canonical_sha, normalized_spec, apply_changes
    from .paths import model_relative,manifest_relative,analysis_relative
    def unique(rows,key):
        result={}
        for row in rows:
            identity=row[key]
            if identity in result:raise ValueError('项目记录身份重复。')
            result[identity]=row
        return result
    def require(name,expected=None):
        safe_name(name)
        if name not in entries:raise ValueError('项目依赖缺失：'+name)
        if expected and entries[name]['sha256']!=expected:raise ValueError('项目依赖摘要与权威记录不一致：'+name)
        return read(name)
    def document(name):return load_json(require(name))
    inputs=unique(records.get('inputs',[]),'id')
    drawings=unique(records.get('drawing_inputs',[]),'id')
    sessions=unique(records.get('sessions',[]),'id')
    jobs=unique(records.get('jobs',[]),'job')
    for bid,inp in inputs.items():
        if bid in drawings:
            d=drawings[bid]
            require('payload/workbench/inputs/'+bid+'/source.'+safe_name(d['format']),inp['sha'])
            meta=document('payload/workbench/inputs/'+bid+'/input.json')
            if meta.get('source_sha256')!=inp['sha'] or meta.get('filename')!=d['filename'] or meta.get('format')!=d['format']:
                raise ValueError('原图输入记录不一致。')
        else:
            meta=document('payload/workbench/inputs/'+bid+'.json')
            if sha(inp['text'].encode())!=inp['sha'] or meta.get('text')!=inp['text'] or meta.get('text_sha256')!=inp['sha'] or meta.get('units')!='mm':
                raise ValueError('原始文字记录不一致。')
    decoded={}
    for sid,row in sessions.items():
        d=load_json(row['data']);decoded[sid]=d
        inp=inputs.get(d.get('bundle_id'))
        if not inp or row['owner']!=inp['owner'] or d.get('session_id')!=sid or d.get('text_sha256')!=inp['sha'] or d.get('source_sha256')!=inp['sha'] or d.get('text')!=inp['text']:
            raise ValueError('模型来源或所有权不一致。')
        base='payload/workbench/sessions/'+sid+'/'
        if d.get('source_preview_sha256'):
            preview_sid=d.get('source_preview_session_id',sid)
            if preview_sid not in sessions or sessions[preview_sid]['owner']!=row['owner']:raise ValueError('原图预览来源缺失。')
            if d.get('analysis_attempt_id')!=load_json(sessions[preview_sid]['data']).get('analysis_attempt_id'):raise ValueError('原图预览尝试版本不一致。')
            require('payload/workbench/sessions/'+preview_sid+'/'+analysis_relative(d)+'/preview.png',d['source_preview_sha256'])
        if d.get('step_sha256'):
            normalized_spec(d)
            names={'step':d.get('step_filename','plate.step'),'preview':'preview.json','validation':'validation.json','drawform_validation':'drawform-validation.json'}
            hashes=d.get('artifact_hashes',{})
            if hashes.get('step')!=d['step_sha256']:raise ValueError('模型STEP摘要记录不一致。')
            for kind,name in names.items():
                raw=require(base+model_relative(d)+'/'+safe_name(name),hashes.get(kind))
                if not hashes.get(kind):raise ValueError('模型产物摘要记录缺失。')
                if kind!='step':
                    checked=load_json(raw)
                    if checked.get('step_sha256')!=d['step_sha256'] or kind!='preview' and checked.get('passed') is not True:
                        raise ValueError('模型检查/预览指向另一份STEP。')
            model_manifest=document(base+manifest_relative(d))
            expected={'parameters':d['parameters'],'text_sha256':d['text_sha256'],'artifacts':hashes,
                      'spec':d.get('spec'),'input_kind':d.get('input_kind','text'),'source_sha256':d.get('source_sha256'),
                      'revision_provenance':d.get('revision_provenance'),'human_review':'not_reviewed'}
            if any(model_manifest.get(k)!=v for k,v in expected.items()):raise ValueError('模型manifest与版本记录不一致。')
            if row['state']=='adopted':
                adoption=document(base+'adoption.json')
                if adoption.get('candidate_id')!=sid or adoption.get('step_sha256')!=d['step_sha256'] or adoption.get('parameters')!=d['parameters'] or adoption.get('adopted_by_seat')!=row['owner'][:8]:
                    raise ValueError('模型采用回执不一致。')
        if d.get('job_id'):
            job=jobs.get(d['job_id'])
            if not job or job.get('source_sha256')!=d.get('step_sha256'):raise ValueError('模型缺少关联出图记录。')
            require('payload/'+job['job']+'/'+safe_name(job['source']),d['step_sha256'])
            binding=document(base+'drawing-binding.json')
            if any(binding.get(k)!=d.get(k) for k in ('session_id','job_id','step_sha256','text_sha256','parameters')):raise ValueError('模型出图绑定回执不一致。')
    links=unique(records.get('model_revisions',[]),'child_id')
    numbers=set()
    for sid,link in links.items():
        parent=decoded.get(link['parent_id']);child=decoded.get(sid);root=decoded.get(link['root_id'])
        if not parent or not child or not root or link['root_id'] in links:raise ValueError('模型祖先或根版本缺失。')
        if any(sessions[x]['owner']!=link['owner'] for x in (sid,link['parent_id'],link['root_id'])):raise ValueError('模型版本链所有权不一致。')
        if (link['root_id'],link['number']) in numbers:raise ValueError('模型版本号重复。')
        numbers.add((link['root_id'],link['number']))
        request=load_json(link['request_json']);changes=load_json(link['changes_json'])
        spec,diff=apply_changes(normalized_spec(parent),request['changes'])
        if (parent.get('step_sha256')!=link['parent_step_sha'] or canonical_sha(normalized_spec(parent))!=link['parent_spec_sha']
                or canonical_sha(normalized_spec(child))!=link['spec_sha'] or spec!=normalized_spec(child) or diff!=changes
                or canonical_sha(request)!=link['payload_sha']):raise ValueError('模型父子规格或修改请求不一致。')
        revision=document('payload/workbench/sessions/'+sid+'/revision.json')
        if revision.get('request')!=request or revision.get('provenance')!=child.get('revision_provenance'):raise ValueError('模型修改回执不一致。')
        current=sid;seen=set()
        for _ in range(65):
            if current not in links:
                if current!=link['root_id']:raise ValueError('版本链未到达项目根。')
                break
            if current in seen:raise ValueError('模型版本链循环。')
            seen.add(current);current=links[current]['parent_id']
        else:raise ValueError('模型版本链超过64层。')
    for job in jobs.values():
        for record in [job,*job.get('answer_runs',[])]:
            for _,name in record.get('files',[]):require('payload/'+job['job']+'/'+safe_name(name))
    for receipt in records.get('clarification_receipts',[]):
        sid=receipt['session']
        if sid not in sessions or sessions[sid]['owner']!=receipt['owner']:raise ValueError('澄清回执所属模型不一致。')
        record=document('payload/workbench/sessions/'+sid+'/clarifications/'+receipt['payload_sha']+'.json')
        actual=sha(json.dumps(record.get('request'),sort_keys=True,ensure_ascii=False,allow_nan=False).encode())
        if actual!=receipt['payload_sha']:raise ValueError('澄清请求摘要不一致。')
    attempts=unique(records.get('model_attempts',[]),'id')
    for identity,row in attempts.items():
        sid=row['session_id'];record=load_json(row['data'])
        if sid not in sessions or row['owner']!=sessions[sid]['owner']:raise ValueError('执行尝试所属模型不一致。')
        from .attempts import intent
        frozen=intent(decoded[sid],row['kind'])
        if record.get('frozen')!=frozen or canonical_sha(frozen)!=row['basis_sha']:raise ValueError('执行尝试冻结依据不一致。')
        base='payload/workbench/sessions/'+sid+'/attempts/'+identity+'/'
        stored=document(base+'intent.json')
        for key in ('number','directory','frozen','runtime_sha256','created_at'):
            if stored.get(key)!=record.get(key):raise ValueError('执行尝试意图文件不一致。')
        for group in ('files','retained_files'):
            for name,expected in record.get(group,{}).items():require(base+safe_name(name),expected)
        if row['state']=='complete':
            pointer='model_attempt_id' if row['kind']=='build' else 'analysis_attempt_id'
            if decoded[sid].get(pointer)!=identity:raise ValueError('执行尝试提交指针不一致。')
    for operation in records.get('legacy_publications',[]):
        from drawform.service.journal import digest as journal_digest,STAGES
        if operation['job_id'] not in jobs:raise ValueError('旧发布日志缺少关联任务。')
        identity=operation['operation_id'];base='payload/legacy-publications/'+identity+'/'
        original=document(base+'intent.json')
        if (original.get('payload_sha256')!=journal_digest(operation['payload'])
                or any(original.get(k)!=operation.get(k) for k in ('operation_id','kind','job_id','request_key','payload'))):
            raise ValueError('旧发布意图与交付记录不一致。')
        for number,event in enumerate(operation.get('events',[]),1):
            if document(base+'events/'+f'{number:06d}.json')!=event:raise ValueError('旧发布阶段证据不一致。')
        if operation.get('stage') not in STAGES:raise ValueError('旧发布阶段无效。')
        for artifact in operation.get('data',{}).get('artifacts',[]):
            require('payload/'+safe_name(artifact['staged']),artifact['sha256'])
            if operation['stage'] in ('artifacts_committed','audit_committed','complete'):
                require('payload/'+safe_name(artifact['path']),artifact['sha256'])


def verify_snapshot(directory, expected_manifest_sha=None):
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError('项目快照目录缺失或为链接。')
    actual = set()
    for path in directory.rglob('*'):
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode) or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise ValueError('项目快照包含链接或特殊文件。')
        if path.is_file(): actual.add(path.relative_to(directory).as_posix())
    def read(name):
        path = directory / safe_name(name)
        if path.stat().st_size > MAX_BYTES: raise ValueError('快照文件过大。')
        return path.read_bytes()
    raw = read('manifest.json')
    if expected_manifest_sha and sha(raw) != expected_manifest_sha:
        raise ValueError('项目清单与已提交有效版本摘要不一致。')
    return _verify(load_json(raw), read, actual)


def verify_package(path):
    """Never extract; reject ambiguous names and oversized data before reading."""
    if isinstance(path, bytes): path = io.BytesIO(path)
    with zipfile.ZipFile(path) as archive:
        items = archive.infolist()
        if len(items) > MAX_FILES + 1: raise ValueError('交付条目过多。')
        names = set(); folded = set(); total = 0
        for info in items:
            name = safe_name(info.filename)
            fold = unicodedata.normalize('NFC', name).casefold()
            mode = info.external_attr >> 16
            if (name in names or fold in folded or info.is_dir() or info.flag_bits & 1
                    or (stat.S_IFMT(mode) and not stat.S_ISREG(mode))):
                raise ValueError('交付包包含重复、加密、链接或特殊条目。')
            total += info.file_size
            if info.file_size > MAX_BYTES or total > MAX_BYTES or info.file_size > max(info.compress_size, 1) * 1000:
                raise ValueError('交付包大小或压缩比超出限制。')
            names.add(name);folded.add(fold)
        if 'manifest.json' not in names: raise ValueError('缺少交付清单。')
        return _verify(load_json(archive.read('manifest.json')), archive.read, names)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('package', type=Path)
    args = parser.parse_args(argv)
    try: result = verify_package(args.package)
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        print(json.dumps({'verified': False, 'error': str(error)}, ensure_ascii=False));return 2
    print(json.dumps(result, ensure_ascii=False, indent=2));return 0


if __name__ == '__main__': raise SystemExit(main())

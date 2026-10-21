"""Immutable model variants. Version allocation and request receipts share one transaction."""
from __future__ import annotations
import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from .plate_spec import SpecError
from .simple_spec import validate_spec, finite
from .modeling import Conflict, digest

LABELS={'diameter_1_mm':'第1段直径','length_1_mm':'第1段长度','diameter_2_mm':'第2段直径','length_2_mm':'第2段长度','length_mm':'长度','width_mm':'宽度','thickness_mm':'厚度','outer_diameter_mm':'外径','inner_diameter_mm':'内径','x_mm':'X位置','y_mm':'Y位置','diameter_mm':'直径'}


def canonical_sha(value):
    try:
        return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()
    except (TypeError,ValueError):
        raise SpecError('修改内容必须为有限JSON数据。')


def normalized_spec(data):
    if data.get('spec'):
        result=validate_spec(data['spec'])
        if data.get('parameters')!=result['parameters'] or data.get('holes',result['holes'])!=result['holes']:
            raise Conflict('模型规格与保存参数不一致。')
        return result
    p=data.get('parameters',{})
    if set(p)!={'length_mm','width_mm','thickness_mm','hole_diameter_mm'}:
        raise SpecError('模型还没有完整的可修改规格。')
    return validate_spec({'schema_version':2,'kind':'plate','parameters':{k:p[k] for k in ('length_mm','width_mm','thickness_mm')},'holes':[{'x_mm':0.,'y_mm':0.,'diameter_mm':p['hole_diameter_mm']}]})


def apply_changes(spec,operations):
    if not isinstance(operations,list) or not 1<=len(operations)<=20:
        raise SpecError('请填写至少一项且最多20项明确修改。')
    result=json.loads(json.dumps(spec));seen=set();differences=[]
    def assign(target,key,value,path,label,hole_id=None):
        if path in seen:raise SpecError('同一字段不能重复修改。')
        seen.add(path);value=finite(value,label);old=target[key]
        if value==old:raise SpecError('修改值与原值相同，请只提交实际变化。')
        target[key]=value
        diff={'path':path,'label':label,'before':old,'after':value,'unit':'mm'}
        if hole_id:diff['hole_id']=hole_id
        differences.append(diff)
    for op in operations:
        if not isinstance(op,dict):raise SpecError('每项修改必须为明确字段对象。')
        if op.get('op')=='set_parameter':
            if set(op)!={'op','parameter','value_mm'} or not isinstance(op['parameter'],str) or op['parameter'] not in result['parameters']:
                raise SpecError('参数修改包含未知字段。')
            key=op['parameter'];assign(result['parameters'],key,op['value_mm'],'parameters.'+key,LABELS[key])
        elif op.get('op')=='set_hole':
            if set(op)-{'op','hole_id','x_mm','y_mm','diameter_mm'} or not set(op)&{'x_mm','y_mm','diameter_mm'}:
                raise SpecError('孔修改只接受既有孔的XY和直径。')
            identity=op.get('hole_id');match=re.fullmatch(r'hole_([1-9]|1[0-6])',identity) if isinstance(identity,str) else None
            if not match or int(match[1])>len(result['holes']):raise SpecError('孔编号不属于所选父模型。')
            if ('hole',identity) in seen:raise SpecError('同一个孔的修改请合并为一项。')
            seen.add(('hole',identity));target=result['holes'][int(match[1])-1]
            for key in ('x_mm','y_mm','diameter_mm'):
                if key in op:assign(target,key,op[key],f'holes.{identity}.{key}',f'孔{match[1]} {LABELS[key]}',identity)
        else:raise SpecError('仅允许修改已有参数或已有孔，不接受代码、路径或表达式。')
    return validate_spec(result),differences


class ModelRevisions:
    def __init__(self,service):
        self.s=service
        with service.connect() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS model_revisions(
 child_id TEXT PRIMARY KEY REFERENCES sessions(id),owner TEXT NOT NULL,
 root_id TEXT NOT NULL REFERENCES sessions(id),parent_id TEXT NOT NULL REFERENCES sessions(id),
 number INTEGER NOT NULL,request_key TEXT NOT NULL,payload_sha TEXT NOT NULL,
 parent_step_sha TEXT NOT NULL,parent_spec_sha TEXT NOT NULL,spec_sha TEXT NOT NULL,
 changes_json TEXT NOT NULL,request_json TEXT NOT NULL,created_at TEXT NOT NULL,
 UNIQUE(owner,parent_id,request_key),UNIQUE(root_id,number));''');db.commit()

    def link(self,sid,owner,db=None):
        if db is not None:return db.execute('SELECT * FROM model_revisions WHERE child_id=? AND owner=?',(sid,owner)).fetchone()
        with self.s.connect() as conn:return self.link(sid,owner,conn)

    def latest(self,root,owner,db):
        return db.execute('SELECT COALESCE(MAX(number),1) FROM model_revisions WHERE root_id=? AND owner=?',(root,owner)).fetchone()[0]

    def decorate(self,data,owner):
        with self.s.connect() as db:
            link=self.link(data['session_id'],owner,db);root=link['root_id'] if link else data['session_id'];latest=self.latest(root,owner,db)
        try:spec=normalized_spec(data)
        except (SpecError,Conflict):spec=None
        data['editable_spec']=spec
        data['model_version']={'number':link['number'] if link else 1,'latest_number':latest,'root_session_id':root,
          'parent_session_id':link['parent_id'] if link else None,'parent_step_sha256':link['parent_step_sha'] if link else None,
          'parent_spec_sha256':link['parent_spec_sha'] if link else None,'spec_sha256':canonical_sha(spec) if spec else None,
          'changes':json.loads(link['changes_json']) if link else [],'mode':'design_variant' if link else 'original'}
        return data

    def verify_lineage(self,data,owner):
        """Read only, bounded ancestor verification, never promote another version."""
        current=data;seen=set()
        for _ in range(65):
            link=self.link(current['session_id'],owner)
            if link is None:return
            if current['session_id'] in seen:raise Conflict('模型版本来源出现循环。')
            seen.add(current['session_id'])
            if canonical_sha(normalized_spec(current))!=link['spec_sha']:raise Conflict('修改版规格已改变。')
            try:parent=self.s.raw(link['parent_id'],owner)
            except KeyError:raise Conflict('父模型记录缺失，不能沿用修改依据。')
            if parent['state'] not in ('awaiting_adoption','adopted') or parent.get('step_sha256')!=link['parent_step_sha'] or canonical_sha(normalized_spec(parent))!=link['parent_spec_sha']:
                raise Conflict('父模型版本依据缺失或已改变。')
            self.s.check_local_artifacts(parent,owner)
            from .paths import model_relative
            path=self.s.directory(parent['session_id'])/model_relative(parent)/parent.get('step_filename','plate.step')
            if path.is_symlink() or not path.is_file() or digest(path)!=link['parent_step_sha']:
                raise Conflict('父模型STEP缺失或已改变，不能沿用修改依据。')
            current=parent
        raise Conflict('模型版本来源超过64层。')

    def create(self,parent_id,owner,body,key):
        if not isinstance(key,str) or not 1<=len(key)<=128:raise SpecError('请提供有效的修改请求编号。')
        if set(body)!={'expected_step_sha256','expected_spec_sha256','expected_revision','changes'}:
            raise SpecError('修改请求须绑定父模型、规格及版本依据。')
        if type(body['expected_revision']) is not int or body['expected_revision']<1:raise SpecError('模型版本依据必须为正整数。')
        payload=canonical_sha(body);s=self.s
        with s.lock,s.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            parent=s.raw(parent_id,owner)
            receipt=db.execute('SELECT child_id,payload_sha FROM model_revisions WHERE owner=? AND parent_id=? AND request_key=?',(owner,parent_id,key)).fetchone()
            if receipt:
                if receipt['payload_sha']!=payload:raise Conflict('同一修改请求编号对应了不同内容。')
                return s.get(receipt['child_id'],owner)
            if s.stopped.is_set():raise Conflict('服务正在停止，请稍后重试。')
            if parent['state'] not in ('awaiting_adoption','adopted'):raise Conflict('请先等待父模型完成。')
            s.check_artifacts(parent,owner)
            base=normalized_spec(parent);link=self.link(parent_id,owner,db);root=link['root_id'] if link else parent_id
            latest=self.latest(root,owner,db)
            if body['expected_revision']!=latest or body['expected_step_sha256']!=parent.get('step_sha256') or body['expected_spec_sha256']!=canonical_sha(base):
                raise Conflict('父模型或模型族版本依据已改变，请刷新比较后重新提交。')
            depth=0;ancestor=link
            while ancestor:
                depth+=1
                if depth>=64:raise SpecError('本轮模型修改链最多64层，请新建模型。')
                ancestor=self.link(ancestor['parent_id'],owner,db)
            spec,diff=apply_changes(base,body['changes']);sid=uuid.uuid4().hex;number=latest+1;now=datetime.now(timezone.utc).isoformat()
            fields=('bundle_id','text_sha256','text','source_sha256','input_kind','filename','sheet','analysis','source_preview_sha256','analysis_attempt_id')
            data={k:parent[k] for k in fields if k in parent}
            data.update(session_id=sid,candidate_id=sid,created_at=now,parameters=spec['parameters'],holes=spec['holes'],spec=spec,model_kind=spec['kind'],job_id=None,error='',backend='local_simple_v2',human_review='not_reviewed')
            if data.get('input_kind')=='drawing':
                data['source_preview_session_id']=parent.get('source_preview_session_id',parent_id)
                data['source_preview_url']=f'/api/workbench/v1/modeling/sessions/{sid}/source-preview'
            provenance={'root_session_id':root,'parent_session_id':parent_id,'number':number,'parent_step_sha256':parent['step_sha256'],'parent_spec_sha256':canonical_sha(base),'spec_sha256':canonical_sha(spec),'changes':diff,'mode':'design_variant','human_review':'not_reviewed'}
            data['revision_provenance']=provenance
            if s.auto_resume:
                from .attempts import bind_intent
                bind_intent(data,'build')
            s.write_json(s.directory(sid)/'revision.json',{'request':body,'provenance':provenance,'created_at':now})
            db.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?)',(sid,owner,'revision:'+sid,payload,'queued',json.dumps(data,ensure_ascii=False)))
            db.execute('INSERT INTO model_revisions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(sid,owner,root,parent_id,number,key,payload,parent['step_sha256'],canonical_sha(base),canonical_sha(spec),json.dumps(diff,ensure_ascii=False),json.dumps(body,ensure_ascii=False),now));db.commit()
        s.pending.put((sid,owner))
        return s.get(sid,owner)

    def history(self,sid,owner):
        data=self.s.raw(sid,owner)
        with self.s.connect() as db:
            link=self.link(sid,owner,db);root=link['root_id'] if link else sid
            self.s.raw(root,owner)
            links=db.execute('SELECT * FROM model_revisions WHERE root_id=? AND owner=? ORDER BY number',(root,owner)).fetchall()
            latest=links[-1]['number'] if links else 1
        versions=[]
        for identity,number,parent,changes in [(root,1,None,[])]+[(r['child_id'],r['number'],r['parent_id'],json.loads(r['changes_json'])) for r in links]:
            try:item=self.s.get(identity,owner)
            except KeyError:item={'state':'unavailable','created_at':None,'step_sha256':None,'job_id':None}
            versions.append({k:item.get(k) for k in ('state','step_sha256','job_id','created_at','artifact_integrity')}|{'session_id':identity,'number':number,'parent_session_id':parent,'changes':changes,'view_url':'/workbench/?session='+identity})
        return {'root_session_id':root,'selected_session_id':sid,'latest_number':latest,'versions':versions}

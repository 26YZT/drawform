"""Source-preserving drawing ingestion and version-bound human clarification."""
from __future__ import annotations
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import uuid
from .plate_spec import SpecError
from .simple_spec import spec_from_analysis


class DrawingWorkflow:
    def __init__(self,service):
        self.s=service
        with service.connect() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS drawing_inputs(id TEXT PRIMARY KEY,format TEXT NOT NULL,filename TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS clarification_receipts(owner TEXT NOT NULL,session TEXT NOT NULL,key TEXT NOT NULL,payload_sha TEXT NOT NULL,PRIMARY KEY(owner,session,key));''');db.commit()

    def lookup(self,bundle,owner):
        with self.s.connect() as db:
            row=db.execute('SELECT d.*,i.sha FROM drawing_inputs d JOIN inputs i ON i.id=d.id WHERE d.id=? AND i.owner=?',(bundle,owner)).fetchone()
        return dict(row) if row else None

    def upload(self,filename,data,owner):
        if not isinstance(filename,str) or not data or len(data)>20*1024*1024:raise SpecError('图纸须非空且不超过20MB。')
        name=filename.replace('\\','/').rsplit('/',1)[-1][:160];ext=Path(name).suffix.lower()
        formats={'.dxf':'dxf','.pdf':'pdf','.png':'png','.jpg':'jpeg','.jpeg':'jpeg'}
        fmt=formats.get(ext)
        if not fmt:raise SpecError('请选择DXF、PDF、PNG或JPEG图纸。','UNSUPPORTED_FORMAT')
        signatures={'pdf':data.startswith(b'%PDF-'),'png':data.startswith(b'\x89PNG\r\n\x1a\n'),'jpeg':data.startswith(b'\xff\xd8'),
                    'dxf':b'SECTION' in data[:1024] and b'EOF' in data[-1024:] and not data.startswith(b'AutoCAD Binary DXF')}
        if not signatures[fmt]:raise SpecError('扩展名与文件真实格式不符或图纸已损坏。','INVALID_DRAWING')
        bundle=uuid.uuid4().hex;sha=hashlib.sha256(data).hexdigest()
        directory=self.s.root/'inputs'/bundle;directory.mkdir(parents=True,exist_ok=False)
        source=directory/('source.'+fmt)
        with source.open('xb') as f:f.write(data);f.flush();os.fsync(f.fileno())
        self.s.write_json(directory/'input.json',{'source_sha256':sha,'filename':name,'format':fmt})
        with self.s.connect() as db:
            db.execute('INSERT INTO inputs VALUES(?,?,?,?)',(bundle,owner,'',sha))
            db.execute('INSERT INTO drawing_inputs VALUES(?,?,?)',(bundle,fmt,name));db.commit()
        return {'bundle_id':bundle,'input_kind':'drawing','filename':name,'source_sha256':sha,'format':fmt}

    def verify_source(self,data,owner):
        from .modeling import Conflict
        meta=self.lookup(data['bundle_id'],owner)
        if meta is None:raise Conflict('原图记录缺失。')
        path=self.s.root/'inputs'/meta['id']/('source.'+meta['format'])
        if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=meta['sha']:
            raise Conflict('原始图纸缺失或已改变，不能沿用旧的尺寸依据。')

    def basis(self,analysis,revision):
        return hashlib.sha256(json.dumps({'analysis':analysis,'revision':revision},sort_keys=True,ensure_ascii=False,allow_nan=False).encode()).hexdigest()

    def analyze(self,sid,owner,data,*,attempt_id=None,attempt_directory=None):
        s=self.s;meta=self.lookup(data['bundle_id'],owner)
        source=s.root/'inputs'/meta['id']/('source.'+meta['format'])
        if hashlib.sha256(source.read_bytes()).hexdigest()!=meta['sha']:raise SpecError('原始图纸已改变，禁止继续。','BASIS_STALE')
        directory=attempt_directory or s.directory(sid);directory.mkdir(parents=True,exist_ok=True);output=(directory/'analysis').resolve()
        req={'input_path':str(source),'format':meta['format'],'output_dir':str(output),'source_sha256':meta['sha']}
        s.write_json(directory/'analysis-request.json',req)
        s.subprocess([sys.executable,'-m','drawform.workbench.drawing_intake',str(directory/'analysis-request.json')],directory,directory/'analysis.log',source_path=s.source_root,timeout=180)
        analysis=json.loads((output/'analysis.json').read_text())
        if analysis.get('source_sha256')!=meta['sha'] or hashlib.sha256(source.read_bytes()).hexdigest()!=meta['sha']:
            raise SpecError('图纸分析与原文件版本不一致。','BASIS_STALE')
        if analysis.get('unsupported') and analysis['unsupported'] not in ('source_conflict','unexplained_annotation'):
            raise SpecError('图纸不在当前简单零件支持范围：'+str(analysis['unsupported']),'UNSUPPORTED_DRAWING')
        if analysis.get('kind') not in ('plate','washer','stepped_shaft'):raise SpecError('无法从图纸确定矩形板、圆盘/垫圈或二段阶梯轴，请补充清晰的单零件图。','UNSUPPORTED_DRAWING')
        analysis['source_method']='dxf_native_geometry' if meta['format']=='dxf' else 'local_ocr_role_labels'
        if meta['format']!='dxf':analysis['notes'].append('本批从明确角色标注重建尺寸，不声称理解任意图片轮廓；采用前须将源图与模型核对。')
        if any(f.get('parameter')=='through' and f.get('value')=='through' and f.get('evidence',{}).get('type')!='user_answer' for f in analysis.get('facts',[])):
            analysis['through_confirmed']=True;analysis['through_basis']='source_explicit'
        questions=analysis.setdefault('questions',[])
        for q in questions:
            if q.get('readonly') or analysis.get('source_conflict') or analysis.get('unsupported')=='source_conflict':q['type']='blocked'
        known={q.get('parameter') for q in questions}
        parameters=analysis.setdefault('parameters',{})
        keys={'plate':('length_mm','width_mm','thickness_mm'),'washer':('outer_diameter_mm','inner_diameter_mm','thickness_mm'),'stepped_shaft':('diameter_1_mm','length_1_mm','diameter_2_mm','length_2_mm')}[analysis['kind']]
        for k in keys:
            parameters.setdefault(k,None)
            if parameters[k] is None and k not in known:
                questions.append({'id':'q_'+k,'parameter':k,'label':{'diameter_1_mm':'第1段直径是多少？','length_1_mm':'第1段长度是多少？','diameter_2_mm':'第2段直径是多少？','length_2_mm':'第2段长度是多少？','thickness_mm':'厚度是多少？','length_mm':'长度是多少？','width_mm':'宽度是多少？','outer_diameter_mm':'外径是多少？','inner_diameter_mm':'内径是多少？'}[k],'reason':'图纸中未找到可用的明确尺寸。','type':'number','unit':analysis.get('units') or '图纸单位'})
        if analysis.get('units') not in ('mm','inch') and 'units' not in known:
            questions.append({'id':'q_units','parameter':'units','label':'图纸尺寸使用什么单位？','reason':'图纸没有明确可读的单位依据。','type':'choice','choices':[{'value':'mm','label':'毫米'},{'value':'inch','label':'英寸'}]})
        if analysis['kind']=='stepped_shaft' and not analysis.get('shaft_solid_confirmed') and 'shaft_solid' not in known:
            questions.append({'id':'q_shaft_solid','parameter':'shaft_solid','label':'确认这是二段同轴实心、无孔的阶梯轴？','reason':'单视图轮廓不能证明内部无孔。','type':'choice','choices':[{'value':'solid','label':'确认同轴实心、无孔'}]})
        holes=analysis.get('holes') or []
        needs=bool(holes) or (analysis['kind']=='washer' and (parameters.get('inner_diameter_mm') or 0)>0)
        if needs and not analysis.get('through_confirmed') and 'through' not in known:
            questions.append({'id':'q_through','parameter':'through','label':'这些圆孔是否沿厚度方向完全贯穿？','reason':'单视图圆轮廓本身不能证明通孔。','type':'choice','choices':[{'value':'through','label':'确认是贯通孔'}]})
        preview=output/'preview.png'
        if not preview.is_file():raise SpecError('图纸预览未生成，不能确认尺寸出处。')
        fields={'analysis':analysis,'questions':questions,'basis_sha256':self.basis(analysis,1),'clarification_revision':1,
                'source_preview_sha256':hashlib.sha256(preview.read_bytes()).hexdigest(),'source_preview_url':f'/api/workbench/v1/modeling/sessions/{sid}/source-preview',
                'model_kind':analysis['kind'],'parameters':parameters,'holes':holes,'clarifications':[]}
        if attempt_id:fields['analysis_attempt_id']=attempt_id
        with s.lock:
            if s.get(sid,owner)['state']!='analyzing' or s.stopped.is_set():return
            state='needs_clarification' if questions else 'queued'
            if not questions:
                spec=spec_from_analysis(analysis)
                fields.update(spec=spec,parameters=spec['parameters'],holes=spec['holes'])
            if attempt_id:
                s.attempts.capture(sid,owner,attempt_id,directory,{'state':state,'values':fields})
                s.attempts.complete(sid,owner,attempt_id,state,fields)
            else:s.update(sid,owner,state,**fields)
            if not questions:s.pending.put((sid,owner))

    def answer(self,sid,owner,body,key):
        return self.s.clarifications.answer(sid,owner,body,key)

    def preview(self,sid,owner):
        from .modeling import Conflict
        data=self.s.get(sid,owner);self.verify_source(data,owner)
        preview_sid=data.get('source_preview_session_id',sid)
        original=self.s.raw(preview_sid,owner)
        if original.get('source_sha256')!=data.get('source_sha256'):raise Conflict('原图预览与当前修改来源不一致。')
        from .paths import analysis_relative
        if data.get('analysis_attempt_id')!=original.get('analysis_attempt_id'):raise Conflict('源图预览执行版本不一致。')
        path=self.s.directory(preview_sid)/analysis_relative(original)/'preview.png'
        if not data.get('source_preview_sha256') or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=data['source_preview_sha256']:
            raise Conflict('图纸预览缺失或已改变。')
        return path

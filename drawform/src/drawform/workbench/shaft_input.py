"""Finite two-stage shaft facts from text, real DXF geometry or role-labelled OCR.

This module owns no CAD execution or persistence. Unknown annotations remain
blocking facts; only fields used by the shaft specification are consumed.
"""
from __future__ import annotations
import hashlib
import math
import re
import unicodedata
from .plate_spec import SpecError

NUMBER = r'[-+]?(?:\d+(?:\.\d*)?|\.\d+)'
FIELDS = ('diameter_1_mm','length_1_mm','diameter_2_mm','length_2_mm')
LABELS = {'diameter_1_mm':'第一段直径','length_1_mm':'第一段长度',
          'diameter_2_mm':'第二段直径','length_2_mm':'第二段长度'}
KIND = re.compile(r'(?:二段|两段|2\s*段)?\s*(?:同轴\s*)?(?:实心\s*)?阶梯轴|(?:two[-\s]*(?:stage|segment)\s+)?(?:coaxial\s+)?(?:solid\s+)?stepped\s+shaft\b',re.I)
SOLID = re.compile(r'实心|无孔|\bsolid\b|\bno\s+holes?\b',re.I)
UNIT = re.compile(r'毫米|(?<![a-z])mm\b|英寸|(?<![a-z])inch(?:es)?\b',re.I)
NON_MM = re.compile(r'厘米|英寸|公分|分米|微米|(?<!毫)米|(?<![a-z])(?:cm|inch(?:es)?|feet|ft|meters?)\b',re.I)
FORBIDDEN = re.compile(r'通孔|盲孔|沉孔|沉头|孔|螺纹|圆角|倒角|锥|偏心|三段|第三段|第3段|3\s*段|凹槽|键槽|加强筋|凸台|台阶|blind|counterbore|countersink|thread|tapped|fillet|chamfer|taper|eccentric|three[-\s]*stage|(?:stage|segment)\s*3\b|\bholes?\b|pocket|slot|keyway|boss|\bribs?\b|±|公差|配合|\btolerance\b',re.I)
STAGE = re.compile(r'第?([一二12])段|\b(?:segment|stage)\s*([12])(?!\d)',re.I)
PUNCT = r'[\s,，。.;；:：=、?？()（）\[\]{}]'


def has_shaft_declaration(text):
    return isinstance(text,str) and bool(KIND.search(unicodedata.normalize('NFKC',text)))


def _scan(text):
    """Return exact spans plus a residual; never assign an unqualified number."""
    rest=list(text); roles=[]; solids=[]; units=[]; kinds=[]
    def take(pattern):
        found=list(re.finditer(pattern,''.join(rest),re.I))
        for m in found:rest[m.start():m.end()]=' '*(m.end()-m.start())
        return found
    for m in take(KIND.pattern):kinds.append((m.start(),m.end()))
    # Solidity may occur inside the consumed type declaration.
    for m in SOLID.finditer(text):
        solids.append((m.start(),m.end()));rest[m.start():m.end()]=' '*(m.end()-m.start())
    take(r'同轴|\bcoaxial\b')
    for m in take(r'(?:尺寸单位|单位|\bunits?\b)\s*(?:为|是|=|:)?\s*(毫米|mm|英寸|inch(?:es)?)'):
        units.append(('inch' if re.search(r'英寸|inch',m.group(1),re.I) else 'mm',m.start(),m.end()))
    for m in take(UNIT.pattern):units.append(('inch' if re.search(r'英寸|inch',m.group(),re.I) else 'mm',m.start(),m.end()))
    aliases=[]
    for stem,short in (('diameter','d'),('length','l')):
        pattern=rf'(?<![\w])(?:{stem}_([12])_mm|{short}([12]))(?![\w])\s*[:=]?\s*({NUMBER})?'
        for m in take(pattern):
            stage=int(m.group(1) or m.group(2));key=f'{stem}_{stage}_mm'
            roles.append((key,float(m.group(3)) if m.group(3) is not None else None,m.start(),m.end()))
            aliases.append((stage,m.start()))
    markers=list(STAGE.finditer(''.join(rest)))
    for i,m in enumerate(markers):
        stage={'一':1,'二':2,'1':1,'2':2}[m.group(1)] if m.group(1) else int(m.group(2))
        end=markers[i+1].start() if i+1<len(markers) else len(text)
        if any(index!=stage and m.end()<=position<end for index,position in aliases):
            raise SpecError('段号与尺寸角色标签冲突。','SOURCE_FACT_CONFLICT')
        rest[m.start():m.end()]=' '*(m.end()-m.start())
        for stem,label in (('diameter',r'直径|\bdiameter'),('length',r'长度|长|\blength')):
            pattern=rf'(?:{label})\s*(?:为|=|:)?\s*({NUMBER})?'
            for found in re.finditer(pattern,''.join(rest)[m.end():end],re.I):
                start=m.end()+found.start();stop=m.end()+found.end()
                roles.append((f'{stem}_{stage}_mm',float(found.group(1)) if found.group(1) is not None else None,start,stop))
                rest[start:stop]=' '*(stop-start)
    # Introductory words are anchored to the original text, not the blanked
    # remainder: otherwise a trailing datum "A" can turn into a leading article.
    for pattern in (r'^\s*(?:(?:请|帮我)\s*){0,2}(?:生成|制作|做)\s*(?:一个|一根)?',
                    r'^\s*(?:please\s+)?(?:make|create)\s+(?:a\s+)?',r'^\s*a\b'):
        for m in re.finditer(pattern,text,re.I):rest[m.start():m.end()]=' '*(m.end()-m.start())
    take(r'的')
    residual=re.sub(PUNCT,'',''.join(rest))
    unsupported=bool(FORBIDDEN.search(''.join(rest)))
    return {'roles':roles,'solids':solids,'units':units,'kinds':kinds,'residual':residual,'unsupported':unsupported}


def _base(sha,input_kind):
    return {'schema_version':2,'input_kind':input_kind,'source_sha256':sha,'kind':'stepped_shaft',
            'part_type':'stepped_shaft','parameters':{k:None for k in FIELDS},'holes':[],
            'units':None,'facts':[],'questions':[],'notes':[],'shaft_solid_confirmed':False,
            'requires_explicit_origin':False,'coordinate_origin_confirmed':True,
            'through_confirmed':False,'preview_file':'preview.png'}


def _fact(a,key,value,evidence,unit=None):
    a['facts'].append({'id':f"fact_{len(a['facts'])+1}",'parameter':key,'value':value,
                       'unit':unit or a.get('units') or 'source_unit','evidence':evidence})


def _block(a,message,code='source_conflict',evidence=None,text=None):
    if not a.get('unsupported'):a['unsupported']=code
    a['notes'].append(message)
    if evidence is not None:_fact(a,code,text or evidence.get('text',message),evidence,'text')
    if not any(q.get('readonly') for q in a['questions']):
        a['questions'].append({'id':code,'parameter':code,'label':'请更正原始输入后重新上传',
                              'reason':message,'type':'choice','choices':[],'readonly':True})


def refresh_shaft_questions(analysis):
    """Mutate only questions/derived state; return the same object and retain facts."""
    if analysis.get('kind')!='stepped_shaft' or analysis.get('holes')!=[] or set(analysis.get('parameters',{}))!=set(FIELDS):
        raise SpecError('二段阶梯轴规格字段不正确。')
    if analysis.get('unsupported') or any(q.get('readonly') or q.get('type')=='blocked' for q in analysis.get('questions',[])):return analysis
    units=analysis.get('units');allowed=(None,'mm') if analysis.get('input_kind')=='text' else (None,'mm','inch')
    if units not in allowed:raise SpecError('轴输入单位不受支持。','UNSUPPORTED_UNIT')
    factor=25.4 if units=='inch' else 1.;p=analysis['parameters']
    for key,value in p.items():
        if value is not None and (isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not 0<value*factor<=1000):
            raise SpecError(f'{LABELS[key]}须为大于0且不超过1000毫米的有限数值。')
    lengths=[p[k]*factor for k in ('length_1_mm','length_2_mm') if p[k] is not None]
    if sum(lengths)>1000 or len(lengths)==1 and lengths[0]>=1000:raise SpecError('轴总长须不超过1000毫米。')
    if p['diameter_1_mm'] is not None and p['diameter_2_mm'] is not None and abs(p['diameter_1_mm']-p['diameter_2_mm'])*factor<=1e-6:
        raise SpecError('两段直径相等或退化，不是本批阶梯轴。','SOURCE_FACT_CONFLICT')
    if type(analysis.get('shaft_solid_confirmed',False)) is not bool:raise SpecError('轴实心确认状态不正确。')
    questions=[]
    def ask(key,label,reason,choices=None):
        question_unit=units or ('source_unit' if analysis.get('input_kind')=='drawing' else 'mm')
        if not choices and question_unit=='source_unit':reason+=' 请按所选原图单位填写数值。'
        q={'id':key,'parameter':key,'label':label,'reason':reason,'type':'choice' if choices else 'number','unit':question_unit}
        if choices:q['choices']=[{'value':v,'label':s} for v,s in choices]
        questions.append(q)
    if units is None:ask('units','请确认轴尺寸单位','原始输入没有明确单位。',[('mm','毫米')] if analysis.get('input_kind')=='text' else [('mm','毫米'),('inch','英寸')])
    if not analysis.get('shaft_solid_confirmed'):ask('shaft_solid','是否为二段同轴实心无孔轴？','没有实心/无孔来源事实，不自动推断。',[('solid','确认二段同轴实心、无孔')])
    for key in FIELDS:
        if p[key] is None:ask(key,LABELS[key]+'是多少？','该段角色未给出可信明确数值。')
    analysis['questions']=questions
    return analysis


def analyze_shaft_text(text):
    if not isinstance(text,str) or not text.strip() or len(text)>2000:raise SpecError('请填写最多2000字的阶梯轴描述。')
    norm=unicodedata.normalize('NFKC',text)
    if NON_MM.search(norm):raise SpecError('文字轴输入仅支持毫米。','UNSUPPORTED_UNIT')
    scanned=_scan(norm)
    if len(scanned['kinds'])!=1:raise SpecError('请明确一个二段阶梯轴。','UNSUPPORTED_FEATURE')
    if scanned['unsupported']:raise SpecError('轴描述包含未支持特征。','UNSUPPORTED_FEATURE')
    if scanned['residual']:raise SpecError('轴描述有未消费词语或数字，不能猜段号、表达式或尺寸。','UNCONSUMED_REQUIREMENT')
    sha=hashlib.sha256(text.encode()).hexdigest();nh=hashlib.sha256(norm.encode()).hexdigest();a=_base(sha,'text')
    a.update(normalized_text=norm,normalized_text_sha256=nh,normalization='NFKC')
    def evidence(start,end):return {'type':'text_span','span_basis':'normalized_text','span':[start,end],
        'text':norm[start:end],'source_sha256':sha,'normalized_text_sha256':nh}
    for start,end in scanned['kinds']:_fact(a,'part_type','stepped_shaft',evidence(start,end),'intent')
    for start,end in scanned['solids']:
        a['shaft_solid_confirmed']=True;_fact(a,'shaft_solid','solid',evidence(start,end),'intent')
    for unit,start,end in scanned['units']:a['units']=unit;_fact(a,'units',unit,evidence(start,end),'unit')
    seen=set()
    for key,value,start,end in scanned['roles']:
        if key in seen:raise SpecError('同一轴段尺寸重复或冲突。','SOURCE_FACT_CONFLICT')
        seen.add(key);a['parameters'][key]=value;_fact(a,key,value,evidence(start,end))
    return refresh_shaft_questions(a)


def analyze_shaft_entries(entries,sha,*,native_parameters=None,units=None):
    """Role-labelled drawing entries; every entry gets a consumption record."""
    a=_base(sha,'drawing');a['units']=units
    if native_parameters is not None:a['parameters'].update(native_parameters)
    records=[];seen={};role_pages=set();kind_pages=set();credible=set();kind_count=0
    for entry in entries:
        text=unicodedata.normalize('NFKC',entry['text']);e=entry['evidence'];trusted=e.get('confidence',1)>=.4
        record={'id':f'annotation-{len(records)+1}','text':entry['text'],'roles':[],'evidence':e}
        metadata=re.fullmatch(r'(?:图号|drawing\s*(?:no\.?|number)|part\s*(?:no\.?|number))\s*[:=：]\s*[a-z0-9_.-]+|第\s*\d+\s*页|page\s*\d+(?:\s*of\s*\d+)?|(?:比例|scale)\s*[:=：]?\s*\d+(?:\.\d+)?\s*[:/]\s*\d+(?:\.\d+)?',text.strip(),re.I)
        if metadata:record['status']='allowed_text';records.append(record);continue
        try:scanned=_scan(text)
        except SpecError as error:
            scanned={'roles':[],'kinds':[],'solids':[],'units':[],'residual':text,'unsupported':False}
            _block(a,str(error),'source_conflict',e,text)
        if trusted:
            for start,end in scanned['kinds']:
                kind_count+=1
                _fact(a,'part_type','stepped_shaft',e,'intent');record['roles'].append('kind')
                if 'page_index' in e:kind_pages.add(e['page_index'])
            for start,end in scanned['solids']:
                a['shaft_solid_confirmed']=True;_fact(a,'shaft_solid','solid',e,'intent');record['roles'].append('shaft_solid')
            for unit,start,end in scanned['units']:
                _fact(a,'units',unit,e,'unit');record['roles'].append('units')
                if a['units'] is not None and a['units']!=unit:_block(a,'图纸单位与已有单位来源冲突。','source_conflict',e,text)
                else:a['units']=unit
            for key,value,start,end in scanned['roles']:
                _fact(a,key,value,e);record['roles'].append(key)
                if value is not None:
                    credible.add(key)
                    if 'page_index' in e:role_pages.add(e['page_index'])
                    existing=a['parameters'][key]
                    if existing is not None and not math.isclose(existing,value,rel_tol=1e-7,abs_tol=1e-6):
                        _block(a,'图纸段尺寸与已有明确事实冲突。','source_conflict',e,text)
                    elif existing is None:a['parameters'][key]=value
                    if key in seen and not math.isclose(seen[key],value,rel_tol=1e-7,abs_tol=1e-6):_block(a,'图纸同一段尺寸出现不同数值。','source_conflict',e,text)
                    seen[key]=value
        residual=scanned['residual'] if trusted else text
        if not record['roles'] and text.strip():residual=residual or text
        if scanned['unsupported']:_block(a,'图纸轴备注包含未支持特征。','unsupported_feature',e,text)
        if residual:
            record.update(status='unexplained',residual=residual)
            _block(a,'未解释轴图纸原文：'+entry['text'],'unexplained_annotation',e,entry['text'])
        else:record['status']='consumed_role'
        records.append(record)
    a['text_consumption']=records
    if kind_count!=1:_block(a,'轴图纸须有且只有一个可信阶梯轴类型声明。','ambiguous_parts' if kind_count else 'unreadable_shape')
    if native_parameters is None:
        if len(credible)<2:_block(a,'轴图纸至少需要两个可信段角色值，不能手填替代全部识图。','unreadable_dimensions')
        if len(role_pages)>1 or kind_pages and role_pages and not role_pages<=kind_pages:_block(a,'轴类型和尺寸来自多个有效页面或无法绑定同页。','ambiguous_pages')
    try:refresh_shaft_questions(a)
    except SpecError as error:_block(a,str(error),'source_conflict')
    return a


def _profile(document):
    entities=list(document.modelspace());geometry=[e for e in entities if e.dxftype() in ('LWPOLYLINE','LINE')]
    if any(e.dxftype() not in ('LWPOLYLINE','LINE','TEXT','MTEXT','DIMENSION') for e in entities):raise SpecError('轴DXF有额外实体或孔圆。')
    if len(geometry)==1 and geometry[0].dxftype()=='LWPOLYLINE':
        poly=geometry[0]
        width=float(poly.dxf.get('const_width',0))
        if not poly.closed or not math.isfinite(width) or width or any(not math.isfinite(v) or abs(v)>1e-9 for p in poly.get_points() for v in p[2:5]):raise SpecError('轴轮廓必须闭合、无宽度且全部直线。')
        points=[(float(p[0]),float(p[1])) for p in poly.get_points('xy')]
        if len(points)>1 and points[0]==points[-1]:points.pop()
    elif len(geometry)==8 and all(e.dxftype()=='LINE' for e in geometry):
        links={}
        for e in geometry:
            for p,q in ((e.dxf.start,e.dxf.end),(e.dxf.end,e.dxf.start)):
                key=(round(p.x,8),round(p.y,8));links.setdefault(key,[]).append((round(q.x,8),round(q.y,8)))
        if len(links)!=8 or any(len(v)!=2 for v in links.values()):raise SpecError('轴线段不能构成唯一八点闭环。')
        points=[min(links)];previous=None
        for _ in range(7):
            options=[q for q in links[points[-1]] if q!=previous];following=min(options)
            previous=points[-1];points.append(following)
        if points[0] not in links[points[-1]]:raise SpecError('轴轮廓没有闭合。')
    else:raise SpecError('轴DXF只支持一个八点轮廓，不忽略独立轮廓/中心线。')
    if len(points)!=8 or len(set(points))!=8 or not all(math.isfinite(v) for p in points for v in p):raise SpecError('轴轮廓顶点无效。')
    for e in geometry:
        if any(not math.isfinite(a) or abs(a-b)>1e-6 for a,b in zip(e.dxf.get('extrusion',(0,0,1)),(0,0,1))):raise SpecError('轴DXF不是同一XY方向。')
    zs=[float(e.dxf.elevation) for e in geometry if e.dxftype()=='LWPOLYLINE']+[float(p.z) for e in geometry if e.dxftype()=='LINE' for p in (e.dxf.start,e.dxf.end)]
    if not all(math.isfinite(z) for z in zs) or max(zs)-min(zs)>1e-6:raise SpecError('轴DXF存在无效或多个平面。')
    xs=sorted({round(p[0],8) for p in points})
    if len(xs)!=3:raise SpecError('轴轮廓必须恰好一个肩面轴向位置。')
    for a,b in zip(points,points[1:]+points[:1]):
        if abs(a[0]-b[0])>1e-6 and abs(a[1]-b[1])>1e-6:raise SpecError('轴轮廓包含斜线。')
    spans=[]
    for lo,hi in zip(xs,xs[1:]):
        mid=(lo+hi)/2
        ys=sorted({round(a[1],8) for a,b in zip(points,points[1:]+points[:1]) if abs(a[1]-b[1])<1e-6 and min(a[0],b[0])<mid<max(a[0],b[0])})
        if len(ys)!=2:raise SpecError('每段轴轮廓须有上下两条边。')
        spans.append(ys)
    if abs(sum(spans[0])-sum(spans[1]))>1e-6:raise SpecError('两段轴轮廓不是同轴对称。')
    y1,y2=spans;expected=[(xs[0],y1[0]),(xs[1],y1[0]),(xs[1],y2[0]),(xs[2],y2[0]),(xs[2],y2[1]),(xs[1],y2[1]),(xs[1],y1[1]),(xs[0],y1[1])]
    def edges(p):return {tuple(sorted((tuple(round(v,8) for v in a),tuple(round(v,8) for v in b)))) for a,b in zip(p,p[1:]+p[:1])}
    if edges(points)!=edges(expected):raise SpecError('轴轮廓不能对应严格二段阶梯轴。')
    return {'diameter_1_mm':y1[1]-y1[0],'length_1_mm':xs[1]-xs[0],
            'diameter_2_mm':y2[1]-y2[0],'length_2_mm':xs[2]-xs[1]},geometry,xs,spans


def analyze_shaft_dxf(document,sha):
    entries=[]
    for e in document.modelspace():
        if e.dxftype() in ('TEXT','MTEXT'):
            text=e.plain_text();entries.append({'text':text,'evidence':{'type':'dxf_entity','entity_handle':e.dxf.handle,'layer':e.dxf.layer,'text':text}})
    header=int(document.header.get('$INSUNITS',0));units={4:'mm',1:'inch'}.get(header)
    try:p,geometry,xs,spans=_profile(document)
    except SpecError as error:
        a=analyze_shaft_entries(entries,sha,native_parameters={k:None for k in FIELDS},units=units)
        _block(a,str(error),'unsupported_geometry');a['unsupported']='unsupported_geometry';return a
    a=analyze_shaft_entries(entries,sha,native_parameters=p,units=units)
    if header not in (0,1,4):_block(a,'DXF轴单位不受支持。','unsupported_unit')
    if units:_fact(a,'units',units,{'type':'dxf_header','text':f'$INSUNITS={header}'},'unit')
    for key,value in p.items():_fact(a,key,value,{'type':'dxf_entity','entity_handle':geometry[0].dxf.handle,'layer':geometry[0].dxf.layer,'entity_handles':[e.dxf.handle for e in geometry],'text':'真实八点轴向轮廓，从左至右段1/2'})
    # Native dimensions are checked from their definition points, never a value pool.
    for dim in document.modelspace().query('DIMENSION'):
        raw_display=dim.dxf.get('text','')
        evidence={'type':'dxf_entity','entity_handle':dim.dxf.handle,'layer':dim.dxf.layer,'text':raw_display}
        try:
            plane=float(geometry[0].dxf.elevation if geometry[0].dxftype()=='LWPOLYLINE' else geometry[0].dxf.start.z)
            if any(not math.isfinite(v) or abs(v-expected)>1e-6 for v,expected in zip(dim.dxf.get('extrusion',(0,0,1)),(0,0,1))):raise SpecError('轴原生尺寸不在支持的XY方向。')
            for name in ('defpoint','defpoint2','defpoint3','defpoint4','defpoint5'):
                if dim.dxf.hasattr(name):
                    point=dim.dxf.get(name)
                    if not all(math.isfinite(v) for v in point) or abs(point.z-plane)>1e-6:raise SpecError('轴原生尺寸定义点不在同一有效轮廓平面。')
            value=float(dim.get_measurement());kind=dim.dimtype
            if kind not in (0,1):raise SpecError('轴单轮廓只接受水平/垂直原生线性尺寸。')
            p1,p2=dim.dxf.defpoint2,dim.dxf.defpoint3
            angle=(math.degrees(math.atan2(p2.y-p1.y,p2.x-p1.x)) if kind==1 else float(dim.dxf.get('angle',0)))%180
            if abs(float(dim.override().get('dimlfac',1))-1)>1e-9:raise SpecError('轴原生尺寸dimlfac不为1。')
            if min(abs(angle),abs(angle-180))<1e-6:
                if any(not any(abs(x-v)<1e-6 for v in xs) for x in (p1.x,p2.x)):raise SpecError('轴长度尺寸定义点不是端面/肩面位置。')
                if not math.isclose(value,abs(p1.x-p2.x),abs_tol=1e-6):raise SpecError('轴长度尺寸与实际轴向距离冲突。')
            elif abs(angle-90)<1e-6:
                if abs(p1.x-p2.x)>1e-6:raise SpecError('轴直径尺寸没有明确横截面位置。')
                candidates=[ys for i,ys in enumerate(spans) if xs[i]-1e-6<=p1.x<=xs[i+1]+1e-6]
                if not any(abs(min(p1.y,p2.y)-ys[0])<1e-6 and abs(max(p1.y,p2.y)-ys[1])<1e-6 and abs(value-(ys[1]-ys[0]))<1e-6 for ys in candidates):raise SpecError('轴直径尺寸与该段实际轮廓冲突。')
            else:raise SpecError('轴原生尺寸角度不受支持。')
            displayed=raw_display.replace('%%c','ø')
            unit_values={('inch' if re.search(r'英寸|inch',m.group(),re.I) else 'mm') for m in UNIT.finditer(displayed)}
            if len(unit_values)>1 or unit_values and a['units'] is not None and a['units'] not in unit_values:
                raise SpecError('轴原生尺寸显示单位与已有单位冲突。')
            if unit_values and a['units'] is None:
                a['units']=next(iter(unit_values));_fact(a,'units',a['units'],evidence,'unit')
            if min(abs(angle),abs(angle-180))<1e-6 and re.search(r'[øØ⌀]',displayed):raise SpecError('轴长度尺寸错误地显示直径符号。')
            numbers=re.findall(NUMBER,displayed) if '<>' not in displayed else []
            if numbers and (len(numbers)!=1 or not math.isclose(float(numbers[0]),value,abs_tol=1e-6)):raise SpecError('轴原生尺寸显示替换与实际测量冲突。')
            leftover=UNIT.sub('',displayed)
            leftover=re.sub(r'<>|[øØ⌀\s]','',leftover) if '<>' in displayed else re.sub(NUMBER+r'|[øØ⌀\s]','',leftover)
            _fact(a,'native_dimension',value,evidence)
            if displayed:
                record={'id':f"annotation-{len(a['text_consumption'])+1}",'text':raw_display,'roles':['native_dimension'],'evidence':evidence,'status':'unexplained' if leftover else 'consumed_role'}
                if leftover:record['residual']=leftover;_block(a,'原生尺寸有未解释备注。','unexplained_annotation',evidence,displayed)
                a['text_consumption'].append(record)
        except (SpecError,TypeError,ValueError,AttributeError) as error:
            _block(a,str(error),'source_conflict',evidence,raw_display or str(error))
            if raw_display:a['text_consumption'].append({'id':f"annotation-{len(a['text_consumption'])+1}",'text':raw_display,
                'roles':[],'evidence':evidence,'status':'unexplained','residual':str(error)})
    try:refresh_shaft_questions(a)
    except SpecError as error:_block(a,str(error),'source_conflict')
    return a

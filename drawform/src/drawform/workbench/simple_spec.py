"""Finite mechanical specifications shared by HTTP control and isolated workers."""
from __future__ import annotations
import math
from .plate_spec import SpecError


def finite(value,name):
    if isinstance(value,bool) or not isinstance(value,(int,float)):
        raise SpecError(f'{name}须为有限数值。')
    try:value=float(value)
    except (ValueError,OverflowError):raise SpecError(f'{name}数值超出范围。')
    if not math.isfinite(value):raise SpecError(f'{name}须为有限数值。')
    return value


def number(value, name, *, zero=False):
    value=finite(value,name)
    if not (0<=value<=1000 if zero else 0<value<=1000):
        raise SpecError(f'{name}须在'+('0至1000' if zero else '大于0至1000')+'毫米范围内。')
    return value


def validate_spec(spec):
    if not isinstance(spec,dict) or set(spec)!={'schema_version','kind','parameters','holes'} or type(spec['schema_version']) is not int or spec['schema_version']!=2:
        raise SpecError('模型规格必须采用有限的schema2字段。')
    kind=spec['kind'];p=spec['parameters'];holes=spec['holes']
    if not isinstance(p,dict) or not isinstance(holes,list):raise SpecError('参数和孔列表格式错误。')
    if kind=='plate':
        if set(p)!={'length_mm','width_mm','thickness_mm'} or len(holes)>16:raise SpecError('矩形板只接受长宽厚和最多16个显式通孔。')
        p={k:number(v,k) for k,v in p.items()};out=[]
        for hole in holes:
            if not isinstance(hole,dict) or set(hole)!={'x_mm','y_mm','diameter_mm'}:raise SpecError('孔仅接受中心XY及直径。')
            d=number(hole['diameter_mm'],'孔径');xy=[]
            for k in ('x_mm','y_mm'):
                value=hole[k]
                xy.append(finite(value,'孔位置'))
            if abs(xy[0])+d/2>=p['length_mm']/2-1e-6 or abs(xy[1])+d/2>=p['width_mm']/2-1e-6:
                raise SpecError('孔必须完整位于板内；本批不支持开边槽或相切孔。')
            if any(math.hypot(xy[0]-h['x_mm'],xy[1]-h['y_mm'])<=(d+h['diameter_mm'])/2+1e-6 for h in out):
                raise SpecError('孔不得相交、重合或相切。')
            out.append({'x_mm':xy[0],'y_mm':xy[1],'diameter_mm':d})
        holes=out
    elif kind=='washer':
        if set(p)!={'outer_diameter_mm','inner_diameter_mm','thickness_mm'} or holes:raise SpecError('圆盘/垫圈仅接受外径、内径和厚度。')
        p={k:number(v,k,zero=k=='inner_diameter_mm') for k,v in p.items()}
        if p['inner_diameter_mm']>=p['outer_diameter_mm']:raise SpecError('内径必须小于外径。')
    elif kind=='stepped_shaft':
        keys={'diameter_1_mm','length_1_mm','diameter_2_mm','length_2_mm'}
        if set(p)!=keys or holes:raise SpecError('二段实心阶梯轴只接受两段直径和长度，不接受孔或额外特征。')
        p={k:number(v,k) for k,v in p.items()}
        if abs(p['diameter_1_mm']-p['diameter_2_mm'])<=1e-6:raise SpecError('两段直径必须不同，同径请使用圆柱/圆盘类型。')
        if p['length_1_mm']+p['length_2_mm']>1000:raise SpecError('阶梯轴总长须不超过1000毫米。')
    else:raise SpecError('本批只支持矩形板、圆盘/垫圈与二段实心阶梯轴。','UNSUPPORTED_FEATURE')
    return {'schema_version':2,'kind':kind,'parameters':p,'holes':holes}


def spec_from_analysis(analysis):
    if analysis.get('unsupported') or analysis.get('source_conflict'):raise SpecError('输入存在冲突或未支持特征，请更正输入。','SOURCE_FACT_CONFLICT')
    if analysis.get('units') not in ('mm','inch'):raise SpecError('输入单位尚未确认。','MISSING_CRITICAL_DIMENSION')
    if analysis.get('kind')=='stepped_shaft' and not analysis.get('shaft_solid_confirmed'):
        raise SpecError('二段轴的实心、无孔性质尚未确认。','MISSING_CRITICAL_DIMENSION')
    factor=25.4 if analysis['units']=='inch' else 1.0
    p=analysis.get('parameters') or {}
    if any(v is None for v in p.values()):raise SpecError('还有关键尺寸未确认。','MISSING_CRITICAL_DIMENSION')
    if analysis.get('requires_explicit_origin') and not analysis.get('coordinate_origin_confirmed'):
        raise SpecError('孔坐标原点尚未确认。','MISSING_CRITICAL_DIMENSION')
    holes=analysis.get('holes') or []
    needs_through=bool(holes) or (analysis.get('kind')=='washer' and p.get('inner_diameter_mm',0)>0)
    if needs_through and not analysis.get('through_confirmed'):
        raise SpecError('孔的贯通性质尚未确认。','MISSING_CRITICAL_DIMENSION')
    return validate_spec({'schema_version':2,'kind':analysis.get('kind'),
                          'parameters':{k:finite(v,k)*factor for k,v in p.items()},
                          'holes':[{k:finite(v,k)*factor for k,v in h.items()} for h in holes]})


def parse_extended_text(text,units='mm'):
    """Explicit finite washer/disc grammar; old plate grammar remains unchanged."""
    import re,unicodedata
    if not isinstance(text,str) or len(text)>2000:raise SpecError('请填写明确的简单零件尺寸。')
    t=unicodedata.normalize('NFKC',text).lower()
    if re.search(r'矩形板|长方形板|\brectangular\s+plate\b',t):
        from .text_simple import parse_simple_plate
        return parse_simple_plate(text)
    is_washer=bool(re.search(r'垫圈|\bwasher\b',t));is_disc=bool(re.search(r'实心圆盘|\bsolid\s+(?:disc|disk)\b',t))
    if not is_washer and not is_disc:raise SpecError('本段文字只支持原中心通孔板、明确垫圈或实心圆盘。','UNSUPPORTED_FEATURE')
    if units!='mm' or re.search(r'厘米|英寸|\bcm\b|\binch',t):raise SpecError('文字输入请使用毫米。','UNSUPPORTED_UNIT')
    patterns={'outer_diameter_mm':r'(?:外径|outer\s+diameter)\s*(?:为|=|:)?\s*(\d+(?:\.\d+)?)',
              'inner_diameter_mm':r'(?:内径|inner\s+diameter)\s*(?:为|=|:)?\s*(\d+(?:\.\d+)?)',
              'thickness_mm':r'(?:厚度|厚|thickness)\s*(?:为|=|:)?\s*(\d+(?:\.\d+)?)'}
    values={};rest=t
    for key,pattern in patterns.items():
        matches=list(re.finditer(pattern,t))
        if len(matches)>1:raise SpecError('同一尺寸出现多次，请保留明确的一组。','SOURCE_FACT_CONFLICT')
        if matches:values[key]=float(matches[0].group(1));rest=re.sub(pattern,' ',rest)
    if is_disc and 'inner_diameter_mm' not in values:values['inner_diameter_mm']=0.0
    if set(values)!=set(patterns):raise SpecError('请完整填写外径、内径和厚度；实心圆盘无需内径。','MISSING_CRITICAL_DIMENSION')
    rest=re.sub(r'毫米|垫圈|实心圆盘|请|做|一|个|的|生成','',rest)
    rest=re.sub(r'\b(?:washer|solid|disc|disk|mm|make|a)\b','',rest)
    if re.search(r'[a-z0-9\u4e00-\u9fff]',rest):raise SpecError('仍有本段不能消费的特征，请仅描述简单垫圈或实心圆盘。','UNCONSUMED_REQUIREMENT')
    if is_washer and values['inner_diameter_mm']<=0:raise SpecError('垫圈内径必须大于0；无孔请明确写实心圆盘。')
    return validate_spec({'schema_version':2,'kind':'washer','parameters':values,'holes':[]})

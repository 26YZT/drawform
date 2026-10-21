"""图前斜孔参考：真实平面候选、稳定身份、闭合选择及原精度求交。"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math

from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.BRepClass import BRepClass_FaceClassifier
from OCP.BRepGProp import BRepGProp
from OCP.GeomAbs import GeomAbs_Circle, GeomAbs_Line, GeomAbs_Plane
from OCP.GProp import GProp_GProps
from OCP.Precision import Precision
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_IN, TopAbs_ON
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS
from OCP.gp import gp_Pnt

from drawform.delivery import BLOCK, DeliveryBlocked, Verdict
from drawform.domain.curves import view_basis
from drawform.geometry.features import Bore, find_counterbores

BASIS_VERSION = 'oblique-plane-reference-v1-20261015-1'
MAX_OPTIONS = 64
GEOMETRY_TOL = Precision.Confusion_s()
# 求交倒数放大限由OCCT角度精度导出，不能把近乎平行平面当稳定截面。
ANGULAR_LIMIT = math.sqrt(Precision.Angular_s())


def _xyz(point):
    return point.X(), point.Y(), point.Z()


def _dot(a, b):
    return math.fsum(x*y for x,y in zip(a,b))


def _quantized(value):
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError('参考几何含非有限数值')
        return round(value, 6) or 0.0
    if isinstance(value, dict):
        return {k:_quantized(v) for k,v in sorted(value.items())}
    if isinstance(value, (tuple,list)):
        return [_quantized(v) for v in value]
    return value


def _hash(value):
    return hashlib.sha256(json.dumps(_quantized(value),sort_keys=True,
                                    separators=(',',':'),ensure_ascii=False).encode()).hexdigest()


def _unit(vector):
    length=math.sqrt(_dot(vector,vector))
    if not math.isfinite(length) or length <= Precision.Angular_s():
        raise ValueError('参考几何缺少有效方向')
    result=tuple(v/length for v in vector)
    index=max(range(3),key=lambda i:abs(result[i]))
    return result if result[index]>0 else tuple(-v for v in result)


def is_oblique(bore):
    return sum(abs(v)>Precision.Angular_s() for v in _unit(bore.axis_direction))>1


def bore_geometry(bore):
    d=_unit(bore.axis_direction)
    projection=_dot(bore.axis_point,d)
    offset=tuple(p-projection*v for p,v in zip(bore.axis_point,d))
    span=bore.axis_span
    if (span is None or len(span)!=2 or not all(math.isfinite(v) for v in span)
            or span[1]-span[0]<=GEOMETRY_TOL):
        raise ReferenceUnavailable('reference_unavailable','斜孔缺少可信的有限轴向范围，不能枚举定位参考')
    ends=tuple(tuple(c+s*v for c,v in zip(offset,d)) for s in span)
    geometry={'direction':d,'offset':offset,'diameter':bore.diameter,'ends':ends}
    return 'bore-ref-'+_hash(geometry)[:24], geometry


class ReferenceUnavailable(DeliveryBlocked):
    def __init__(self, reason, message):
        super().__init__(Verdict(BLOCK,(reason,),(message,)))


class ReferenceRequired(DeliveryBlocked):
    def __init__(self, snapshot):
        super().__init__(Verdict(BLOCK,('reference_required',),
            ('斜孔定位需要先由工程师确认参考截面；尚未生成交付图',)))
        self.snapshot=snapshot


class ReferenceInputError(ValueError):
    def __init__(self, message, reason='invalid_reference_answer', status_code=400):
        super().__init__(message)
        self.reason=reason
        self.status_code=status_code


@dataclass(frozen=True)
class ReferenceFact:
    feature_id: str
    bore_id: str
    plane_id: str
    point: tuple[float,float,float]
    normal: tuple[float,float,float]
    constant: float
    on_patch: bool
    source_sha256: str
    basis_sha: str
    axes: tuple[str,str]


def _plane(face):
    adaptor=BRepAdaptor_Surface(face)
    if adaptor.GetType()!=GeomAbs_Plane:
        return None
    plane=adaptor.Plane()
    raw=_xyz(plane.Axis().Direction())
    n=_unit(raw)
    # 用原精度法向先求常数；量化仅用于身份，绝不写回求交数值。
    k=_dot(n,_xyz(plane.Location()))
    props=GProp_GProps();BRepGProp.SurfaceProperties_s(face,props)
    edges=[];paths=[];explorer=TopExp_Explorer(face,TopAbs_EDGE)
    while explorer.More():
        edge=TopoDS.Edge(explorer.Current());curve=BRepAdaptor_Curve(edge)
        a,b=curve.FirstParameter(),curve.LastParameter()
        if not all(math.isfinite(v) for v in (a,b)):
            raise ReferenceUnavailable('reference_unavailable','参考面的边界参数无效')
        line=curve.GetType()==GeomAbs_Line
        points=[_xyz(curve.Value(a+(b-a)*i/(1 if line else 32))) for i in range(2 if line else 33)]
        paths.append(points)
        mass=GProp_GProps();BRepGProp.LinearProperties_s(edge,mass)
        identity=[str(curve.GetType()),mass.Mass(),_xyz(mass.CentreOfMass())]
        if curve.GetType()==GeomAbs_Circle:
            circle=curve.Circle()
            identity.extend([_xyz(circle.Location()),circle.Radius(),_unit(_xyz(circle.Axis().Direction()))])
        else:
            identity.append(sorted(_xyz(curve.Value(a+(b-a)*i/8)) for i in range(9)))
        edges.append(_hash(identity));explorer.Next()
    identity={'normal':n,'constant':k,'area':props.Mass(),
              'centroid':_xyz(props.CentreOfMass()),'edges':sorted(set(edges))}
    return {'id':'plane-ref-'+_hash(identity)[:24],**identity,'paths':paths,
            'tolerance':max(GEOMETRY_TOL,BRep_Tool.Tolerance_s(face))}


def plane_candidates(shape):
    result={};explorer=TopExp_Explorer(shape,TopAbs_FACE)
    while explorer.More():
        face=TopoDS.Face(explorer.Current());record=_plane(face)
        if record is not None:
            if record['id'] in result:
                raise ReferenceUnavailable('ambiguous_reference','参考面几何身份重复或碰撞，不能猜测对应关系')
            result[record['id']]=(face,record)
        explorer.Next()
    return result


def _intersection(bore, geometry, plane, axes):
    d=geometry['direction'];c=geometry['offset'];n=plane['normal']
    denominator=_dot(n,d)
    omitted=next(i for i in range(3) if i not in axes)
    # 截面和保留的两条坐标还必须能唯一确定Q，避免一条坐标被平面恒等式替代。
    if abs(denominator)<=ANGULAR_LIMIT or abs(n[omitted])<=ANGULAR_LIMIT:
        return None
    s=(plane['constant']-_dot(n,c))/denominator
    if not math.isfinite(s) or not bore.axis_span[0]-GEOMETRY_TOL<=s<=bore.axis_span[1]+GEOMETRY_TOL:
        return None
    point=tuple(a+s*b for a,b in zip(c,d))
    if not all(math.isfinite(v) for v in point):
        return None
    return point


def snapshot_for(model, features, source_sha256, projections=()):
    bores=[f for f in features if isinstance(f,Bore)]
    excluded={p.cbore_id for p in find_counterbores(bores)}
    targets=[b for b in bores if b.feature_id not in excluded and is_oblique(b)]
    if not targets:
        return None
    planes=plane_candidates(model.shape);questions=[];used={};identities=set()
    for bore in targets:
        bore_id,geometry=bore_geometry(bore)
        if bore_id in identities:
            raise ReferenceUnavailable('ambiguous_reference','斜孔几何身份重复或碰撞，不能静默合并')
        identities.add(bore_id)
        axes=tuple(i for i,v in enumerate(geometry['direction']) if abs(v)<0.5)
        if len(axes)!=2:
            raise ReferenceUnavailable('unsupported_reference_position',
                '该斜孔不能按当前两向定位策略完整表达，需要工程师另行制图；未删减定位约束')
        options=[]
        for plane_id,(face,plane) in sorted(planes.items()):
            point=_intersection(bore,geometry,plane,axes)
            if point is None:
                continue
            state=BRepClass_FaceClassifier(face,gp_Pnt(*point),plane['tolerance']).State()
            on_patch=state in (TopAbs_IN,TopAbs_ON)
            options.append({'id':plane_id,'plane_id':plane_id,'point':point,'on_patch':on_patch,
                'label':f"平面参考 · {'交点在有限面片上' if on_patch else '交点在面片外/孔洞内，使用延拓平面'}"})
            used[plane_id]=plane
        if not options:
            raise ReferenceUnavailable('reference_unavailable',
                '斜孔没有可稳定求交并完整表达两向定位的真实平面候选，请工程师处理')
        if len(options)>MAX_OPTIONS:
            raise ReferenceUnavailable('reference_candidates_overflow',
                f'斜孔参考超过{MAX_OPTIONS}项，未截断候选或默认选取，请工程师处理')
        questions.append({'id':'reference-'+bore_id,'bore_id':bore_id,'feature_id':bore.feature_id,
            'target':f'斜孔 ⌀{bore.diameter:g} mm','axes':['xyz'[i] for i in axes],
            'axis_ends':geometry['ends'],'options':options,'allow_unknown':True})
    questions.sort(key=lambda q:q['id'])
    snapshot={'version':BASIS_VERSION,'source_sha256':source_sha256,'questions':questions,
              'planes':{key:used[key] for key in sorted(used)}}
    # feature_id是当前提取的显示关联，不属于可跨枚举顺序复用的身份。
    basis={**snapshot,'questions':[{k:v for k,v in q.items() if k!='feature_id'} for q in questions],
           'planes':{key:{k:v for k,v in record.items() if k!='paths'}
                     for key,record in snapshot['planes'].items()}}
    snapshot['basis_sha']=_hash(basis)
    snapshot['preview']=[{'name':p.direction.name,'basis':view_basis(p.direction),
                         'visible':[curve.sample() for curve in p.visible],
                         'hidden':[curve.sample() for curve in p.hidden]}
                        for p in projections]
    return snapshot


def validate_submission(snapshot, submission):
    if (not isinstance(snapshot,dict) or snapshot.get('version')!=BASIS_VERSION
            or not isinstance(snapshot.get('source_sha256'),str)
            or not isinstance(snapshot.get('basis_sha'),str)
            or not isinstance(snapshot.get('questions'),list) or not snapshot['questions']
            or any(not isinstance(q,dict) or not isinstance(q.get('id'),str)
                   or not isinstance(q.get('options'),list) or not q['options']
                   or len(q['options'])>MAX_OPTIONS
                   or any(not isinstance(o,dict) or not isinstance(o.get('id'),str) for o in q['options'])
                   for q in snapshot['questions'])):
        raise ReferenceInputError('参考快照缺失或损坏，请重新提交','missing_reference',409)
    if (not isinstance(submission,dict) or set(submission)!={'basis_sha','answers'}
            or not isinstance(submission['basis_sha'],str)
            or not isinstance(submission['answers'],(tuple,list))):
        raise ReferenceInputError('仅接受basis_sha和answers，不能输入坐标、数值或向量')
    if submission['basis_sha']!=snapshot['basis_sha']:
        raise ReferenceInputError('来源或参考候选已失效，请重新加载并确认','stale_reference',409)
    questions={q['id']:q for q in snapshot['questions']};accepted={}
    for item in submission['answers']:
        if (not isinstance(item,dict) or not {'question_id','option_id'}<=set(item)
                or set(item)-{'question_id','option_id','free_note'}):
            raise ReferenceInputError('每条参考答案仅接受question_id、option_id和free_note')
        qid=item['question_id'];option=item['option_id'];note=item.get('free_note','')
        if not isinstance(qid,str) or qid not in questions or qid in accepted:
            raise ReferenceInputError('参考问题陌生或重复，不能猜测对应关系')
        if not isinstance(note,str) or len(note)>4096:
            raise ReferenceInputError('备注必须是最多4096字的字符串，且不参与数值计算')
        if option is not None and (not isinstance(option,str) or option not in {o['id'] for o in questions[qid]['options']}):
            raise ReferenceInputError('候选不属于该孔；只接受候选ID或null（不知道）')
        accepted[qid]={'question_id':qid,'option_id':option,'free_note':note}
    if set(accepted)!=set(questions):
        raise ReferenceInputError('请逐孔明确选择，未提交的选择不能当作不知道或默认候选')
    return {'basis_sha':snapshot['basis_sha'],'answers':[accepted[k] for k in sorted(accepted)]}


def resolve(snapshot, submission):
    if snapshot is None:
        if submission:
            raise ReferenceInputError('当前输入没有这些图前参考问题','stale_reference',409)
        return ()
    if submission is None:
        raise ReferenceRequired(snapshot)
    accepted=validate_submission(snapshot,submission)
    if any(a['option_id'] is None for a in accepted['answers']):
        raise ReferenceUnavailable('reference_unconfirmed','工程师无法确认斜孔参考，未生成交付图')
    answers={a['question_id']:a['option_id'] for a in accepted['answers']};facts=[]
    for q in snapshot['questions']:
        chosen=next(o for o in q['options'] if o['id']==answers[q['id']])
        plane=snapshot['planes'][chosen['plane_id']]
        facts.append(ReferenceFact(q['feature_id'],q['bore_id'],plane['id'],tuple(chosen['point']),
            tuple(plane['normal']),plane['constant'],chosen['on_patch'],
            snapshot['source_sha256'],snapshot['basis_sha'],tuple(q['axes'])))
    return tuple(facts)

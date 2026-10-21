"""Bounded facts for a solid, two-stage Z shaft; never infer a generic CAD profile."""
from __future__ import annotations
from dataclasses import dataclass
import math
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepClass import BRepClass_FaceClassifier
from OCP.BRepClass3d import BRepClass3d_SolidClassifier
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.GeomAbs import GeomAbs_Cylinder, GeomAbs_Plane
from OCP.TopAbs import TopAbs_FACE, TopAbs_IN, TopAbs_ON, TopAbs_OUT
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS,TopoDS_Shape
from OCP.gp import gp_Pnt

TOL=1e-6

def close(a,b):return math.isclose(a,b,rel_tol=1e-9,abs_tol=TOL)

@dataclass(frozen=True)
class ShaftSegment:
    diameter: float
    low: float
    high: float

@dataclass(frozen=True)
class SteppedShaftProfile:
    segments: tuple[ShaftSegment,ShaftSegment]
    support_radius: float
    shoulder_area: float

    @property
    def total(self):return self.segments[-1].high

    def supports(self,features):
        from .features import Gap
        result=[]
        for segment in self.segments:
            matches=[g for g in features if isinstance(g,Gap) and g.axis=='z'
                     and close(g.low,segment.low) and close(g.high,segment.high)]
            if len(matches)!=1:raise ValueError('二段轴缺少唯一真实轴向间隙事实')
            g=matches[0]
            result.append((g.feature_id,(self.support_radius,0.,g.low),(self.support_radius,0.,g.high)))
        if len({row[0] for row in result})!=2:raise ValueError('二段轴肩面来源身份冲突')
        return tuple(result)


def two_stage_profile(model):
    """Recognize only two full coaxial cylinders and exactly three axial faces.

    End order, trimmed face support, solid/air samples and volume are measured
    from the source shape. Other parts keep the existing generic policy.
    """
    # Pure scheme callers may provide only bbox/origin; no physical profile can
    # be inferred without an actual source shape. Keep their generic path pure.
    if not isinstance(getattr(model,'shape',None),TopoDS_Shape):return None
    if model.solid_count!=1 or not model.is_valid or not close(model.origin[2],0):return None
    cylinders=[];planes=[]
    exp=TopExp_Explorer(model.shape,TopAbs_FACE)
    while exp.More():
        face=TopoDS.Face(exp.Current());exp.Next();surface=BRepAdaptor_Surface(face)
        if surface.GetType()==GeomAbs_Cylinder:
            cyl=surface.Cylinder();axis=cyl.Axis();d=axis.Direction();p=axis.Location()
            if not (close(d.X(),0) and close(d.Y(),0) and close(abs(d.Z()),1)
                    and close(p.X(),0) and close(p.Y(),0)
                    and close(abs(surface.LastUParameter()-surface.FirstUParameter()),2*math.pi)):return None
            u=(surface.FirstUParameter()+surface.LastUParameter())/2
            low,high=sorted(float(surface.Value(u,v).Z()) for v in (surface.FirstVParameter(),surface.LastVParameter()))
            cylinders.append(ShaftSegment(float(2*cyl.Radius()),low,high))
        elif surface.GetType()==GeomAbs_Plane:
            plane=surface.Plane();n=plane.Axis().Direction()
            if not (close(n.X(),0) and close(n.Y(),0) and close(abs(n.Z()),1)):return None
            props=GProp_GProps();BRepGProp.SurfaceProperties_s(face,props)
            planes.append((float(plane.Location().Z()),float(props.Mass()),face))
        else:return None
    if len(cylinders)!=2 or len(planes)!=3:return None
    segments=tuple(sorted(cylinders,key=lambda s:s.low));a,b=segments
    if not (close(a.low,0) and close(a.high,b.low) and close(b.high,model.bbox[2])
            and a.high>a.low+TOL and b.high>b.low+TOL
            and abs(a.diameter-b.diameter)>TOL):return None
    maxd=max(a.diameter,b.diameter)
    if not all(close(x,y) for x,y in zip(model.bbox,(maxd,maxd,b.high))):return None
    if not close(model.origin[0],-maxd/2) or not close(model.origin[1],-maxd/2):return None
    expected=((0.,math.pi*a.diameter**2/4),(a.high,math.pi*abs(a.diameter**2-b.diameter**2)/4),
              (b.high,math.pi*b.diameter**2/4))
    radius=min(a.diameter,b.diameter)/2
    for z,area in expected:
        matches=[(p,ar,f) for p,ar,f in planes if close(p,z) and close(ar,area)]
        if len(matches)!=1:return None
        if BRepClass_FaceClassifier(matches[0][2],gp_Pnt(radius,0,z),1e-7).State() not in (TopAbs_IN,TopAbs_ON):
            raise ValueError('二段轴真实肩缘支撑不在有限面片上')
    props=GProp_GProps();BRepGProp.VolumeProperties_s(model.shape,props)
    expected_volume=math.pi*(a.diameter**2*(a.high-a.low)+b.diameter**2*(b.high-b.low))/4
    if not close(props.Mass(),expected_volume):return None
    state=lambda point:BRepClass3d_SolidClassifier(model.shape,gp_Pnt(*point),1e-7).State()
    for s in segments:
        z=(s.low+s.high)/2
        if state((0.,0.,z))!=TopAbs_IN or state((s.diameter*.6,0.,z))!=TopAbs_OUT:return None
    return SteppedShaftProfile(segments,radius,expected[1][1])


def shaft_scheme_errors(model,scheme,placed=None):
    """Re-read source geometry so missing shoulder constraints cannot pass vacuously."""
    profile=two_stage_profile(model)
    if profile is None:return []
    from drawform.dimensioning.constraints import Sized,Chain
    supports=profile.supports(scheme.features);ids={row[0] for row in supports}
    expected={row[0]:(row[1],row[2]) for row in supports}
    actual={row[0]:(row[1],row[2]) for row in scheme.shoulder_supports}
    errors=[]
    from .features import Shaft,group_shafts
    groups=group_shafts([f for f in scheme.features if isinstance(f,Shaft)])
    group_ids={g.group_id for g in groups}
    diameter_constraints=[c for c in scheme.plan.constraints if isinstance(c,Sized) and c.dof=='diameter' and c.feature in group_ids]
    source_diameters=sorted(s.diameter for s in profile.segments)
    if len(diameter_constraints)!=2 or any(not close(a,b) for a,b in zip(sorted(c.value for c in diameter_constraints),source_diameters)) or any(c.quantity!=1 for c in diameter_constraints):
        errors.append('二段轴两外径未完整绑定实际外圆')
    totals=[c for c in scheme.plan.constraints if isinstance(c,Sized) and (c.feature,c.dof)==('outline','height')]
    if len(totals)!=1 or not close(totals[0].value,profile.total):errors.append('二段轴总长未绑定真实端面')
    if actual!=expected:errors.append('二段轴肩面三维支撑事实缺失或改变')
    gaps=[c for c in scheme.plan.constraints if isinstance(c,Sized) and c.dof=='gap' and c.feature in ids]
    chains=[c for c in scheme.plan.constraints if isinstance(c,Chain) and len(c.members)==2 and set(c.members)=={(i,'gap') for i in ids}]
    if len(gaps)!=1 or len(chains)!=1:errors.append('二段轴须以总长和唯一实标段长确定肩面')
    elif not close(chains[0].total,profile.total) or len(chains[0].reference)!=1 or chains[0].reference[0] not in chains[0].members or gaps[0].feature==chains[0].reference[0][0]:
        errors.append('二段轴尺寸链总长或参考环不正确')
    for constraint in gaps:
        low,high=expected[constraint.feature]
        if not close(constraint.value,high[2]-low[2]):errors.append('轴向尺寸未绑定真实肩面位置')
    if placed is not None:
        dims=[d for d in placed if d.source and d.source[0] in ids and d.source[1]=='gap']
        if len(dims)!=1:errors.append('二段轴肩面尺寸没有唯一落到图面')
        for dim in dims:
            points=expected[dim.source[0]]
            if len(dim.reference_points_3d)!=2 or any(math.dist(p,q)>TOL for p,q in zip(dim.reference_points_3d,points)):
                errors.append('肩面尺寸未使用真实端面和肩缘支撑点')
    return errors


def centerline_shafts(features):
    """A narrow feature signature; line/circle witnesses are still required by HLR."""
    from .features import Shaft,Gap
    shafts=[f for f in features if isinstance(f,Shaft)]
    gaps=sorted((f for f in features if isinstance(f,Gap)),key=lambda g:g.low)
    if len(shafts)!=2 or len(gaps)!=2 or len(features)!=4:return ()
    if any(g.axis!='z' or not g.solid for g in gaps) or not close(gaps[0].low,0) or not close(gaps[0].high,gaps[1].low):return ()
    if abs(shafts[0].diameter-shafts[1].diameter)<=TOL:return ()
    if any(not close(a,b) for a,b in zip(sorted(s.length for s in shafts),sorted(g.gap for g in gaps))):return ()
    for shaft in shafts:
        d=shaft.axis_direction;p=shaft.axis_point
        if not (close(d[0],0) and close(d[1],0) and close(abs(d[2]),1) and close(p[0],0) and close(p[1],0)):return ()
    return tuple(shafts)


def unique_shaft_gap_ids(features,profile):
    """Keep legacy IDs unless this new profile's two real spans collide."""
    from dataclasses import replace
    import hashlib
    from .features import Gap
    spans={(s.low,s.high) for s in profile.segments}
    gaps=[f for f in features if isinstance(f,Gap) and f.axis=='z'
          and any(close(f.low,low) and close(f.high,high) for low,high in spans)]
    if len({g.feature_id for g in gaps})==len(gaps):return features
    def renamed(gap):
        identity=':'.join((gap.axis,float(gap.low).hex(),float(gap.high).hex()))
        return replace(gap,feature_id='shaft-gap-'+hashlib.sha256(identity.encode()).hexdigest()[:20])
    return tuple(renamed(f) if any(f is g for g in gaps) else f for f in features)

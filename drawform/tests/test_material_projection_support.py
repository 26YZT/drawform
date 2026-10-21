"""投影近似不足时，只能使用经过真实实体边界验证的支撑点。"""
from pathlib import Path
from dataclasses import replace
import math
import pytest
from OCP.BRepClass3d import BRepClass3d_SolidClassifier
from OCP.BRepExtrema import BRepExtrema_IsInFace
from OCP.TopAbs import TopAbs_ON
from OCP.gp import gp_Pnt
from drawform.geometry.loader import import_step
from drawform.geometry.features import extract_features
from drawform.geometry import projection as kernel
from drawform.geometry.projection import FRONT,TOP,LEFT,project_view
from drawform.geometry.layout import layout_views
from drawform.domain.curves import view_basis,to_view
from drawform.dimensioning.scheme import derive_scheme
from drawform.dimensioning.place import place_scheme,place_datum_marks
from drawform.delivery import gate_for,unplaced_dimensions
from drawform.output.paper import prepare_layout,_curve

CORPUS=Path(__file__).resolve().parents[2]/'corpus/cadsy/samples'

def _case(key):
    model=import_step(CORPUS/key/'model.step');scheme=derive_scheme(model,extract_features(model.shape,model.bbox))
    pro={d.name:project_view(model.shape,d) for d in (FRONT,TOP,LEFT)}
    return model,scheme,pro,place_scheme(scheme,pro)


@pytest.mark.parametrize('key',['part_0027','part_0048'])
def test_true_material_points_recover_outline_width_without_rewriting_the_plan(key):
    model,scheme,pro,dims=_case(key)
    dim=next((d for d in dims if d.source==('outline','width')),None)
    assert dim is not None, '不能把HLR近似误差当成真实轮廓测量的缺失'
    plan=next(c for c in scheme.plan.constraints if getattr(c,'feature',None)=='outline' and c.dof=='width')
    assert dim.value==plan.value
    references=getattr(dim,'reference_points_3d',())
    assert len(references)==2
    projected=[]
    for point in references:
        assert BRepClass3d_SolidClassifier(model.shape,gp_Pnt(*point),1e-7).State()==TopAbs_ON
        projected.append(to_view(point,*view_basis(pro[dim.view].direction)))
    assert dim.p1==pytest.approx(projected[0],abs=1e-9)
    assert dim.p2==pytest.approx(projected[1],abs=1e-9)
    assert gate_for(model,scheme,dims).allowed


def test_position_and_datum_reference_the_same_true_material_support():
    model,scheme,pro,dims=_case('part_0048')
    dim=next((d for d in dims if d.kind=='position' and d.axis=='y'),None)
    assert dim is not None
    assert len(dim.reference_points_3d)==1
    point=dim.reference_points_3d[0]
    assert BRepClass3d_SolidClassifier(model.shape,gp_Pnt(*point),1e-7).State()==TopAbs_ON
    mark=next(m for m in place_datum_marks(scheme,pro,dims) if m.letter=='B')
    assert mark.view==dim.view
    assert mark.anchor==pytest.approx(to_view(point,*view_basis(pro[dim.view].direction)),abs=1e-9)
    assert mark.reach==0, '不能把平滑曲面的单点支撑当作可任意滑动的平面'


def test_support_references_do_not_snap_or_replace_the_original_hlr_curves(monkeypatch):
    model=import_step(CORPUS/'part_0048/model.step');original=kernel._clean;captured=[]
    def observe(*args):
        result=original(*args);captured.append(result);return result
    monkeypatch.setattr(kernel,'_clean',observe)
    pro=project_view(model.shape,LEFT)
    assert getattr(pro,'material_supports',()), '应记录真实支撑依据'
    assert (pro.visible,pro.hidden)==captured[0]


def test_exact_analytic_views_keep_their_existing_curve_anchors(plate_step):
    model=import_step(plate_step);scheme=derive_scheme(model,extract_features(model.shape,model.bbox));pro={d.name:project_view(model.shape,d) for d in (FRONT,TOP,LEFT)}
    assert all(not getattr(v,'material_supports',()) for v in pro.values())
    dims=place_scheme(scheme,pro)
    assert all(not getattr(d,'reference_points_3d',()) for d in dims)
    assert gate_for(model,scheme,dims).allowed


class _NoSolution:
    def __init__(self,*args,**kwargs):pass
    def IsDone(self):return False


class _OutsidePoint(_NoSolution):
    def IsDone(self):return True
    def NbSolution(self):return 1
    def SupportTypeShape1(self,index):return BRepExtrema_IsInFace
    def PointOnShape1(self,index):return gp_Pnt(0,0,999)


@pytest.mark.parametrize('solver',[_NoSolution,_OutsidePoint])
def test_unverified_material_support_keeps_the_original_constraint_unplaced(monkeypatch,solver):
    monkeypatch.setattr(kernel,'BRepExtrema_DistShapeShape',solver)
    model,scheme,pro,dims=_case('part_0027')
    assert all(not getattr(v,'material_supports',()) for v in pro.values())
    assert ('outline','width') in unplaced_dimensions(scheme,dims)
    assert not gate_for(model,scheme,dims).allowed


def test_material_support_cannot_cover_a_grossly_incorrect_projection(monkeypatch,plate_step):
    original=kernel._clean
    def oversized(*args):
        v,h=original(*args)
        return tuple(_curve(c,1.1) for c in v),tuple(_curve(c,1.1) for c in h)
    monkeypatch.setattr(kernel,'_clean',oversized)
    model=import_step(plate_step);scheme=derive_scheme(model,extract_features(model.shape,model.bbox));pro={d.name:project_view(model.shape,d) for d in (FRONT,TOP,LEFT)};dims=place_scheme(scheme,pro)
    assert all(not getattr(v,'material_supports',()) for v in pro.values())
    assert ('outline','length') in unplaced_dimensions(scheme,dims)
    assert not gate_for(model,scheme,dims).allowed


def test_paper_scaling_preserves_true_3d_references_and_scales_only_their_projection():
    model,scheme,pro,dims=_case('part_0027')
    dim=next((d for d in dims if d.source==('outline','width')),None)
    assert dim is not None
    layout=prepare_layout(layout_views(tuple(pro.values())),dims,place_datum_marks(scheme,pro,dims),(),'A3','first_angle',.5)
    by={v.projection.direction.name:v.projection for v in layout.views}
    for name,projection in pro.items():
        for before,after in zip(projection.material_supports,by[name].material_supports):
            assert before.lower_3d==after.lower_3d and before.upper_3d==after.upper_3d
            assert after.lower_2d==pytest.approx(tuple(x*.5 for x in before.lower_2d))
            assert after.upper_2d==pytest.approx(tuple(x*.5 for x in before.upper_2d))
    current=next(d for d in layout.model_dimensions if d.source==dim.source)
    assert current.reference_points_3d==dim.reference_points_3d
    assert (current.p1,current.p2,current.value)==(dim.p1,dim.p2,dim.value)


@pytest.mark.parametrize('fault',['nonfinite','different_projection'])
def test_invalid_three_dimensional_reference_facts_are_refused(fault):
    model,scheme,pro,dims=_case('part_0027')
    index=next(i for i,d in enumerate(dims) if d.source==('outline','width'))
    dim=dims[index];references=list(dim.reference_points_3d);point=list(references[0]);point[0]=math.nan if fault=='nonfinite' else point[0]+1;references[0]=tuple(point)
    wrong=replace(dim,reference_points_3d=tuple(references));dims=(*dims[:index],wrong,*dims[index+1:])
    verdict=gate_for(model,scheme,dims)
    assert 'inconsistent_dimensions' in verdict.failed

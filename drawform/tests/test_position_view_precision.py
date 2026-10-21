"""定位视图的基准边界须与精确模型原点一致，不能用离散外形改写方案值。"""
from dataclasses import replace
import pytest
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.gp import gp_Pnt
from drawform.geometry.loader import LoadedModel
from drawform.geometry.features import Bore,extract_features
from drawform.dimensioning.scheme import Scheme,DatumCandidate,derive_scheme
from drawform.dimensioning.constraints import Plan,Sized,Datum
from drawform.dimensioning.place import place_scheme,place_datum_marks
from drawform.domain.curves import FRONT,TOP,LEFT,Polyline2D
from drawform.geometry.projection import Projection
from drawform.delivery import unplaced_dimensions


def _projection(direction,x=0,y=0):
    return Projection(direction,(Polyline2D(((x,y),(10,y),(10,10),(x,10),(x,y))),),())


def _case(axis):
    bore=Bore('bore',2,(5,4,4),(0,1,0) if axis=='z' else (0,0,1),10,True,(0,10))
    value={'x':5,'y':4,'z':4}[axis]
    scheme=Scheme(Plan((Sized('bore',axis,value),),Datum('A','B','C')),(),features=(bore,),origin=(0,0,0))
    pro={d.name:_projection(d) for d in (FRONT,TOP,LEFT)}
    preferred='front' if axis=='z' else 'top';along=1 if axis in ('y','z') else 0
    pro[preferred]=_projection(pro[preferred].direction,**{'y' if along else 'x':.001})
    return scheme,pro


@pytest.mark.parametrize('axis,expected',[('x','front'),('y','left'),('z','left')])
def test_position_and_datum_use_a_parallel_view_with_an_accurate_origin(axis,expected):
    scheme,pro=_case(axis);dim,=place_scheme(scheme,pro)
    assert dim.view==expected
    assert dim.value==pytest.approx(scheme.plan.constraints[0].value,abs=1e-6)
    mark,=place_datum_marks(scheme,pro,(dim,))
    assert mark.view==expected


def test_no_accurate_position_view_retains_the_original_constraint_for_refusal():
    scheme,pro=_case('x');pro['front']=_projection(FRONT,x=.002)
    assert place_scheme(scheme,pro)==()
    assert unplaced_dimensions(scheme,())==[('bore','x')]
    assert scheme.plan.constraints[0].value==5


def test_a_selected_real_internal_datum_is_not_confused_with_the_outer_boundary():
    scheme,pro=_case('x');face=DatumCandidate('inner','x',2,(2,4,4),20,((2,0,0),(2,10,10)))
    scheme=replace(scheme,plan=Plan((Sized('bore','x',3),),Datum('A','B','C')),datum_candidates=(face,),datum_choices=(('A','inner'),))
    dim,=place_scheme(scheme,pro)
    assert dim.p1[0]==2 and dim.p2[0]==5 and dim.value==3


def test_the_derived_scheme_carries_the_exact_model_origin_before_any_answers():
    shape=BRepPrimAPI_MakeBox(gp_Pnt(10,20,30),10,10,10).Shape()
    model=LoadedModel(shape,(10,10,10),(10,20,30))
    scheme=derive_scheme(model,extract_features(shape,model.bbox))
    assert scheme.origin==model.origin

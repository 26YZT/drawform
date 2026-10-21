"""斜孔定位使用方案已采用的真实轴线上点，不换成另一轴向截面。"""
import math
import pytest
from drawform.geometry.features import Bore
from drawform.dimensioning.constraints import Plan,Sized
from drawform.dimensioning.scheme import Scheme
from drawform.dimensioning.place import place_scheme,position_anchors
from drawform.domain.curves import FRONT,TOP,LEFT,Polyline2D
from drawform.geometry.projection import Projection
from drawform.delivery import unplaced_dimensions


def _case(span=(30,40)):
    bore=Bore('oblique',10,(10,20,30),(0,.4,math.sqrt(.84)),10,True,span)
    scheme=Scheme(Plan((Sized('oblique','y',20),)),(),features=(bore,),origin=(0,0,0))
    pro={d.name:Projection(d,(Polyline2D(((0,0),(60,0),(60,60),(0,60),(0,0))),),()) for d in (FRONT,TOP,LEFT)}
    return bore,scheme,pro


def test_oblique_position_uses_the_same_axis_point_as_its_plan():
    bore,scheme,pro=_case()
    dim,=place_scheme(scheme,pro)
    assert position_anchors(scheme)['oblique']==bore.axis_point
    assert dim.value==scheme.plan.constraints[0].value
    assert math.dist(dim.p1,dim.p2)==pytest.approx(20,abs=1e-6)
    assert dim.p2==(10,20)


def test_an_arbitrary_axis_point_outside_the_finite_bore_keeps_the_constraint_unplaced():
    bore,scheme,pro=_case((100,110))
    assert 'oblique' not in position_anchors(scheme)
    assert place_scheme(scheme,pro)==()
    assert unplaced_dimensions(scheme,())==[('oblique','y')]
    assert scheme.plan.constraints[0].value==20


def test_axis_parallel_position_keeps_its_existing_perpendicular_anchor():
    bore=Bore('normal',10,(10,20,300),(0,0,1),10,True,(0,10))
    scheme=Scheme(Plan((Sized('normal','x',10),Sized('normal','y',20))),(),features=(bore,))
    assert position_anchors(scheme)['normal']==(10,20,0)

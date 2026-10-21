"""曲线离散保留真实极值与参数顺序；独立解析几何作数值判据。"""
import math
import numpy as np
import pytest
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
from OCP.Geom import Geom_Ellipse,Geom_BezierCurve
from OCP.collections import Array1_gp_Pnt
from OCP.gp import gp_Ax2,gp_Dir,gp_Pnt
from drawform.geometry.projection import _to_curve
from drawform.domain.curves import Line2D,Polyline2D


def _ellipse(offset,scale,first=0,last=2*math.pi):
    phi=.47;a=20*scale;b=8*scale;cx,cy=offset
    curve=Geom_Ellipse(gp_Ax2(gp_Pnt(cx,cy,0),gp_Dir(0,0,1),gp_Dir(math.cos(phi),math.sin(phi),0)),a,b)
    edge=BRepBuilderAPI_MakeEdge(curve,first,last).Edge()
    points=_to_curve(edge).sample()
    angles=[first,last]
    for base in (math.atan2(-b*math.sin(phi),a*math.cos(phi)),math.atan2(b*math.cos(phi),a*math.sin(phi))):
        angles.extend(base+k*math.pi for k in range(-3,5) if first<=base+k*math.pi<=last)
    exact=[(cx+a*math.cos(t)*math.cos(phi)-b*math.sin(t)*math.sin(phi),cy+a*math.cos(t)*math.sin(phi)+b*math.sin(t)*math.cos(phi)) for t in angles]
    for axis in (0,1):
        assert min(p[axis] for p in points)==pytest.approx(min(p[axis] for p in exact),rel=0,abs=1e-6)
        assert max(p[axis] for p in points)==pytest.approx(max(p[axis] for p in exact),rel=0,abs=1e-6)
    theta=[]
    for x,y in points:
        u=(x-cx)*math.cos(phi)+(y-cy)*math.sin(phi)
        v=-(x-cx)*math.sin(phi)+(y-cy)*math.cos(phi)
        assert (u/a)**2+(v/b)**2==pytest.approx(1,abs=1e-11)
        theta.append(math.atan2(v/b,u/a))
    assert np.all(np.diff(np.unwrap(theta))>=-1e-10), '补点必须沿原参数顺序，不能连出折返'


@pytest.mark.parametrize('offset,scale',[((7,9),1),((10000,-30000),10),((-3,8),.1)])
def test_rotated_ellipse_keeps_exact_extrema_and_its_real_locus(offset,scale):
    _ellipse(offset,scale)


@pytest.mark.parametrize('first,last',[(.13,1.81),(2.0,5.5)])
def test_trimmed_ellipse_adds_only_extrema_inside_its_original_interval(first,last):
    _ellipse((7,9),1,first,last)


def test_cubic_bezier_keeps_both_internal_extrema_in_parameter_order():
    poles=Array1_gp_Pnt(1,4)
    for i,p in enumerate(((0,0,0),(1,3,0),(2,-3,0),(3,0,0)),1):poles.SetValue(i,gp_Pnt(*p))
    curve=Geom_BezierCurve(poles);points=_to_curve(BRepBuilderAPI_MakeEdge(curve).Edge()).sample()
    assert max(y for x,y in points)==pytest.approx(math.sqrt(3)/2,rel=0,abs=1e-6)
    assert min(y for x,y in points)==pytest.approx(-math.sqrt(3)/2,rel=0,abs=1e-6)
    assert [x for x,y in points]==sorted(x for x,y in points)
    for x,y in points:
        t=x/3
        assert y==pytest.approx(9*t-27*t*t+18*t*t*t,abs=1e-10)


def test_an_exact_straight_edge_keeps_its_existing_two_endpoints():
    edge=BRepBuilderAPI_MakeEdge(gp_Pnt(1,2,0),gp_Pnt(7,9,0)).Edge()
    assert _to_curve(edge)==Line2D((1,2),(7,9))

"""拟合与 DXF 只表达原采样轨迹，不能凭端点接近补出整圆或互补弧。"""
import math

import ezdxf
import pytest

from drawform.domain.curves import FRONT, Arc2D, Circle2D, Polyline2D
from drawform.geometry.layout import PlacedView
from drawform.geometry.projection import Projection
from drawform.geometry.refit import refit
from drawform.output.dxf import write_dxf


def _points(angles, radius=10):
    return tuple((7+radius*math.cos(a),9+radius*math.sin(a)) for a in angles)


def test_nist_tiny_open_trace_does_not_invent_a_1900_mm_circle():
    # 用户提供的 NIST CTC04，LEFT 隐藏 B 样条的实际 HLR 采样点。
    points=((728.086534629497,-.38062230642634576),
            (728.08658283815,-.38060233745),
            (728.0866312145798,-.38058229957716594))
    original=Polyline2D(points)
    fitted=refit(original)
    assert isinstance(fitted,Polyline2D)
    assert fitted.points==points


def test_closed_backtracking_arc_is_not_a_full_circle():
    angles=[1+i*.05/20 for i in range(21)]
    points=_points(angles+angles[-2::-1],radius=1000)
    curve=Polyline2D(points)
    assert refit(curve) is curve


def test_open_backtracking_arc_does_not_drop_the_farther_part_of_the_trace():
    angles=[i*math.pi/40 for i in range(21)]+[math.pi/2-i*math.pi/40 for i in range(1,11)]
    curve=Polyline2D(_points(angles))
    assert refit(curve) is curve


@pytest.mark.parametrize('sign', [-1,1])
def test_a_genuine_full_circle_remains_an_analytic_circle(sign):
    fitted=refit(Polyline2D(_points([sign*2*math.pi*i/48 for i in range(49)])))
    assert isinstance(fitted,Circle2D)
    assert fitted.center==pytest.approx((7,9),abs=1e-6)
    assert fitted.radius==pytest.approx(10,abs=1e-6)


@pytest.mark.parametrize('sign', [-1,1])
def test_a_genuine_major_arc_keeps_its_sweep_and_orientation(sign):
    points=_points([math.pi/4+sign*1.5*math.pi*i/72 for i in range(73)])
    fitted=refit(Polyline2D(points))
    assert isinstance(fitted,Arc2D)
    assert fitted.end_angle-fitted.start_angle==pytest.approx(sign*1.5*math.pi,abs=1e-6)


@pytest.mark.parametrize('sweep', [-math.pi/2,-1.5*math.pi,math.pi/2])
def test_native_arc_has_the_same_locus_as_the_engine_trace(tmp_path,sweep):
    arc=Arc2D((7,9),3,math.pi/4,math.pi/4+sweep)
    view=PlacedView(Projection(FRONT,(arc,),()),(0,0))
    path=write_dxf((view,),tmp_path/'arc.dxf')
    native,=ezdxf.readfile(path).modelspace().query('ARC')
    points=[tuple(p)[:2] for p in native.flattening(1e-5)]
    # 对源弧外的一点，最近角必须钉在端点；不能用只看半径的检查放过互补弧。
    for x,y in points:
        angle=math.atan2(y-9,x-7)
        progress=((angle-arc.start_angle) if sweep>0 else (arc.start_angle-angle))%(2*math.pi)
        assert progress<=abs(sweep)+1e-6 or 2*math.pi-progress<1e-6
    actual=math.radians((native.dxf.end_angle-native.dxf.start_angle)%360)
    assert actual==pytest.approx(abs(sweep),abs=1e-9)

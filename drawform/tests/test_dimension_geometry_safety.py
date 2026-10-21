"""轮廓量值用准确视图和真实边界点；短线、箭头同样受图面检查约束。"""
import math

import pytest
from drawform.delivery import unplaced_dimensions
from drawform.dimensioning.constraints import Plan,Sized
from dataclasses import replace
from drawform.dimensioning.place import place_scheme,PlacedDimension,_coalesce_dimensions,_lane_dimensions
from drawform.dimensioning.scheme import Scheme
from drawform.domain.curves import FRONT,TOP,LEFT,Arc2D,Polyline2D
from drawform.geometry.projection import Projection
from drawform.geometry.refit import refit
from drawform.output.collide import _box_crossed_by,text_box
from drawform.output.drawing import Drawing,SheetOverflow,Text,_dimension,_require_inside_frame
from drawform.output.dxf import _emit_dimension,_new_document
from drawform.output.sheet import make_frame


def _rectangle(direction,width,height):
    return Projection(direction,(Polyline2D(((0,0),(width,0),(width,height),(0,height),(0,0))),),())


def _scheme():
    return Scheme(Plan((Sized('outline','length',10),Sized('outline','height',8),Sized('outline','width',6))),())


@pytest.mark.parametrize('dof,preferred,expected', [('length','front','top'),('height','front','left'),('width','top','left')])
def test_an_outline_uses_an_accurate_parallel_view_when_the_preferred_view_misses_an_extremum(dof,preferred,expected):
    pro={'front':_rectangle(FRONT,10,8),'top':_rectangle(TOP,10,6),'left':_rectangle(LEFT,6,8)}
    if dof=='length':pro[preferred]=_rectangle(FRONT,9.999,8)
    elif dof=='height':pro[preferred]=_rectangle(FRONT,10,7.999)
    else:pro[preferred]=_rectangle(TOP,10,5.999)
    dimension=next(d for d in place_scheme(_scheme(),pro) if d.source==('outline',dof))
    assert dimension.view==expected
    assert math.dist(dimension.p1,dimension.p2)==pytest.approx(dimension.value,abs=1e-6)


def test_no_accurate_outline_view_leaves_the_constraint_for_the_existing_delivery_gate_to_refuse():
    pro={'front':_rectangle(FRONT,10,7.999),'top':_rectangle(TOP,10,6),'left':_rectangle(LEFT,6,7.999)}
    scheme=_scheme();dimensions=place_scheme(scheme,pro)
    assert ('outline','height') in unplaced_dimensions(scheme,dimensions)
    assert any(c.feature=='outline' and c.dof=='height' for c in scheme.plan.constraints)


def test_an_accurate_preferred_view_keeps_its_existing_outline_placement():
    pro={'front':_rectangle(FRONT,10,8),'top':_rectangle(TOP,10,6),'left':_rectangle(LEFT,6,8)}
    by={d.source:d for d in place_scheme(_scheme(),pro)}
    assert by[('outline','length')].view=='front'
    assert by[('outline','height')].view=='front'
    assert by[('outline','width')].view=='top'


def test_outline_witnesses_are_real_boundary_extrema_and_native_measurement_uses_the_axis():
    points=((0,2),(10,5),(3,10),(0,2))
    scheme=Scheme(Plan((Sized('outline','length',10),Sized('outline','height',8))),())
    dims=place_scheme(scheme,{'front':Projection(FRONT,(Polyline2D(points),),())})
    assert len(dims)==2
    doc=_new_document()
    for dim in dims:
        assert dim.p1 in points and dim.p2 in points
        _emit_dimension(doc.modelspace(),dim,(0,0))
        line=_dimension(dim,(0,0))[0][0]
        assert math.dist(*line)==pytest.approx(dim.value,abs=1e-9)
    assert [d.get_measurement() for d in doc.modelspace().query('DIMENSION')]==pytest.approx([10,8])


@pytest.mark.parametrize('start,end', [(.1,2.1),(2.1,.1),(.1,5.9),(5.9,.1)])
def test_arc_sampling_keeps_exact_cardinal_extrema_without_changing_sweep(start,end):
    arc=Arc2D((7,9),3,start,end);points=arc.sample()
    assert points[0]==pytest.approx((7+3*math.cos(start),9+3*math.sin(start)))
    assert points[-1]==pytest.approx((7+3*math.cos(end),9+3*math.sin(end)))
    assert max(y for x,y in points)==pytest.approx(12,abs=1e-12)
    if min(start,end)<math.pi<max(start,end):assert min(x for x,y in points)==pytest.approx(4,abs=1e-12)


def test_a_nearly_circular_ellipse_is_not_refitted_to_a_false_circle():
    points=tuple((10*math.cos(i*2*math.pi/48),10.0005*math.sin(i*2*math.pi/48)) for i in range(49))
    original=Polyline2D(points)
    assert refit(original) is original


def test_a_foreign_short_line_entirely_inside_a_text_box_is_still_a_collision():
    box=text_box(Text('123',100,100,'middle'))
    assert _box_crossed_by(box,(99,100),(101,100))


def test_an_arrow_with_an_inside_tip_and_outside_tail_is_refused():
    drawing=Drawing('A3',(),(((26,100),(24,99),(24,101)),),())
    with pytest.raises(SheetOverflow):_require_inside_frame(drawing,make_frame('A3'))


def test_different_oblique_measurement_directions_do_not_share_one_depth_annotation():
    a=PlacedDimension('depth','front',(100,200),(112,216),(120,208),'↧20',20,source=('a','depth'))
    b=replace(a,p2=(88,216),source=('b','depth'))
    scheme=Scheme(Plan((Sized('a','depth',20),Sized('b','depth',20))),())
    assert len(_coalesce_dimensions((a,b),scheme))==2


def test_axis_measured_outline_with_non_aligned_witnesses_reserves_a_real_dimension_lane():
    outline=PlacedDimension('linear','front',(0,2),(3,10),(-8,10),'8',8,
                            source=('outline','height'),measurement_axis=(0,1))
    position=PlacedDimension('position','front',(5,2),(5,10),(-8,6),'8',8,
                             axis='z',source=('other','z'))
    projection=Projection(FRONT,(Polyline2D(((0,2),(10,5),(3,10),(0,2))),),())
    a,b=_lane_dimensions((outline,position),{'front':projection})
    assert a.base[0]!=b.base[0]
    assert (a.p1,a.p2,a.value)==(outline.p1,outline.p2,outline.value)

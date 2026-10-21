"""视图间距须包含相邻尺寸带和基准框，同时保持正投影对齐。"""
from dataclasses import replace
import pytest
from drawform.domain.curves import FRONT,TOP,LEFT,Polyline2D
from drawform.geometry.projection import Projection
from drawform.geometry.layout import layout_views
from drawform.dimensioning.place import PlacedDimension
from drawform.output.drawing import DatumMark,_dimension,datum_points
from drawform.output.paper import prepare_layout
from drawform.output.collide import _box_crossed_by


def _views(mode):
    projections=tuple(Projection(d,(Polyline2D(((0,0),(10,0),(10,10),(0,10),(0,0))),),()) for d in (FRONT,TOP,LEFT))
    return layout_views(projections,mode=mode)


@pytest.mark.parametrize('mode',['first_angle','third_angle'])
@pytest.mark.parametrize('factor',[1,.5])
def test_adjacent_dimension_bands_and_datum_letters_get_separate_view_space(mode,factor):
    right=mode=='first_angle'
    dim=PlacedDimension('linear','front',(10,0),(10,10),(18 if right else -8,5),'10',10,source=('outline','height'))
    mark=DatumMark('left',(0 if right else 10,5),(-1,0) if right else (1,0),'B',standoff=7)
    layout=prepare_layout(_views(mode),(dim,),(mark,),(),'A3',mode,factor)
    by={v.projection.direction.name:v for v in layout.views};ox,oy=by['left'].origin
    corners=datum_points(layout.datums[0].anchor,mark.outward,7)[3]
    box=(min(x+ox for x,y in corners),min(y+oy for x,y in corners),max(x+ox for x,y in corners),max(y+oy for x,y in corners))
    lines,heads,texts=_dimension(layout.dimensions[0],by['front'].origin)
    assert not any(_box_crossed_by(box,a,b) for line in lines for a,b in zip(line,line[1:])), '相邻视图的尺寸线穿过基准框'
    assert by['front'].origin[0]==pytest.approx(by['top'].origin[0])
    assert by['front'].origin[1]==pytest.approx(by['left'].origin[1])
    original=layout.model_dimensions[0]
    assert (original.p1,original.p2,original.value,original.source)==(dim.p1,dim.p2,dim.value,dim.source)


def test_already_separated_bare_views_keep_their_existing_relative_origins():
    views=_views('first_angle');layout=prepare_layout(views,(),(),(),'A3','first_angle',1)
    def relative(items):
        front=next(v for v in items if v.projection.direction.name=='front')
        return [(v.origin[0]-front.origin[0],v.origin[1]-front.origin[1]) for v in items]
    assert relative(views)==relative(layout.views)

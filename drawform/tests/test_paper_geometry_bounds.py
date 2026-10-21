"""居中范围必须包含实际绘出的中心线端部。"""
import pytest
from drawform.domain.curves import FRONT,Line2D,Centerline
from drawform.geometry.layout import PlacedView
from drawform.geometry.projection import Projection
from drawform.output.sheet import Frame,center_on_frame,content_box


@pytest.mark.parametrize('line,expected',[(Centerline((-3,5),(23,5)),(-3,0,23,10)),(Centerline((5,-3),(5,23)),(0,-3,10,23))])
def test_centering_accounts_for_centerline_overshoot(line,expected):
    projection=Projection(FRONT,(Line2D((0,0),(10,10)),),())
    views=(PlacedView(projection,(0,0),centerlines=(line,)),)
    assert content_box(views)==expected
    frame=Frame('A3',(100,100),(0,0,30,30))
    moved=center_on_frame(views,frame)
    ox,oy=moved[0].origin
    assert all(frame.contains((ox+x,oy+y,ox+x,oy+y)) for x,y in line.sample())
    x0,y0,x1,y1=content_box(moved)
    assert x0==pytest.approx(30-x1)
    assert y0==pytest.approx(30-y1)

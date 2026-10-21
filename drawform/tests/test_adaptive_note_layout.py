from dataclasses import replace
import pytest
from drawform.output.paper_notes import required_height,other_labels,tolerance_labels,NotesOverflow,width_of
from drawform.output.sheet import make_frame
from drawform.output.titleblock import BLOCK_H,BLOCK_W
from drawform.output.paper import prepare_layout,_clear_datum_sides
from drawform.output.drawing import DatumMark,datum_anchor_slide,_dimension
from drawform.domain.curves import FRONT,TOP,LEFT,Polyline2D
from drawform.geometry.projection import Projection
from drawform.geometry.layout import layout_views
from drawform.dimensioning.place import PlacedDimension

def test_long_notes_reserve_height_without_changing_any_word_or_size():
    inner=make_frame('A2').inner;notes=tuple(f'R{i:02}：1*X={i}.123456789012345' for i in range(14))
    height=required_height(notes,inner,'一般公差 ISO 2768-m')
    labels=other_labels(notes,inner,'一般公差 ISO 2768-m')
    assert height>BLOCK_H and [row[0] for row in labels][1:]==list(notes)
    assert all(row[3]==3.5 for row in labels)
    assert all(x+width_of(text,h)<=inner[2]-BLOCK_W-5 for text,x,y,h in labels)
    assert min(y-h for text,x,y,h in labels)>=max(y+h for text,x,y,h in tolerance_labels('一般公差 ISO 2768-m',inner))

def test_already_fitting_note_panel_keeps_its_height():
    inner=make_frame('A3').inner
    assert required_height(('视图按第一角画法',),inner,'一般公差 ISO 2768-m')==BLOCK_H

def test_note_panel_exceeding_the_whole_page_still_refuses():
    with pytest.raises(NotesOverflow):other_labels(tuple(f'说明{i}' for i in range(100)),make_frame('A2').inner)

@pytest.mark.parametrize('factor',[1,.5])
def test_body_at_unit_or_reduced_scale_respects_the_actual_note_height(factor):
    projections=tuple(Projection(d,(Polyline2D(((0,0),(20,0),(20,20),(0,20),(0,0))),),()) for d in (FRONT,TOP,LEFT))
    notes=tuple(f'注记{i}' for i in range(15));inner=make_frame('A2').inner
    layout=prepare_layout(layout_views(projections),(),(),(),'A2','first_angle',factor,notes=notes)
    assert layout.content_frame.inner[1]>=inner[1]+required_height(notes,inner)+5
    assert all(y+v.origin[1]>=layout.content_frame.inner[1] for v in layout.views for c in v.projection.visible for x,y in c.sample())

def test_dimension_fan_can_change_side_while_datum_stays_on_the_same_face():
    projection=Projection(TOP,(Polyline2D(((0,0),(40,0),(40,40),(0,40),(0,0))),),())
    dims=tuple(PlacedDimension('linear','top',(0,0),(0,length),(-8*(i+1),length/2),str(length),length,source=(str(i),'gap'))
               for i,length in enumerate((25,30,35,40)))
    mark=DatumMark('top',(0,20),(-1,0),'A',standoff=40,reach=3)
    assert datum_anchor_slide(mark,[line for d in dims for line in _dimension(d,(0,0))[0]]) is None
    moved,marks,changed=_clear_datum_sides(dims,(mark,),{'top':projection})
    assert changed and marks[0].anchor==mark.anchor and marks[0].outward==mark.outward
    assert all(replace(after,base=before.base)==before for before,after in zip(dims,moved))
    assert all(d.base[0]>40 for d in moved)
    assert datum_anchor_slide(marks[0],[line for d in moved for line in _dimension(d,(0,0))[0]]) is not None


def test_moving_a_band_never_overlays_an_existing_opposite_dimension():
    projection=Projection(TOP,(Polyline2D(((0,0),(10,0),(10,10),(0,10),(0,0))),),())
    left=PlacedDimension('linear','top',(0,0),(0,10),(-8,5),'10',10,source=('height','length'))
    right=replace(left,kind='depth',base=(18,5),source=('hole','depth'))
    mark=DatumMark('top',(0,5),(-1,0),'A',standoff=20,reach=0)
    moved,marks,changed=_clear_datum_sides((left,right),(mark,),{'top':projection})
    assert moved==(left,right) and marks==(mark,) and not changed

"""Independent interval and reachable-label counterexamples; no changed geometry."""
from dataclasses import replace
import itertools
import pytest
from drawform.domain.curves import FRONT,Polyline2D
from drawform.geometry.projection import Projection
from drawform.dimensioning.place import PlacedDimension,_lane_dimensions
from drawform.output.annotation_layout import settle_annotations,annotation_box,_route_clear
from drawform.output.drawing import Text,DrawingItem
from drawform.output.sheet import Frame

def dimensions(spans,vertical=False):
    result=[]
    for i,(lo,hi) in enumerate(spans):
        a,b=(lo,0),(hi,0);base=((lo+hi)/2,-8*(i+1))
        if vertical:a,b,base=(a[1],a[0]),(b[1],b[0]),(base[1],base[0])
        result.append(PlacedDimension('linear','front',a,b,base,str(hi-lo),hi-lo,
                                      source=(str(i),'gap')))
    return tuple(result)

def projection():
    return {'front':Projection(FRONT,(Polyline2D(((0,0),(140,0),(140,140),(0,140),(0,0))),),())}

@pytest.mark.parametrize('vertical',[False,True])
def test_crossing_intervals_share_two_lanes_without_altering_the_dimension(vertical):
    dims=dimensions(((0,20),(90,120),(10,50),(40,100)),vertical)
    result=_lane_dimensions(dims,projection());axis=0 if vertical else 1
    assert len({d.base[axis] for d in result})==2
    assert max(-d.base[axis] for d in result)==16
    for before,after in zip(dims,result):assert replace(after,base=before.base)==before
    for a,b in itertools.combinations(result,2):
        if a.base[axis]==b.base[axis]:
            av=sorted((a.p1[1-axis],a.p2[1-axis]));bv=sorted((b.p1[1-axis],b.p2[1-axis]))
            assert av[1]<=bv[0] or bv[1]<=av[0]

def test_compaction_is_independent_of_input_order():
    dims=dimensions(((0,20),(90,120),(10,50),(40,100)))
    layouts=[{d.source:d.base for d in _lane_dimensions(order,projection())} for order in itertools.permutations(dims)]
    assert all(layout==layouts[0] for layout in layouts)

def test_true_nested_dimensions_keep_short_inside_long_outside():
    dims=dimensions(((0,100),(10,80),(20,30)))
    result=_lane_dimensions(dims,projection())
    assert [d.base[1] for d in result]==[-24,-16,-8]

def test_already_optimal_lanes_are_not_rearranged():
    dims=dimensions(((0,20),(40,60),(0,100)))
    result=_lane_dimensions(dims,projection())
    assert [d.base[1] for d in result]==[-8,-8,-16]

def test_diagonal_and_radial_dimensions_keep_their_existing_placement():
    linear=replace(dimensions(((0,20),))[0],p2=(20,20),base=(-8,8),value=20*2**.5)
    radial=replace(linear,kind='diameter',base=None,source=('other','diameter'))
    assert _lane_dimensions((linear,radial),projection())==(linear,radial)

def test_a_clear_but_unreachable_label_slot_does_not_hide_a_reachable_slot():
    owner="dim:('hole', 'diameter')"
    dim=PlacedDimension('diameter','front',(35,50),(37,50),None,'⌀4',4,source=('hole','diameter'))
    texts=(Text('⌀4',60,50,'middle',owner=owner),
           Text('W'*40,50,50,'middle',rotate=90,owner='wall',height=5),
           Text('block',60,50,'middle',owner='other'))
    items=(DrawingItem(owner,'DIM',((37,50),(43,50))),)
    frame=Frame('test',(100,100),(0,0,100,100))
    result,runs=settle_annotations(texts,items,(dim,),{'front':(0,0)},frame)
    assert result[0].x<45 and result[0].content==texts[0].content
    assert result[1:]==list(texts[1:])
    route=next(item.points for item in runs if item.owner==owner)
    assert route[0]==dim.p2
    assert _route_clear(route,[annotation_box(t) for t in result])

def test_a_fixed_label_blocking_the_arrow_exit_is_not_crossed_by_the_prefix():
    from drawform.output.drawing import SheetOverflow
    owner="dim:('hole', 'diameter')"
    dim=PlacedDimension('diameter','front',(35,50),(37,50),None,'⌀4',4,source=('hole','diameter'))
    texts=(Text('⌀4',60,50,'middle',owner=owner),
           Text('block',60,50,'middle',owner='other'),
           Text('.',38.5,50,'middle',owner='fixed',height=1))
    with pytest.raises(SheetOverflow,match='箭头出口'):
        settle_annotations(texts,(DrawingItem(owner,'DIM',((37,50),(43,50))),),(dim,),
                           {'front':(0,0)},Frame('test',(100,100),(0,0,100,100)),fixed={'fixed'})

def test_fixed_labels_are_not_moved_by_route_recovery():
    owner="dim:('hole', 'diameter')"
    dim=PlacedDimension('diameter','front',(35,50),(37,50),None,'⌀4',4,source=('hole','diameter'))
    texts=(Text('⌀4',60,50,'middle',owner=owner),)
    items=(DrawingItem(owner,'DIM',((37,50),(43,50))),)
    result,runs=settle_annotations(texts,items,(dim,),{'front':(0,0)},Frame('test',(100,100),(0,0,100,100)),fixed={owner})
    assert result==list(texts) and runs==list(items)

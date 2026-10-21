"""有限图框内，密集文字之间允许多次转弯；封死的路径仍拒绝。"""
from drawform.output.annotation_layout import leader_route,_route_clear
from drawform.output.sheet import Frame


def _frame():
    return Frame('A3',(100,100),(0,0,100,100))


def test_a_leader_can_follow_a_winding_corridor_with_more_than_three_segments():
    anchor=(5,50);box=(92,47,98,53)
    obstacles=((20,0,30,75),(45,25,55,100),(70,0,80,75))
    run=leader_route(anchor,box,obstacles,_frame())
    assert run is not None
    assert run[0]==anchor
    assert len(run)>4
    assert _route_clear(run,[box,*obstacles])
    assert all(_frame().contains((*p,*p)) for p in run)


def test_a_wall_across_the_whole_frame_still_prevents_a_leader():
    assert leader_route((5,50),(92,47,98,53),((40,0,50,100),),_frame()) is None


def test_an_anchor_inside_foreign_text_cannot_be_routed_through_that_text():
    assert leader_route((5,50),(92,47,98,53),((4,49,6,51),),_frame()) is None


def test_the_existing_clear_direct_route_is_preserved():
    assert leader_route((5,50),(92,47,98,53),(),_frame())==((5,50),(90.5,50))


def test_a_segment_along_the_edge_of_foreign_text_is_not_a_clear_corridor():
    assert not _route_clear(((5,0),(90,0)),((40,0,50,100),))

"""Equal nominal diameters do not identify the same physical cylindrical feature."""
from dataclasses import replace
import math
import pytest
from drawform.delivery import DeliveryBlocked
from drawform.dimensioning.constraints import Plan,Sized
from drawform.dimensioning.scheme import Scheme
from drawform.dimensioning.place import place_scheme,diameter_source_errors
from drawform.domain.curves import FRONT,TOP,LEFT,Circle2D,Arc2D,to_view,view_basis
from drawform.geometry.features import Bore,group_bores
from drawform.geometry.projection import Projection
from drawform import pipeline

def scheme_for(bores):
    return Scheme(Plan(tuple(Sized(g.group_id,'diameter',g.diameter,quantity=g.count)
                             for g in group_bores(bores))),(),features=tuple(bores))

def test_same_radius_on_another_axis_cannot_steal_the_diameter_label():
    a=Bore('a',20,(30,-125,-25),(0,1,0),5)
    b=Bore('b',20,(-110,-95,0),(0,0,1),10)
    front=to_view(a.axis_point,*view_basis(FRONT));top=to_view(b.axis_point,*view_basis(TOP))
    projections={'top':Projection(TOP,(Circle2D(top,10),),()),'front':Projection(FRONT,(Circle2D(front,10),),())}
    scheme=scheme_for((a,b));dims=place_scheme(scheme,projections)
    assert len(dims)==2
    first=next(d for d in dims if d.source[0]=='group-a')
    assert first.view=='front' and first.p1==front
    assert not diameter_source_errors(scheme,dims)

def test_same_axis_direction_but_different_hole_centers_stay_bound_to_their_groups():
    a=Bore('a',10,(0,0,0),(0,0,1),5)
    b=Bore('b',10,(40,20,0),(0,0,1),8)
    centers=[to_view(x.axis_point,*view_basis(TOP)) for x in (a,b)]
    projection={'top':Projection(TOP,tuple(Circle2D(c,5) for c in centers),())}
    dims=place_scheme(scheme_for((a,b)),projection)
    assert {d.source[0]:d.p1 for d in dims}=={'group-a':centers[0],'group-b':centers[1]}

def test_any_real_member_of_the_same_group_can_supply_the_circle():
    bores=(Bore('a',10,(0,0,0),(0,0,1),5),Bore('b',10,(40,20,0),(0,0,1),5))
    center=to_view(bores[1].axis_point,*view_basis(TOP))
    scheme=scheme_for(bores);dims=place_scheme(scheme,{'top':Projection(TOP,(Circle2D(center,5),),())})
    assert len(dims)==1 and dims[0].p1==center and dims[0].text=='2×⌀10'
    assert not diameter_source_errors(scheme,dims)

@pytest.mark.parametrize('start,end',[(math.pi,1.5*math.pi),(1.5*math.pi,math.pi)])
def test_partial_arc_arrow_stays_on_the_actual_arc(start,end):
    bore=Bore('a',10,(0,0,0),(0,0,1),5)
    dims=place_scheme(scheme_for((bore,)),{'top':Projection(TOP,(Arc2D((0,0),5,start,end),),())})
    assert len(dims)==1 and dims[0].p2[0]<0 and dims[0].p2[1]<0
    assert math.dist(dims[0].p1,dims[0].p2)==pytest.approx(5)

def test_absent_own_geometry_does_not_fall_back_to_an_unrelated_circle():
    bore=Bore('a',10,(40,20,0),(0,0,1),5)
    assert place_scheme(scheme_for((bore,)),{'top':Projection(TOP,(Circle2D((0,0),5),),())})==()

@pytest.mark.parametrize('reverse',[False,True])
def test_normal_bore_cannot_borrow_a_same_size_counterbore_member(reverse):
    from drawform.dimensioning.place import _diameter_members,_group_members
    a=Bore('normal',20,(40,0,0),(0,0,1),5,False,(5,10),10)
    c=Bore('counter',20,(0,0,0),(0,0,1),5,False,(5,10),10,
           shoulder_neighbors=(('pilot',),()),topology_checked=True)
    pilot=Bore('pilot',10,(0,0,0),(0,0,1),5,True,(0,5))
    bores=(a,c,pilot) if not reverse else (c,a,pilot)
    assert _diameter_members(bores)['group-normal']==(a,)
    assert _group_members(bores)['group-normal'].feature_id=='normal'
    scheme=Scheme(Plan((Sized('group-normal','diameter',20),)),(),features=bores)
    assert place_scheme(scheme,{'top':Projection(TOP,(Circle2D((0,0),10),),())})==()


def test_source_gate_rejects_wrong_circle_even_when_native_measurement_is_unchanged(bearing_seat_step,tmp_path,monkeypatch):
    original=pipeline.place_scheme
    def wrong(*args,**kwargs):
        dims=list(original(*args,**kwargs));index=next(i for i,d in enumerate(dims) if d.kind=='diameter')
        d=dims[index];dims[index]=replace(d,p1=(d.p1[0]+17,d.p1[1]),p2=(d.p2[0]+17,d.p2[1]))
        return tuple(dims)
    monkeypatch.setattr(pipeline,'place_scheme',wrong)
    with pytest.raises(DeliveryBlocked) as error:pipeline.render_part(bearing_seat_step,tmp_path)
    assert 'inconsistent_dimensions' in error.value.failed
    assert not list(tmp_path.iterdir())

"""尺寸只调整呈现：共享来源完整，字位无堆叠，原生测量数值不变。"""
from dataclasses import replace
import math

import ezdxf
import pytest

from drawform.delivery import unplaced_dimensions
from drawform.dimensioning.constraints import Plan, Sized
from drawform.dimensioning.place import PlacedDimension, _coalesce_dimensions
from drawform.dimensioning.scheme import Scheme
from drawform.geometry.features import Bore
from drawform.output.annotation_layout import settle_annotations
from drawform.output.collide import find_collisions
from drawform.output.drawing import Drawing, DrawingItem, Text
from drawform.output.sheet import make_frame
from drawform.pipeline import render_part


def _duplicates():
    constraints = tuple(Sized(f'bore-{i}', 'x', 5.25) for i in range(3))
    dims = tuple(PlacedDimension('position', 'top', (0, 0), (5.25, 0), (0, -8),
                                  '5.25', 5.25, axis='x', source=(c.feature,c.dof)) for c in constraints)
    return Scheme(Plan(constraints), ()), dims


def test_shared_position_keeps_every_source_and_exact_value():
    scheme, dims = _duplicates()
    result = _coalesce_dimensions(dims, scheme)
    assert len(result) == 1 and result[0].value == 5.25
    assert set((result[0].source, *result[0].shared_sources)) == {d.source for d in dims}
    assert not unplaced_dimensions(scheme, result)
    assert len(scheme.plan.constraints) == 3


@pytest.mark.parametrize('difference', ['value', 'quantity', 'tolerance', 'geometry'])
def test_equal_looking_but_distinct_requirements_never_merge(difference):
    scheme, dims = _duplicates()
    changed = scheme.plan.constraints[1]
    if difference == 'geometry':
        dims = (dims[0], replace(dims[1], p1=(1, 0), p2=(6.25, 0)), dims[2])
    elif difference == 'value':
        changed = replace(changed, value=5.2500001)
        dims = (dims[0], replace(dims[1], value=changed.value), dims[2])
    else:
        changed = replace(changed, **{difference: 2 if difference == 'quantity' else 'H7'})
    scheme = replace(scheme, plan=replace(scheme.plan, constraints=(scheme.plan.constraints[0], changed, scheme.plan.constraints[2])))
    assert len(_coalesce_dimensions(dims, scheme)) == 2


def test_concentric_bore_segments_can_share_but_a_shaft_cannot():
    bores = tuple(Bore(f'bore-{i}', 4.224, (0,0,0), (0,0,1), i+1, True) for i in range(2))
    keys = [('group-bore-0','diameter'), ('group-bore-1','diameter'), ('shaft-group-0','diameter')]
    scheme = Scheme(Plan(tuple(Sized(*key, 4.224) for key in keys)), (), features=bores)
    dims = tuple(PlacedDimension('diameter','top',(0,0),(2.112,0),None,'⌀4.224',4.224,source=key) for key in keys)
    result = _coalesce_dimensions(dims, scheme)
    assert len(result) == 2 and result[0].shared_sources == (keys[1],)
    assert not unplaced_dimensions(scheme, result)


def test_crowded_text_moves_with_a_leader_without_changing_measurement():
    owner = "dim:('hole', 'diameter')"
    dim = PlacedDimension('diameter','top',(100,100),(102,100),None,'⌀4',4,source=('hole','diameter'))
    texts = (Text('⌀4',105,100,'middle',owner=owner),Text('8.5',110,100,'middle',owner='dim:other'))
    items = (DrawingItem(owner,'DIM',((102,100),(107,100))),
             DrawingItem('view:top','VISIBLE',((98,98),(110,98),(110,103),(98,103),(98,98))))
    moved, runs = settle_annotations(texts,items,(dim,),{'top':(0,0)},make_frame())
    assert moved[0] != texts[0] and moved[0].content == texts[0].content
    leader = next(item for item in runs if item.owner == owner)
    assert leader.points[0] == dim.p2 and len(leader.points) >= 3
    assert not [hit for hit in find_collisions(Drawing('A3',(),(),tuple(moved),tuple(runs))) if hit.left == owner and hit.kind.startswith('text/')]
    assert dim.value == 4 and dim.p1 == (100,100) and dim.p2 == (102,100)


def test_pipeline_dxf_retains_native_measurements_after_layout(bearing_seat_step, tmp_path):
    part = render_part(bearing_seat_step,tmp_path)
    doc = ezdxf.readfile(dict(part.files)['dxf'])
    entities = list(doc.modelspace().query('DIMENSION'))
    assert len(entities) == len(part.dimensions) and not doc.audit().errors
    for entity, dim in zip(entities, part.dimensions):
        expected = (dim.value if dim.kind in ('diameter','pcd') else math.dist(dim.p1,dim.p2))
        assert entity.get_measurement() == pytest.approx(expected, abs=1e-6)
        assert entity.dxf.text == dim.text
        assert len(doc.blocks[entity.dxf.geometry].query('MTEXT')) == 1


def test_baseline_ascenders_clear_a_line_above_the_old_centered_box():
    owner = "dim:('hole', 'diameter')"
    dim = PlacedDimension('diameter','top',(100,90),(102,90),None,'⌀4',4,source=('hole','diameter'))
    text = Text('⌀4',110,100,'middle',owner=owner)
    outline = DrawingItem('view:top','VISIBLE',((100,102.5),(120,102.5)))
    moved, _ = settle_annotations((text,),(outline,),(dim,),{'top':(0,0)},make_frame())
    assert moved[0] != text and moved[0].content == text.content


def test_diameter_leader_does_not_cross_its_own_text():
    from drawform.output.drawing import _dimension
    from drawform.output.annotation_layout import annotation_box, _route_clear
    dim = PlacedDimension('diameter','top',(0,0),(2,0),None,'⌀4',4)
    lines, _, texts = _dimension(dim,(100,100))
    assert all(_route_clear(line,[annotation_box(texts[0])]) for line in lines)


def test_leader_uses_another_edge_when_left_landing_is_occupied():
    from drawform.output.annotation_layout import leader_route, _route_clear
    box = (189,123,192,129)
    neighbor = (180,123,188,129)
    route = leader_route((199,124),box,[neighbor],make_frame())
    assert route is not None and route[0] == (199,124)
    assert _route_clear(route,[box,neighbor])


def test_aligned_holes_share_one_position_and_keep_all_witness_points():
    from drawform.output.drawing import _dimension
    scheme,dims = _duplicates()
    dims = (dims[0],replace(dims[1],p1=(0,10),p2=(5.25,10)),dims[2])
    result = _coalesce_dimensions(dims,scheme)
    assert len(result) == 1 and (dims[1].p1,dims[1].p2) in result[0].shared_witnesses
    lines,_,_ = _dimension(result[0],(0,0))
    assert any(run[0][1] > 10 and run[1][1] < 0 for run in lines)
    assert not unplaced_dimensions(scheme,result)


def test_datum_letter_stays_in_its_own_box_after_collision_avoidance(bearing_seat_step):
    from tests.test_svg import _placed
    from drawform.dimensioning.scheme import derive_scheme
    from drawform.dimensioning.place import place_scheme,place_datum_marks
    from drawform.output.drawing import build_drawing
    model,features,projections,views = _placed(bearing_seat_step)
    scheme = derive_scheme(model,features)
    dims = place_scheme(scheme,projections)
    drawing = build_drawing(views,dimensions=dims,datum_marks=place_datum_marks(scheme,projections,dims))
    for text in drawing.texts:
        if not text.owner.startswith('datum:'):
            continue
        box = next(item.points for item in drawing.items if item.owner == text.owner and len(item.points) == 5)
        assert min(p[0] for p in box) < text.x < max(p[0] for p in box)
        assert min(p[1] for p in box) < text.y < max(p[1] for p in box)


def test_unchanged_datum_keeps_the_whole_reference_group(bearing_seat_step):
    from tests.test_svg import _placed
    from drawform.dimensioning.scheme import derive_scheme
    from drawform.dimensioning.place import place_scheme,place_datum_marks
    from drawform.output.drawing import build_drawing
    model,features,projections,views = _placed(bearing_seat_step)
    scheme = derive_scheme(model,features)
    dims = place_scheme(scheme,projections)
    marks = place_datum_marks(scheme,projections,dims)
    reference = build_drawing(views,dimensions=dims,datum_marks=marks)
    # 模拟排版后的尺寸引线占据旧代号滑动路径；公差回答不能再重算未改基准的滑动。
    leader = next(item for item in reference.items if item.owner == 'datum:A' and len(item.points) == 2)
    x = sum(p[0] for p in leader.points)/2
    y = leader.points[0][1]
    occupied = replace(reference,items=reference.items+(DrawingItem('dim:routed','DIM',((x,y-10),(x,y+10))),))
    current = build_drawing(views,dimensions=dims,datum_marks=marks,layout_reference=occupied)
    assert [i for i in current.items if i.layer == 'DATUM'] == [i for i in reference.items if i.layer == 'DATUM']
    assert [t for t in current.texts if t.owner.startswith('datum:')] == [t for t in reference.texts if t.owner.startswith('datum:')]


def test_float_fingerprint_sharing_preserves_each_original_value_and_point():
    scheme,dims = _duplicates()
    tail = math.nextafter(dims[1].value, math.inf)
    dims = (dims[0],replace(dims[1],value=tail,p2=(tail,0)),dims[2])
    constraints = tuple(replace(c,value=tail) if i==1 else c for i,c in enumerate(scheme.plan.constraints))
    scheme = replace(scheme,plan=replace(scheme.plan,constraints=constraints))
    result = _coalesce_dimensions(dims,scheme)
    assert len(result) == 1 and result[0].text == '5.25'
    assert result[0].shared_values == (tail,5.25)
    assert result[0].shared_witnesses[0][1] == (tail,0)
    assert scheme.plan.constraints[1].value == tail

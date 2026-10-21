"""斜孔没有正视投影圆时，直径只能锚定真实孔壁与引擎轴线。"""
import math
from dataclasses import replace

import pytest

from drawform.delivery import unplaced_dimensions
from drawform.dimensioning.constraints import Plan, Sized
from drawform.dimensioning.place import place_scheme
from drawform.dimensioning.scheme import Scheme
from drawform.domain.curves import FRONT, TOP, LEFT, Circle2D, Line2D, to_view, view_basis
from drawform.geometry.features import Bore, group_bores
from drawform.geometry.projection import Projection


def _case(wall_radius=6.0, axial_range=(-18, -2)):
    bore = Bore('oblique', 12.0, (95, 193, -52), (0, -math.sin(.4), math.cos(.4)),
                20, True, (-20, 0))
    group = group_bores((bore,))[0]
    constraint = Sized(group.group_id, 'diameter', 12.0)
    scheme = Scheme(Plan((constraint,)), (), features=(bore,))
    projections = {}
    walls = []
    for direction in (TOP, FRONT, LEFT):
        basis = view_basis(direction)
        start, end = [to_view(bore.point_at(t), *basis) for t in axial_range]
        dx, dy = end[0]-start[0], end[1]-start[1]
        span = math.hypot(dx, dy)
        nx, ny = -dy/span, dx/span
        segments = tuple(Line2D((start[0]+sign*nx*wall_radius, start[1]+sign*ny*wall_radius),
                                (end[0]+sign*nx*wall_radius, end[1]+sign*ny*wall_radius))
                         for sign in (1, -1))
        walls.extend((direction.name, line) for line in segments)
        projections[direction.name] = Projection(direction, segments, ())
    return scheme, projections, walls


def test_oblique_diameter_keeps_value_and_uses_a_real_projected_wall():
    scheme, projections, walls = _case()
    dimensions = place_scheme(scheme, projections)
    assert len(dimensions) == 1, '斜孔直径不能因没有 Circle2D 就漏掉'
    dim = dimensions[0]
    assert dim.value == 12 and dim.source == (scheme.plan.constraints[0].feature, 'diameter')
    assert math.dist(dim.p1, dim.p2)*2 == pytest.approx(12, abs=1e-8)
    candidates = [line for view, line in walls if view == dim.view]
    assert any(math.dist(line.start, dim.p2)+math.dist(dim.p2, line.end)
               == pytest.approx(math.dist(line.start,line.end), abs=1e-8) for line in candidates)
    assert not unplaced_dimensions(scheme, dimensions)


@pytest.mark.parametrize('radius,span', [(6.05, (-18, -2)), (6, (40, 60))])
def test_unrelated_or_outside_walls_do_not_supply_a_diameter(radius, span):
    scheme, projections, _ = _case(radius, span)
    assert place_scheme(scheme, projections) == ()
    assert unplaced_dimensions(scheme, ())


def test_adding_a_wall_annotation_keeps_the_existing_circle_witness_point():
    scheme, projections, _ = _case()
    from drawform.geometry.features import Shaft
    scheme = replace(scheme, features=(*scheme.features, Shaft('shaft',14,(40,0,0),(0,0,1),10)))
    scheme = replace(scheme, plan=Plan((*scheme.plan.constraints, Sized('shaft', 'diameter', 14))))
    projections['top'] = replace(projections['top'], visible=(*projections['top'].visible, Circle2D((40, 0), 7)))
    dimensions = place_scheme(scheme, projections)
    shaft = next(d for d in dimensions if d.source == ('shaft', 'diameter'))
    assert shaft.p2 == pytest.approx((40+7/math.sqrt(2), 7/math.sqrt(2)))


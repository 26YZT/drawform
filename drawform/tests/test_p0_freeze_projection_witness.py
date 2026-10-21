"""The independent delivery gate consumes the original finite projected curve."""
from dataclasses import replace
import math

import pytest
from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder

from drawform import pipeline
from drawform.delivery import DeliveryBlocked
from drawform.dimensioning.constraints import Plan, Sized
from drawform.dimensioning.place import PlacedDimension, diameter_source_errors, place_scheme
from drawform.dimensioning.scheme import Scheme
from drawform.domain.curves import Arc2D, Circle2D, TOP, to_view, view_basis
from drawform.geometry.features import Shaft
from drawform.geometry.projection import Projection
from tests.conftest import _write_step
from tests.test_oblique_bore_placement import _case


def _shaft_case(curve, angle):
    feature = Shaft('shaft', 10, (0, 0, 0), (0, 0, 1), 10)
    scheme = Scheme(Plan((Sized('shaft', 'diameter', 10),)), (), features=(feature,))
    dim = PlacedDimension('diameter', 'top', (0, 0),
                          (5 * math.cos(angle), 5 * math.sin(angle)), None, '⌀10', 10,
                          source=('shaft', 'diameter'))
    return scheme, dim, {'top': Projection(TOP, (curve,), ())}


@pytest.mark.parametrize('start,end', [(math.pi, 1.5 * math.pi), (1.5 * math.pi, math.pi),
                                      (1.5 * math.pi, 2.5 * math.pi), (2.5 * math.pi, 1.5 * math.pi),
                                      (-.25 * math.pi, .25 * math.pi), (7 * math.pi, 7.5 * math.pi)])
def test_finite_arc_interior_and_endpoints_pass_but_complement_is_refused(start, end):
    middle = (start + end) / 2
    for angle in (start, middle, end):
        scheme, dim, projections = _shaft_case(Arc2D((0, 0), 5, start, end), angle)
        assert not diameter_source_errors(scheme, (dim,), projections)
    scheme, dim, projections = _shaft_case(Arc2D((0, 0), 5, start, end), middle + math.pi)
    assert diameter_source_errors(scheme, (dim,), projections)
    assert not diameter_source_errors(scheme, (dim,)), 'legacy source-only callers remain compatible'


def test_hidden_circle_is_a_valid_witness_and_missing_or_wrong_view_is_not():
    scheme, dim, projections = _shaft_case(Circle2D((0, 0), 5), .3)
    projections['top'] = replace(projections['top'], visible=(), hidden=projections['top'].visible)
    assert not diameter_source_errors(scheme, (dim,), projections)
    assert diameter_source_errors(scheme, (dim,), {})
    assert diameter_source_errors(scheme, (dim,), {'top': replace(projections['top'], hidden=())})


def test_shared_sources_each_require_their_own_finite_projection_witness():
    scheme, dim, projections = _shaft_case(Circle2D((0, 0), 5), 0)
    second = Shaft('other', 10, (30, 0, 0), (0, 0, 1), 10)
    scheme = replace(scheme, features=(*scheme.features, second))
    dim = replace(dim, shared_sources=(('other', 'diameter'),), shared_values=(10,),
                  shared_witnesses=(((30, 0), (35, 0)),))
    projections['top'] = replace(projections['top'], visible=(Circle2D((0, 0), 5),
                                                            Arc2D((30, 0), 5, math.pi, 1.5 * math.pi)))
    assert diameter_source_errors(scheme, (dim,), projections)
    projections['top'] = replace(projections['top'], visible=(*projections['top'].visible,
                                                            Circle2D((30, 0), 5)))
    assert not diameter_source_errors(scheme, (dim,), projections)


def test_oblique_bore_real_side_wall_passes_but_same_radius_opposite_air_does_not():
    scheme, projections, _ = _case()
    dim, = place_scheme(scheme, projections)
    assert not diameter_source_errors(scheme, (dim,), projections)
    direction = to_view(scheme.features[0].canonical_axis(), *view_basis(projections[dim.view].direction))
    norm = math.hypot(*direction)
    unit = (direction[0] / norm, direction[1] / norm)
    # Move along the real cylinder interval, past the finite projected wall.
    moved = replace(dim, p1=tuple(p + 9 * u for p, u in zip(dim.p1, unit)),
                    p2=tuple(p + 9 * u for p, u in zip(dim.p2, unit)))
    assert not diameter_source_errors(scheme, (moved,))
    assert diameter_source_errors(scheme, (moved,), projections)
    # One real wall is sufficient; reflecting its arrow into the absent side is not.
    visible = projections[dim.view].visible
    keep = tuple(line for line in visible if math.dist(line.start, dim.p2) + math.dist(dim.p2, line.end)
                 == pytest.approx(math.dist(line.start, line.end), abs=1e-8))
    one_side = {dim.view: replace(projections[dim.view], visible=keep, hidden=())}
    assert not diameter_source_errors(scheme, (dim,), one_side)
    reflected = replace(dim, p2=tuple(2 * a - b for a, b in zip(dim.p1, dim.p2)))
    assert not diameter_source_errors(scheme, (reflected,))
    assert diameter_source_errors(scheme, (reflected,), one_side)


def test_pipeline_rejects_an_arrow_outside_the_actual_arc_even_with_correct_radius(tmp_path, monkeypatch):
    step = _write_step(BRepPrimAPI_MakeCylinder(5, 10).Shape(), tmp_path / 'shaft.step')
    output = tmp_path / 'out'
    output.mkdir()
    original_project, original_place = pipeline.project_view, pipeline.place_scheme
    changed = []

    def partial(shape, direction):
        projection = original_project(shape, direction)
        def trim(curves):
            return tuple(Arc2D(c.center, c.radius, math.pi, 1.5 * math.pi)
                         if isinstance(c, Circle2D) else c for c in curves)
        return replace(projection, visible=trim(projection.visible), hidden=trim(projection.hidden))

    def wrong(scheme, projections):
        dimensions = list(original_place(scheme, projections))
        for index, dimension in enumerate(dimensions):
            if dimension.kind == 'diameter':
                changed.append(dimension)
                radius = math.dist(dimension.p1, dimension.p2)
                dimensions[index] = replace(dimension, p2=(dimension.p1[0] + radius, dimension.p1[1]))
        return tuple(dimensions)

    monkeypatch.setattr(pipeline, 'project_view', partial)
    monkeypatch.setattr(pipeline, 'place_scheme', wrong)
    with pytest.raises(DeliveryBlocked) as error:
        pipeline.render_part(step, output)
    assert changed, 'fault injection must actually reach a diameter'
    assert 'inconsistent_dimensions' in error.value.failed
    assert list(output.iterdir()) == []

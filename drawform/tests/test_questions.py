"""P0.1：问题结构、封闭候选与几何指纹。现有测试保持原样。"""

from dataclasses import FrozenInstanceError, fields
import importlib
import importlib.util

import pytest


def _api():
    name = "drawform.service.questions"
    assert importlib.util.find_spec(name) is not None, "缺少 P0.1 问题结构"
    return importlib.import_module(name)


def _question(api, **changes):
    values = dict(id="q-test", kind=api.SPECIAL_TOLERANCE, target="测试尺寸",
                  claim="未注专门公差", basis="无依据", impact="验收无专门依据",
                  options=(api.Option("current", "保持未注", None),))
    values.update(changes)
    return api.Question(**values)


def test_question_and_option_are_frozen_dataclasses():
    api = _api()
    question = _question(api)
    assert question.allow_unknown is True
    assert question.options[0].note == ""
    with pytest.raises(FrozenInstanceError):
        question.kind = "other"
    with pytest.raises(FrozenInstanceError):
        question.options[0].value = 123.0


@pytest.mark.parametrize("kind", ["special_tolerance", "datum_choice", "unnoted_angle"])
def test_only_the_three_specified_kinds_are_supported(kind):
    api = _api()
    assert set(api.KINDS) == {"special_tolerance", "datum_choice", "unnoted_angle"}
    assert _question(api, kind=kind).kind == kind


def test_unknown_kind_is_rejected():
    api = _api()
    with pytest.raises(AssertionError):
        _question(api, kind="free_number")


def test_empty_candidates_are_rejected():
    api = _api()
    with pytest.raises(AssertionError):
        _question(api, options=())


def test_duplicate_option_ids_are_rejected():
    api = _api()
    with pytest.raises(AssertionError):
        _question(api, options=(api.Option("same", "甲", None),
                                api.Option("same", "乙", None)))


def test_a_single_current_option_is_valid():
    api = _api()
    assert len(_question(api).options) == 1


def test_unknown_must_always_be_allowed():
    api = _api()
    with pytest.raises(AssertionError):
        _question(api, allow_unknown=False)


def test_datum_options_cannot_carry_a_number():
    api = _api()
    with pytest.raises(AssertionError):
        _question(api, kind=api.DATUM_CHOICE,
                  options=(api.Option("face-test", "面", 1.0),))


def test_question_has_no_field_for_a_human_number():
    api = _api()
    question = _question(api)
    assert "value" not in {field.name for field in fields(question)}
    assert not any(isinstance(getattr(question, field.name), float)
                   for field in fields(question))


def _topology(shape, kind):
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    cast = TopoDS.Face if kind == TopAbs_FACE else TopoDS.Edge
    explorer = TopExp_Explorer(shape, kind)
    items = []
    while explorer.More():
        items.append(cast(explorer.Current()))
        explorer.Next()
    return items


def _planar_topology(shape):
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Plane
    from OCP.TopAbs import TopAbs_FACE

    return [face for face in _topology(shape, TopAbs_FACE)
            if BRepAdaptor_Surface(face).GetType() == GeomAbs_Plane]


def test_face_ids_repeat_across_independent_step_reads(bearing_seat_step):
    api = _api()
    assert callable(getattr(api, "_face_id", None)), "缺少稳定面指纹"
    from drawform.geometry.loader import import_step

    first = _planar_topology(import_step(bearing_seat_step).shape)
    second = _planar_topology(import_step(bearing_seat_step).shape)
    assert [api._face_id(face) for face in first] == [api._face_id(face) for face in second]
    assert len({api._face_id(face) for face in first}) == len(first)


def test_face_and_edge_ids_survive_step_save_and_reopen(bearing_seat_step, tmp_path):
    api = _api()
    assert callable(getattr(api, "_edge_id", None)), "缺少稳定边指纹"
    from OCP.TopAbs import TopAbs_EDGE
    from drawform.geometry.loader import import_step
    from tests.conftest import _write_step

    original = import_step(bearing_seat_step).shape
    reopened = import_step(_write_step(original, tmp_path / "saved.step")).shape
    assert sorted(api._face_id(f) for f in _planar_topology(original)) == sorted(
        api._face_id(f) for f in _planar_topology(reopened))
    assert sorted(api._edge_id(e) for e in _topology(original, TopAbs_EDGE)) == sorted(
        api._edge_id(e) for e in _topology(reopened, TopAbs_EDGE))


def test_face_id_ignores_topology_orientation(plate_step):
    api = _api()
    assert callable(getattr(api, "_face_id", None)), "缺少稳定面指纹"
    from OCP.TopoDS import TopoDS
    from drawform.geometry.loader import import_step

    face = _planar_topology(import_step(plate_step).shape)[0]
    assert api._face_id(face) == api._face_id(TopoDS.Face(face.Reversed()))


def _rectangle_face(width, height, x=0.0):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakePolygon
    from OCP.gp import gp_Pnt

    polygon = BRepBuilderAPI_MakePolygon()
    for px, py in ((-width / 2, -height / 2), (width / 2, -height / 2),
                   (width / 2, height / 2), (-width / 2, height / 2)):
        polygon.Add(gp_Pnt(px + x, py, 0))
    polygon.Close()
    return BRepBuilderAPI_MakeFace(polygon.Wire()).Face()


def test_coplanar_equal_area_faces_at_different_locations_have_different_ids():
    api = _api()
    assert callable(getattr(api, "_face_id", None)), "缺少稳定面指纹"
    assert api._face_id(_rectangle_face(10, 6)) != api._face_id(_rectangle_face(10, 6, x=20))


def test_equal_area_equal_centroid_faces_with_different_boundaries_do_not_merge():
    api = _api()
    assert callable(getattr(api, "_face_id", None)), "缺少稳定面指纹"
    assert api._face_id(_rectangle_face(10, 6)) != api._face_id(_rectangle_face(15, 4))


def test_edge_id_ignores_endpoint_and_topology_order():
    api = _api()
    assert callable(getattr(api, "_edge_id", None)), "缺少稳定边指纹"
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
    from OCP.TopoDS import TopoDS
    from OCP.gp import gp_Pnt

    first = BRepBuilderAPI_MakeEdge(gp_Pnt(0, 0, 0), gp_Pnt(10, 2, 3)).Edge()
    second = BRepBuilderAPI_MakeEdge(gp_Pnt(10, 2, 3), gp_Pnt(0, 0, 0)).Edge()
    assert api._edge_id(first) == api._edge_id(second)
    assert api._edge_id(first) == api._edge_id(TopoDS.Edge(first.Reversed()))


def test_opposite_arcs_with_the_same_endpoints_have_different_ids():
    api = _api()
    assert callable(getattr(api, "_edge_id", None)), "缺少稳定边指纹"
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
    from OCP.GC import GC_MakeArcOfCircle
    from OCP.gp import gp_Pnt

    edges = [BRepBuilderAPI_MakeEdge(GC_MakeArcOfCircle(
        gp_Pnt(-1, 0, 0), gp_Pnt(0, y, 0), gp_Pnt(1, 0, 0)).Value()).Edge()
        for y in (-1, 1)]
    assert api._edge_id(edges[0]) != api._edge_id(edges[1])


def _derived(step):
    from drawform.dimensioning.scheme import derive_scheme
    from drawform.geometry.features import extract_features
    from drawform.geometry.loader import import_step

    model = import_step(step)
    features = extract_features(model.shape, model.bbox)
    return model, features, derive_scheme(model, features)


def _enumerator():
    api = _api()
    assert callable(getattr(api, "questions_for", None)), "缺少三类候选枚举"
    return api


def _linear_scheme(value, grade="ISO 2768-v", tolerance=None):
    from drawform.dimensioning.constraints import Plan, Sized
    from drawform.dimensioning.scheme import Scheme

    return Scheme(plan=Plan((Sized("outline", "length", value, tolerance=tolerance),),
                            general_tolerance=grade), unknowns=(("outline", "length"),))


def test_a_real_half_millimetre_part_asks_for_special_tolerance(tmp_path):
    api = _enumerator()
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from tests.conftest import _write_step

    model, features, scheme = _derived(_write_step(
        BRepPrimAPI_MakeBox(80.0, 50.0, 0.5).Shape(), tmp_path / "thin.step"))
    questions = api.questions_for(model, features, scheme)
    special = [q for q in questions if q.kind == api.SPECIAL_TOLERANCE]
    assert len(special) == 1
    assert "0.5" in special[0].target
    assert special[0].options and special[0].allow_unknown


def test_a_blank_general_tolerance_cell_enumerates_all_verified_fit_codes():
    api = _enumerator()
    from drawform.rules import iso286

    questions = api.special_tolerance_questions(_linear_scheme(2.0))
    assert len(questions) == 1
    options = questions[0].options
    expected = {f"{letter}{grade}" for letter in iso286.HOLE_LETTERS | iso286.SHAFT_LETTERS
                for grade in iso286.IT_GRADES}
    assert {o.id for o in options} == expected, "不得按常用程度或孔/轴身份预筛"
    for option in options:
        upper, lower = iso286.fit_for(option.id, 2.0)
        assert option.value is None, "单个 value 不代表上下偏差，按审核决定留空"
        assert f"上偏差 {upper:+g} mm" in option.note
        assert f"下偏差 {lower:+g} mm" in option.note


@pytest.mark.parametrize("value", [0.1, 0.5, 4000.1])
def test_a_size_outside_the_verified_fit_table_keeps_one_explicit_current_option(value):
    api = _enumerator()
    question, = api.special_tolerance_questions(_linear_scheme(value))
    assert len(question.options) == 1
    option = question.options[0]
    assert option.id == "current"
    assert option.value is None
    assert "未注" in option.label and "未核对" in option.note
    assert not question.truncated, "表没有值和候选超限是不同事实"


@pytest.mark.parametrize("value,tolerance", [(20.0, None), (0.0, None), (0.5, "H7")])
def test_a_covered_coincident_or_explicit_dimension_does_not_ask_for_special_tolerance(
        value, tolerance):
    api = _enumerator()
    assert api.special_tolerance_questions(
        _linear_scheme(value, grade="ISO 2768-m", tolerance=tolerance)) == ()


def test_datum_candidates_include_every_engine_plane_without_axis_prefilter(plate_step):
    api = _enumerator()
    from drawform.geometry.features import planar_faces

    model, features, scheme = _derived(plate_step)
    questions = api.datum_choice_questions(scheme, model, features)
    assert questions
    expected = {api._face_id(f) for f in _planar_topology(model.shape)}
    assert len(expected) == len(planar_faces(model.shape))
    for question in questions:
        assert question.kind == api.DATUM_CHOICE
        assert {o.id for o in question.options} == expected
        assert all(o.value is None for o in question.options)
        assert not question.truncated


def test_datum_candidates_ignore_planar_face_enumeration_order(plate_step, monkeypatch):
    api = _enumerator()
    model, features, scheme = _derived(plate_step)
    first = api.questions_for(model, features, scheme)
    enumerate_faces = api.planar_faces
    monkeypatch.setattr(api, "planar_faces", lambda shape: tuple(reversed(enumerate_faces(shape))))
    assert api.questions_for(model, features, scheme) == first


def test_no_plane_keeps_the_current_bbox_datum_explicit(plate_step, monkeypatch):
    api = _enumerator()
    model, features, scheme = _derived(plate_step)
    monkeypatch.setattr(api, "planar_faces", lambda shape: ())
    questions = api.datum_choice_questions(scheme, model, features)
    assert questions
    for question in questions:
        assert len(question.options) == 1
        assert question.options[0].value is None
        assert "占位" in question.options[0].label


def test_a_plan_without_used_position_dimensions_does_not_ask_for_a_datum(plate_step):
    api = _enumerator()
    model, features, _ = _derived(plate_step)
    assert api.datum_choice_questions(_linear_scheme(20.0), model, features) == ()


def _angle_scheme(tolerance=None, general="ISO 2768-m"):
    from drawform.dimensioning.constraints import Datum, Plan, Sized
    from drawform.dimensioning.scheme import Scheme

    return Scheme(plan=Plan((Sized("edge-geometric-target", "angle", 45.0,
                                   tolerance=tolerance),), datum=Datum("A"),
                            general_tolerance=general),
                  unknowns=(("edge-geometric-target", "angle"),))


@pytest.mark.parametrize("general", ["ISO 2768-m", None])
def test_unnoted_angles_offer_exactly_the_fixed_closed_set(general):
    api = _enumerator()
    question, = api.unnoted_angle_questions(_angle_scheme(general=general))
    assert question.kind == api.UNNOTED_ANGLE
    assert {o.id: o.value for o in question.options} == {
        "none": None, "pm0.5": 0.5, "pm1": 1.0, "pm2": 2.0}
    assert next(o for o in question.options if o.id == "none").label == "不给"
    assert question.allow_unknown is True


def test_an_explicitly_toleranced_angle_is_not_asked_again():
    api = _enumerator()
    assert api.unnoted_angle_questions(_angle_scheme(tolerance="±1°")) == ()


def test_candidate_overflow_is_reported_and_deterministic(plate_step, monkeypatch, caplog):
    api = _enumerator()
    model, features, scheme = _derived(plate_step)
    monkeypatch.setattr(api, "MAX_OPTIONS", 2)
    first = api.questions_for(model, features, scheme)
    assert first and all(q.truncated for q in first)
    assert all(len(q.options) == 2 for q in first)
    assert "截断" in caplog.text
    enumerate_faces = api.planar_faces
    monkeypatch.setattr(api, "planar_faces", lambda shape: tuple(reversed(enumerate_faces(shape))))
    assert api.questions_for(model, features, scheme) == first


def test_exactly_the_candidate_limit_is_not_truncation(plate_step, monkeypatch):
    api = _enumerator()
    from drawform.geometry.features import planar_faces

    model, features, scheme = _derived(plate_step)
    original = {q.id: q for q in api.questions_for(model, features, scheme)}
    monkeypatch.setattr(api, "MAX_OPTIONS", len(planar_faces(model.shape)))
    questions = api.questions_for(model, features, scheme)
    assert all(not q.truncated for q in questions if q.kind == api.DATUM_CHOICE)
    fits = [q for q in questions if q.kind == api.SPECIAL_TOLERANCE]
    assert fits
    for question in fits:
        assert question.options == original[question.id].options[:api.MAX_OPTIONS]
        assert question.truncated == (len(original[question.id].options) > api.MAX_OPTIONS)


def test_fit_candidate_overflow_is_also_reported(monkeypatch, caplog):
    api = _enumerator()
    monkeypatch.setattr(api, "MAX_OPTIONS", 3)
    question, = api.special_tolerance_questions(_linear_scheme(2.0))
    assert question.truncated
    assert len(question.options) == 3
    assert "截断" in caplog.text


def test_question_order_and_ids_are_stable_when_constraints_are_shuffled(plate_step):
    api = _enumerator()
    from dataclasses import replace

    model, features, scheme = _derived(plate_step)
    first = api.questions_for(model, features, scheme)
    shuffled = replace(scheme, plan=replace(scheme.plan,
                                           constraints=tuple(reversed(scheme.plan.constraints))))
    second = api.questions_for(model, features, shuffled)
    assert first == second
    assert [(q.kind, q.target) for q in first] == sorted((q.kind, q.target) for q in first)


def test_all_question_and_option_ids_survive_step_save_and_reopen(bearing_seat_step, tmp_path):
    api = _enumerator()
    from tests.conftest import _write_step

    model, features, scheme = _derived(bearing_seat_step)
    first = api.questions_for(model, features, scheme)
    reopened = _derived(_write_step(model.shape, tmp_path / "saved-questions.step"))
    assert api.questions_for(*reopened) == first


def test_grouped_dimension_ids_do_not_follow_the_first_enumerated_member(plate_step):
    api = _enumerator()
    from drawform.dimensioning.scheme import derive_scheme
    from drawform.geometry.features import Bore

    model, _, _ = _derived(plate_step)
    bores = tuple(Bore(f"temporary-enumeration-{i}", diameter=0.5,
                       axis_point=(x, 25.0, 0.0), axis_direction=(0.0, 0.0, 1.0),
                       depth=12.0, through=True, axis_span=(0.0, 12.0))
                  for i, x in enumerate((20.0, 60.0)))
    first = api.questions_for(model, bores, derive_scheme(model, bores))
    reversed_bores = tuple(reversed(bores))
    second = api.questions_for(model, reversed_bores, derive_scheme(model, reversed_bores))
    assert first == second
    assert any(q.kind == api.SPECIAL_TOLERANCE for q in first)


@pytest.mark.parametrize("impact", ["", "   "])
def test_an_empty_impact_is_rejected(impact):
    api = _api()
    with pytest.raises(AssertionError):
        _question(api, impact=impact)


@pytest.mark.parametrize("kind,consequence", [
    ("special_tolerance", "公差"), ("datum_choice", "定位"), ("unnoted_angle", "角度")])
def test_each_kind_explains_its_consequence(plate_step, kind, consequence):
    api = _enumerator()
    model, features, scheme = _derived(plate_step)
    questions = {api.SPECIAL_TOLERANCE: api.special_tolerance_questions(_linear_scheme(0.5)),
                 api.DATUM_CHOICE: api.datum_choice_questions(scheme, model, features),
                 api.UNNOTED_ANGLE: api.unnoted_angle_questions(_angle_scheme())}[kind]
    assert questions
    assert all(q.impact.strip() and consequence in q.impact for q in questions)


@pytest.mark.parametrize("kind", ["special_tolerance", "unnoted_angle"])
def test_tolerance_questions_consume_the_existing_assumption_inference(kind, monkeypatch):
    api = _enumerator()
    from drawform.output import assumptions

    infer = assumptions._tolerance
    calls = []

    def observed(model, scheme):
        calls.append(scheme)
        fact = infer(model, scheme)
        return {**fact, "claim": "共享公差事实", "basis": "共享公差依据",
                "impact": "共享公差后果"}

    monkeypatch.setattr(assumptions, "_tolerance", observed)
    scheme = _linear_scheme(0.5) if kind == api.SPECIAL_TOLERANCE else _angle_scheme()
    function = (api.special_tolerance_questions if kind == api.SPECIAL_TOLERANCE
                else api.unnoted_angle_questions)
    questions = function(scheme)
    assert calls, "不得复制 _tolerance 的另一份推断"
    assert all("共享公差事实" in q.claim and "共享公差依据" in q.basis
               and "共享公差后果" in q.impact for q in questions)


def test_datum_questions_consume_the_existing_assumption_inference(plate_step, monkeypatch):
    api = _enumerator()
    from drawform.output import assumptions

    model, features, scheme = _derived(plate_step)
    infer = assumptions._datum_faces
    calls = []

    def observed(model, scheme, dimensions=None):
        calls.append(scheme)
        fact = infer(model, scheme, dimensions)
        return {**fact, "claim": "共享基准事实", "basis": "共享基准依据",
                "impact": "共享基准后果"}

    monkeypatch.setattr(assumptions, "_datum_faces", observed)
    questions = api.datum_choice_questions(scheme, model, features)
    assert calls, "不得复制 _datum_faces 的另一份推断"
    assert all("共享基准事实" in q.claim and "共享基准依据" in q.basis
               and "共享基准后果" in q.impact for q in questions)
    monkeypatch.setattr(assumptions, "_datum_faces", lambda *args: None)
    assert api.datum_choice_questions(scheme, model, features) == ()


def test_datum_questions_only_include_axes_actually_placed_on_the_drawing(plate_step):
    api = _enumerator()
    import inspect
    assert "dimensions" in inspect.signature(api.questions_for).parameters, "未接实际落位事实"
    from drawform.dimensioning.place import place_scheme
    from drawform.geometry.projection import STANDARD_VIEWS, project_view

    model, features, scheme = _derived(plate_step)
    projections = {d.name: project_view(model.shape, d) for d in STANDARD_VIEWS}
    dimensions = place_scheme(scheme, projections)
    only_x = tuple(d for d in dimensions if d.kind == "position" and d.axis == "x")
    assert only_x
    all_questions = api.questions_for(model, features, scheme, dimensions=only_x)
    questions = tuple(q for q in all_questions if q.kind == api.DATUM_CHOICE)
    assert len(questions) == 1
    assert questions[0].target.startswith("基准 A")
    without_dimensions = api.questions_for(model, features, scheme, dimensions=())
    assert not any(q.kind == api.DATUM_CHOICE for q in without_dimensions)
    assert any(q.kind == api.SPECIAL_TOLERANCE for q in without_dimensions)

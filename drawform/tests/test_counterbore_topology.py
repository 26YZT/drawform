"""GEO-019: independent solid constructions, topology, and output/guard checks."""
from dataclasses import replace
from pathlib import Path
import json
import math
import xml.etree.ElementTree as ET

import ezdxf
import pytest
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCP.BRep import BRep_Builder
from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder, BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCone
from OCP.gp import gp_Ax1, gp_Ax2, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec
from OCP.GeomAbs import GeomAbs_Circle, GeomAbs_Cone, GeomAbs_Plane
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_VERTEX
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS

from drawform import pipeline
from drawform.delivery import DeliveryBlocked, gate_for
from drawform.dimensioning.constraints import Sized
from drawform.dimensioning.scheme import derive_scheme
from drawform.geometry import features as feature_module
from drawform.geometry.bore_topology import BoreTopologyError, _shoulder_parameter
from drawform.geometry.features import Bore, extract_features, find_counterbores, group_bores
from drawform.geometry.loader import import_step
from drawform.service.questions import _feature_key
from tests.conftest import _write_step

CORPUS = Path(__file__).resolve().parents[2] / "corpus/cadsy/samples"


def cylinder(radius, low, high):
    return BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(0, 0, low), gp_Dir(0, 0, 1)),
                                    radius, high-low).Shape()


def part(kind):
    body = cylinder(17, 0, 52)
    pilot = cylinder(3, -1 if kind != "blind_pilot" else 20, 53)
    body = BRepAlgoAPI_Cut(body, pilot).Shape()
    wide = cylinder(11, 8 if kind == "enlargement" else 43, 43 if kind == "enlargement" else 53)
    return BRepAlgoAPI_Cut(body, wide).Shape()


def load_shape(tmp_path, shape, factor=1, angle=0):
    scale = gp_Trsf(); scale.SetScale(gp_Pnt(0, 0, 0), factor)
    shape = BRepBuilderAPI_Transform(shape, scale, True).Shape()
    rotation = gp_Trsf(); rotation.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(1, 2, 3)), angle)
    shape = BRepBuilderAPI_Transform(shape, rotation, True).Shape()
    shift = gp_Trsf(); shift.SetTranslation(gp_Vec(101, -73, 39))
    shape = BRepBuilderAPI_Transform(shape, shift, True).Shape()
    step = _write_step(shape, tmp_path / "independent.step")
    model = import_step(step)
    assert model.is_valid and model.solid_count == 1
    features = extract_features(model.shape, model.bbox)
    return step, model, features


@pytest.mark.parametrize("factor", [.4, 1, 2.5])
@pytest.mark.parametrize("angle", [0, .47])
def test_internal_enlargement_is_recognized_from_both_actual_shoulders(tmp_path, factor, angle):
    _, model, features = load_shape(tmp_path, part("enlargement"), factor, angle)
    bores = [b for b in features if isinstance(b, Bore)]
    wide, = [b for b in bores if b.diameter == pytest.approx(22*factor)]
    assert wide.topology_checked and wide.internal_enlargement
    assert all(len(ids) == 1 for ids in wide.shoulder_neighbors)
    by_id = {b.feature_id: b for b in bores}
    assert all(by_id[i].diameter == pytest.approx(6*factor) for ids in wide.shoulder_neighbors for i in ids)
    assert wide.depth == pytest.approx(35*factor)
    assert find_counterbores(bores) == ()
    # Rotated holes need their separate, approved reference confirmation before deriving positions.
    if angle == 0:
        scheme = derive_scheme(model, features)
        assert not [c for c in scheme.plan.constraints if getattr(c, "dof", "") in ("counterbore", "depth")]
        assert any(c.dof == "diameter" and c.value == pytest.approx(22*factor)
                   for c in scheme.plan.constraints if isinstance(c, Sized))


@pytest.mark.parametrize("kind", ["counterbore", "blind_pilot"])
@pytest.mark.parametrize("factor,angle", [(1, 0), (.4, .47), (2.5, math.pi)])
def test_one_true_shoulder_preserves_the_counterbore_with_through_or_blind_pilot(tmp_path, kind, factor, angle):
    _, _, features = load_shape(tmp_path, part(kind), factor, angle)
    bores = [b for b in features if isinstance(b, Bore)]
    wide, = [b for b in bores if b.diameter == pytest.approx(22*factor)]
    assert sum(bool(ids) for ids in wide.shoulder_neighbors) == 1
    pair, = find_counterbores(bores)
    assert pair.diameter == pytest.approx(22*factor)
    assert pair.depth == pytest.approx(9*factor)


def test_overlapping_center_post_is_not_falsely_labeled_as_a_two_shoulder_chamber(tmp_path):
    body = cylinder(17, 0, 30)
    annulus = BRepAlgoAPI_Cut(cylinder(11, 20, 31), cylinder(5, 19, 32)).Shape()
    body = BRepAlgoAPI_Cut(body, annulus).Shape()
    body = BRepAlgoAPI_Cut(body, cylinder(2, -1, 31)).Shape()
    _, model, features = load_shape(tmp_path, body)
    wide, = [b for b in features if isinstance(b, Bore) and b.diameter == pytest.approx(22)]
    assert wide.shoulder_neighbors == ((), ()) and not wide.internal_enlargement
    # Axial overlap alone does not verify a counterbore pilot. Preserve facts and refuse the unproved symbol.
    verdict = gate_for(model, derive_scheme(model, features))
    assert "counterbore_semantics" in verdict.failed


def test_closed_chamber_and_blind_hole_of_same_size_do_not_share_a_depth_group():
    closed = Bore("c", 22, (0, 0, 0), (0, 0, 1), 35, mouth=0,
                  shoulder_neighbors=(("lower",), ("upper",)), topology_checked=True)
    blind = replace(closed, feature_id="b", axis_point=(50, 0, 0), shoulder_neighbors=((), ("pilot",)))
    groups = group_bores([closed, blind])
    assert len(groups) == 2
    assert next(g for g in groups if g.members == ("c",)).mouth is None
    assert next(g for g in groups if g.members == ("b",)).mouth == 0


def test_topology_metadata_does_not_change_existing_question_geometry_identity(tmp_path):
    _, _, features = load_shape(tmp_path, part("counterbore"))
    for bore in [b for b in features if isinstance(b, Bore)]:
        assert _feature_key(bore) == _feature_key(replace(bore, shoulder_neighbors=((), ()), topology_checked=False, end_tolerances=(0, 0)))


def test_cylinder_enumeration_order_does_not_change_physical_classification(tmp_path, monkeypatch):
    step, model, original = load_shape(tmp_path, part("enlargement"))
    method = feature_module._cylindrical_faces
    monkeypatch.setattr(feature_module, "_cylindrical_faces", lambda shape: iter(reversed(tuple(method(shape)))))
    reordered = extract_features(model.shape, model.bbox)
    signature = lambda fs: sorted((round(b.diameter, 6), b.internal_enlargement,
                                   tuple(len(x) for x in b.shoulder_neighbors)) for b in fs if isinstance(b, Bore))
    assert signature(original) == signature(reordered)


def test_step_roundtrip_keeps_the_same_end_relationships(tmp_path):
    _, model, features = load_shape(tmp_path, part("enlargement"))
    saved = _write_step(model.shape, tmp_path / "roundtrip.step")
    reopened = import_step(saved)
    again = extract_features(reopened.shape, reopened.bbox)
    assert sorted(_feature_key(b) for b in features if isinstance(b, Bore)) == sorted(
        _feature_key(b) for b in again if isinstance(b, Bore))
    assert sum(b.internal_enlargement for b in again if isinstance(b, Bore)) == 1


def test_corpus_0033_is_not_rejected_as_geo019_but_unsupported_counterbore_claim_is_blocked():
    model = import_step(CORPUS / "part_0033/model.step")
    features = extract_features(model.shape, model.bbox)
    wide = next(b for b in features if isinstance(b, Bore) and b.diameter == pytest.approx(80))
    assert not wide.internal_enlargement and wide.shoulder_neighbors == ((), ())
    verdict = gate_for(model, derive_scheme(model, features))
    assert "counterbore_semantics" in verdict.failed
    # Its drawing label has a 72 spotface; it is not evidence for an 80 counterbore.


def test_partial_fillet_shoulder_in_0063_retains_both_diameter_and_depth(tmp_path):
    path = CORPUS / "part_0063/model.step"
    model = import_step(path); features = extract_features(model.shape, model.bbox)
    large = next(b for b in features if isinstance(b, Bore) and b.diameter == pytest.approx(8))
    assert sum(bool(ids) for ids in large.shoulder_neighbors) == 1
    scheme = derive_scheme(model, features)
    assert gate_for(model, scheme).allowed
    part = pipeline.render_part(path, tmp_path)
    assert any(d.kind == "depth" and d.value == pytest.approx(2) for d in part.dimensions)
    assert any(d.source and d.source[1] == "counterbore" and d.value == pytest.approx(8) for d in part.dimensions)


def test_geo019_writes_true_diameter_without_fabricated_depth_in_all_outputs(tmp_path):
    part = pipeline.render_part(CORPUS / "part_0047/model.step", tmp_path)
    files = dict(part.files); doc = json.loads(files["json"].read_text())
    assert set(files) == {"dxf", "svg", "pdf", "json"}
    assert any(f["kind"] == "Bore" and f["diameter"] == pytest.approx(70) for f in doc["features"])
    assert any(d["kind"] == "diameter" and d["value"] == pytest.approx(70) for d in doc["dimensions"])
    assert not any(d["kind"] in ("depth", "counterbore") for d in doc["dimensions"])
    dxf = ezdxf.readfile(files["dxf"])
    assert not dxf.audit().errors
    entities = list(dxf.modelspace().query("DIMENSION"))
    assert len(entities) == len(doc["dimensions"])
    assert all(e.get_measurement() == pytest.approx(d["value"], abs=1e-6)
               for e, d in zip(entities, doc["dimensions"]))
    texts = [t.text or "" for t in ET.parse(files["svg"]).getroot().findall("{*}text")]
    assert any("70" in t for t in texts)
    assert not any("↧90" in t or "⌴" in t for t in texts)
    assert files["pdf"].read_bytes().startswith(b"%PDF")


def test_reintroduced_old_counterbore_plan_is_refused_before_any_output(tmp_path, monkeypatch):
    original = pipeline.derive_scheme
    def obsolete(model, features, **kwargs):
        unverified = tuple(replace(f, shoulder_neighbors=((), ()), topology_checked=False)
                           if isinstance(f, Bore) else f for f in features)
        wrong = original(model, unverified, **kwargs)
        return replace(wrong, features=features)
    monkeypatch.setattr(pipeline, "derive_scheme", obsolete)
    with pytest.raises(DeliveryBlocked) as error:
        pipeline.render_part(CORPUS / "part_0047/model.step", tmp_path)
    assert "counterbore_semantics" in error.value.failed
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("fault", ["missing", "wrong_shoulder"])
def test_one_invalid_nonrepresentative_member_blocks_the_whole_counterbore_group(tmp_path, fault):
    shape = BRepPrimAPI_MakeBox(80, 30, 20).Shape()
    for x in (20, 60):
        for radius, low, height in [(5, -1, 22), (8, 14, 7)]:
            tool = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(x, 15, low), gp_Dir(0, 0, 1)), radius, height).Shape()
            shape = BRepAlgoAPI_Cut(shape, tool).Shape()
    step = _write_step(shape, tmp_path / "paired.step")
    model = import_step(step); features = extract_features(model.shape, model.bbox)
    scheme = derive_scheme(model, features)
    assert gate_for(model, scheme).allowed
    large = [b for b in features if isinstance(b, Bore) and b.diameter == pytest.approx(16)]
    assert len(large) == 2
    constraints = [c for c in scheme.plan.constraints if getattr(c, "dof", "") == "counterbore"]
    assert len(constraints) == 1 and constraints[0].quantity == 2
    changes = {"topology_checked": False} if fault == "missing" else {"shoulder_neighbors": ((), ())}
    missing = tuple(replace(b, **changes) if b.feature_id == large[1].feature_id else b for b in features)
    assert "counterbore_semantics" in gate_for(model, replace(scheme, features=missing)).failed


def test_topology_failure_never_falls_back_to_unverified_pairing(tmp_path, monkeypatch, bearing_seat_step):
    from drawform.geometry import bore_topology
    def broken(*args):
        raise RuntimeError("isolated topology failure")
    monkeypatch.setattr(bore_topology, "_end_neighbors", broken)
    with pytest.raises(DeliveryBlocked) as error:
        pipeline.render_part(bearing_seat_step, tmp_path)
    assert error.value.failed == ("counterbore_semantics",)
    assert not list(tmp_path.iterdir())


def test_small_but_real_shoulder_tilt_is_not_accepted_as_perpendicular():
    angle = 5e-7
    normal = (math.sin(angle), 0, math.cos(angle))
    assert _shoulder_parameter((0, 0, 10), normal, (0, 0, 0), (0, 0, 1)) is None


@pytest.mark.parametrize("normal_sign", [-1, 1])
def test_shoulder_coordinate_does_not_depend_on_arbitrary_plane_parameter_origin(normal_sign):
    axis = (math.sqrt(.5), 0, math.sqrt(.5))
    tangent = (axis[0], 0, -axis[2])
    offset = tuple(3*x for x in tangent)
    point = tuple(o + 17*d for o, d in zip(offset, axis))
    moved = tuple(p + 100000*t for p, t in zip(point, tangent))
    normal = tuple(normal_sign*x for x in axis)
    assert _shoulder_parameter(point, normal, offset, axis) == pytest.approx(17, abs=1e-8)
    assert _shoulder_parameter(moved, normal, offset, axis) == pytest.approx(17, abs=1e-8)


def test_one_shallow_shoulder_cannot_supply_both_end_proofs_when_tolerance_covers_depth(tmp_path):
    body = BRepAlgoAPI_Cut(cylinder(17, 0, 52), cylinder(3, -1, 53)).Shape()
    shape = BRepAlgoAPI_Cut(body, cylinder(11, 51.98, 53)).Shape()
    step = _write_step(shape, tmp_path / "shallow.step")
    model = import_step(step)
    assert len(find_counterbores([b for b in extract_features(model.shape, model.bbox) if isinstance(b, Bore)])) == 1
    builder = BRep_Builder(); explorer = TopExp_Explorer(model.shape, TopAbs_FACE)
    while explorer.More():
        face = TopoDS.Face(explorer.Current())
        if BRepAdaptor_Surface(face).GetType() == GeomAbs_Plane:
            builder.UpdateFace(face, .1)
        explorer.Next()
    with pytest.raises(BoreTopologyError, match="两个端部"):
        extract_features(model.shape, model.bbox)


def test_depth_only_old_plan_cannot_reintroduce_an_internal_chamber_depth(tmp_path, monkeypatch):
    original = pipeline.derive_scheme
    def wrong_depth(model, features, **kwargs):
        scheme = original(model, features, **kwargs)
        group = next(g for g in group_bores([b for b in features if isinstance(b, Bore)])
                     if g.diameter == pytest.approx(70))
        wrong = Sized(group.group_id, "depth", 90)
        return replace(scheme, plan=replace(scheme.plan, constraints=scheme.plan.constraints+(wrong,)),
                       unknowns=scheme.unknowns+((group.group_id, "depth"),))
    monkeypatch.setattr(pipeline, "derive_scheme", wrong_depth)
    with pytest.raises(DeliveryBlocked) as error:
        pipeline.render_part(CORPUS / "part_0047/model.step", tmp_path)
    assert "counterbore_semantics" in error.value.failed
    assert any("内部扩腔" in message for message in error.value.verdict.reasons)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("fault", ["quantity", "diameter", "depth"])
def test_old_counterbore_value_or_quantity_is_checked_against_the_verified_geometry(tmp_path, monkeypatch, bearing_seat_step, fault):
    original = pipeline.derive_scheme
    def changed(model, features, **kwargs):
        scheme = original(model, features, **kwargs)
        target = "depth" if fault == "depth" else "counterbore"
        rows = tuple(replace(c, **({"quantity": c.quantity+1} if fault == "quantity" else {"value": c.value+1}))
                     if isinstance(c, Sized) and c.dof == target and c.feature.startswith("cbore-") else c
                     for c in scheme.plan.constraints)
        return replace(scheme, plan=replace(scheme.plan, constraints=rows))
    monkeypatch.setattr(pipeline, "derive_scheme", changed)
    with pytest.raises(DeliveryBlocked) as error:
        pipeline.render_part(bearing_seat_step, tmp_path)
    assert "counterbore_semantics" in error.value.failed
    assert any("数量与已核验孔组不符" in reason for reason in error.value.verdict.reasons)
    assert not list(tmp_path.iterdir())


def test_old_counterbore_answer_is_not_rebound_to_the_new_plain_diameter(tmp_path):
    from drawform.service.answers import Answer, AnswerError
    from drawform.service.questions import questions_for
    path = CORPUS / "part_0047/model.step"
    model = import_step(path); features = extract_features(model.shape, model.bbox)
    legacy = tuple(replace(b, shoulder_neighbors=((), ()), topology_checked=False) if isinstance(b, Bore) else b for b in features)
    legacy_scheme = derive_scheme(model, legacy)
    questions = questions_for(model, legacy, legacy_scheme)
    question = next(q for q in questions if q.kind == "special_tolerance" and "70" in q.target)
    answer = Answer(question.id, question.options[0].id)
    with pytest.raises(AnswerError) as error:
        pipeline.render_part(path, tmp_path, with_questions=True, questions_in=(answer,), questions_ref=questions)
    assert error.value.reason == "stale_base"
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("end", ["cap", "cone_neck"])
def test_a_real_bottom_shoulder_is_not_enough_when_the_proposed_mouth_is_blocked(tmp_path, end):
    body = BRepAlgoAPI_Cut(cylinder(17, 0, 52), cylinder(11, 8, 43)).Shape()
    body = BRepAlgoAPI_Cut(body, cylinder(3, -1, 8)).Shape()
    if end == "cone_neck":
        cone = BRepPrimAPI_MakeCone(gp_Ax2(gp_Pnt(0, 0, 43), gp_Dir(0, 0, 1)), 11, 3, 5).Shape()
        body = BRepAlgoAPI_Cut(body, cone).Shape()
        body = BRepAlgoAPI_Cut(body, cylinder(3, 48, 53)).Shape()
    step, model, features = load_shape(tmp_path, body)
    large = next(b for b in features if isinstance(b, Bore) and b.diameter == pytest.approx(22))
    assert sum(bool(x) for x in large.shoulder_neighbors) == 1
    assert len(find_counterbores([b for b in features if isinstance(b, Bore)])) == 1
    output = tmp_path / "drawing"; output.mkdir()
    with pytest.raises(DeliveryBlocked) as error:
        pipeline.render_part(step, output)
    assert "counterbore_semantics" in error.value.failed
    assert any("入口" in reason and "材料阻挡" in reason for reason in error.value.verdict.reasons)
    assert not list(output.iterdir())


def test_an_unrelated_overhang_does_not_turn_an_open_counterbore_into_an_internal_chamber(tmp_path):
    body = part("counterbore")
    post = BRepPrimAPI_MakeBox(gp_Pnt(14, -3, 52), 3, 6, 15).Shape()
    roof = BRepPrimAPI_MakeBox(gp_Pnt(-17, -3, 65), 34, 6, 3).Shape()
    body = BRepAlgoAPI_Fuse(BRepAlgoAPI_Fuse(body, post).Shape(), roof).Shape()
    _, model, features = load_shape(tmp_path, body)
    scheme = derive_scheme(model, features)
    assert gate_for(model, scheme).allowed


def test_a_remote_face_tolerance_does_not_expand_the_mouth_neighborhood(tmp_path):
    body = part("counterbore")
    post = BRepPrimAPI_MakeBox(gp_Pnt(14, -3, 52), 3, 6, 5).Shape()
    roof = BRepPrimAPI_MakeBox(gp_Pnt(-17, -3, 54), 34, 6, 3).Shape()
    body = BRepAlgoAPI_Fuse(BRepAlgoAPI_Fuse(body, post).Shape(), roof).Shape()
    _, model, _ = load_shape(tmp_path, body)
    builder = BRep_Builder(); explorer = TopExp_Explorer(model.shape, TopAbs_FACE)
    changed = False
    while explorer.More():
        face = TopoDS.Face(explorer.Current()); surface = BRepAdaptor_Surface(face)
        if surface.GetType() == GeomAbs_Plane and abs(surface.Plane().Location().Z()-96) < 1e-6:
            builder.UpdateFace(face, .1); changed = True
        explorer.Next()
    assert changed
    features = extract_features(model.shape, model.bbox)
    counterbore = next(b for b in features if isinstance(b, Bore) and b.diameter == pytest.approx(22))
    assert max(counterbore.end_tolerances) < 1e-5
    assert gate_for(model, derive_scheme(model, features)).allowed


@pytest.mark.parametrize("entity", ["face", "edge", "vertex"])
def test_a_nonplanar_mouth_uses_only_its_local_topological_tolerances(tmp_path, entity):
    body = BRepAlgoAPI_Cut(cylinder(17, 0, 52), cylinder(11, 8, 43)).Shape()
    body = BRepAlgoAPI_Cut(body, cylinder(3, -1, 8)).Shape()
    cone = BRepPrimAPI_MakeCone(gp_Ax2(gp_Pnt(0, 0, 43), gp_Dir(0, 0, 1)), 11, 3, 5).Shape()
    body = BRepAlgoAPI_Cut(body, cone).Shape()
    body = BRepAlgoAPI_Cut(body, cylinder(3, 48, 53)).Shape()
    _, model, _ = load_shape(tmp_path, body)
    builder = BRep_Builder(); changed = False
    explorer = TopExp_Explorer(model.shape, TopAbs_FACE if entity == "face" else TopAbs_EDGE)
    while explorer.More():
        if entity == "face":
            face = TopoDS.Face(explorer.Current())
            if BRepAdaptor_Surface(face).GetType() == GeomAbs_Cone:
                builder.UpdateFace(face, .002); changed = True
        else:
            edge = TopoDS.Edge(explorer.Current()); curve = BRepAdaptor_Curve(edge)
            if curve.GetType() == GeomAbs_Circle and abs(curve.Circle().Radius()-11) < 1e-6 and abs(curve.Circle().Location().Z()-82) < 1e-6:
                if entity == "edge":
                    builder.UpdateEdge(edge, .002)
                else:
                    vertices = TopExp_Explorer(edge, TopAbs_VERTEX)
                    builder.UpdateVertex(TopoDS.Vertex(vertices.Current()), .002)
                changed = True
        explorer.Next()
    assert changed
    features = extract_features(model.shape, model.bbox)
    large = next(b for b in features if isinstance(b, Bore) and b.diameter == pytest.approx(22))
    assert large.end_tolerances[0] < 1e-5
    assert large.end_tolerances[1] == pytest.approx(.002)
    assert "counterbore_semantics" in gate_for(model, derive_scheme(model, features)).failed


def test_opening_intersection_failure_is_a_refusal_not_an_unchecked_pass(tmp_path, monkeypatch, bearing_seat_step):
    from drawform.geometry import bore_topology
    def broken(*args):
        raise RuntimeError("isolated opening intersection failure")
    monkeypatch.setattr(bore_topology, "mouth_region_clear", broken)
    with pytest.raises(DeliveryBlocked) as error:
        pipeline.render_part(bearing_seat_step, tmp_path)
    assert error.value.failed == ("counterbore_semantics",)
    assert not list(tmp_path.iterdir())


def test_opening_check_does_not_change_the_input_feature_facts(bearing_seat_step):
    model = import_step(bearing_seat_step)
    before = extract_features(model.shape, model.bbox)
    assert gate_for(model, derive_scheme(model, before)).allowed
    assert extract_features(model.shape, model.bbox) == before

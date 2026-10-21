"""Independent checks for the interrupted audit: semantic answers and geometric witnesses."""
from dataclasses import replace
import importlib.util
import math
from pathlib import Path
import json

import ezdxf
import pytest

from drawform.delivery import COUNTERBORE_SEMANTICS, gate_for, unplaced_dimensions
from drawform.dimensioning.place import measurement_errors, place_scheme
from drawform.dimensioning.scheme import derive_scheme
from drawform.domain.curves import FRONT, TOP, LEFT
from drawform.geometry.features import extract_features
from drawform.geometry.loader import import_step
from drawform.geometry.projection import project_view
from drawform.pipeline import render_part
from drawform.service.answers import Answer, AnswerError, apply_answers, bind_scheme
from drawform.service.questions import Option, questions_for
from tests.test_frontend_shell import visual_client
from tests.test_live_questions import service_factory, AUTH


def prepared(step):
    model = import_step(step)
    features = extract_features(model.shape, model.bbox)
    scheme = bind_scheme(derive_scheme(model, features), model)
    return model, scheme, questions_for(model, features, scheme)


@pytest.mark.parametrize("fixture,valid,invalid", [
    ("plate_step", "H7", "h7"), ("stepped_shaft_step", "h7", "H7")])
def test_known_holes_and_shafts_reject_the_opposite_fit_direction(request, fixture, valid, invalid):
    _, scheme, questions = prepared(request.getfixturevalue(fixture))
    question = next(q for q in questions if q.kind == "special_tolerance"
                    and any(o.id == valid for o in q.options))
    correct, notes = apply_answers(scheme, questions, (Answer(question.id, valid),))
    assert not notes and correct != scheme
    assert any(getattr(c, "tolerance", None) == valid for c in correct.plan.constraints)
    wrong, notes = apply_answers(scheme, questions, (Answer(question.id, invalid),))
    assert wrong == scheme and notes


@pytest.mark.parametrize("fixture,valid,invalid", [
    ("plate_step", "H7", "h7"), ("stepped_shaft_step", "h7", "H7")])
def test_old_mixed_catalogue_cannot_reintroduce_a_wrong_fit(request, fixture, valid, invalid):
    _, scheme, questions = prepared(request.getfixturevalue(fixture))
    question = next(q for q in questions if q.kind == "special_tolerance"
                    and any(o.id == valid for o in q.options))
    legacy = replace(question, options=question.options + (Option(invalid, invalid, None),))
    catalogue = tuple(legacy if q.id == question.id else q for q in questions)
    result, notes = apply_answers(scheme, catalogue, (Answer(question.id, invalid),))
    assert result == scheme and notes, "历史候选不能覆盖当前孔轴几何语义"


def test_old_shaft_catalogue_is_refused_before_any_drawing_is_published(stepped_shaft_step, tmp_path):
    _, scheme, questions = prepared(stepped_shaft_step)
    question = next(q for q in questions if q.kind == "special_tolerance"
                    and any(o.id == "h7" for o in q.options))
    catalogue = tuple(replace(q, options=q.options + (Option("H7", "H7", None),))
                      if q.id == question.id else q for q in questions)
    out = tmp_path / "refused"
    out.mkdir()
    with pytest.raises(AnswerError):
        render_part(stepped_shaft_step, out, questions_in=(Answer(question.id, "H7"),),
                    questions_ref=catalogue)
    assert not list(out.glob("*.dxf")) and not list(out.glob("*.svg"))


@pytest.mark.parametrize("fixture", ["bolt_circle_step", "odd_bolt_circle_step"])
@pytest.mark.parametrize("wrong_value", [40.0, 60.0, 50.0005])
def test_pcd_placement_cannot_manufacture_witnesses_from_a_wrong_plan(request, fixture, wrong_value):
    model, scheme, _ = prepared(request.getfixturevalue(fixture))
    projections = {d.name: project_view(model.shape, d) for d in (FRONT, TOP, LEFT)}
    original = place_scheme(scheme, projections)
    pcd = next(d for d in original if d.kind == "pcd")
    assert math.dist(pcd.p1, pcd.p2) == pytest.approx(50, abs=1e-6)
    assert gate_for(model, scheme, original, projections=projections).allowed
    bad = replace(scheme, plan=replace(scheme.plan, constraints=tuple(
        replace(c, value=wrong_value) if getattr(c, "dof", None) == "pcd" else c
        for c in scheme.plan.constraints)))
    drawn = place_scheme(bad, projections)
    assert unplaced_dimensions(bad, drawn) or measurement_errors(bad, drawn), \
        "方案错了，必须保留无落位或测量错误，不能用错值反造测量端点"
    assert not gate_for(model, bad, drawn, projections=projections).allowed


def test_counterbore_reason_deduplication_preserves_the_block_and_distinct_errors(tmp_path):
    from tests.test_counterbore_topology import load_shape, part
    _, model, features = load_shape(tmp_path, part("counterbore"))
    scheme = derive_scheme(model, features)
    assert gate_for(model, scheme).allowed
    damaged = replace(scheme, features=tuple(
        replace(f, topology_checked=False) if getattr(f, "diameter", 0) == 22 else f
        for f in features))
    verdict = gate_for(model, damaged)
    assert COUNTERBORE_SEMANTICS in verdict.failed
    reasons = next(r for r in verdict.reasons if r.startswith(COUNTERBORE_SEMANTICS))
    assert reasons.count("缺少大孔端部经真实肩面连接引导孔的依据") == 1
    wrong = replace(scheme, plan=replace(scheme.plan, constraints=tuple(
        replace(c, value=c.value + 1) if getattr(c, "dof", None) in ("counterbore", "depth") else c
        for c in scheme.plan.constraints)))
    different = gate_for(model, wrong)
    assert COUNTERBORE_SEMANTICS in different.failed
    assert any("counterbore数值" in r and "depth数值" in r for r in different.reasons)


def test_presentation_csp_allows_local_assets_without_external_font_destinations(visual_client):
    response = visual_client.get("/")
    assert response.status_code == 200
    policy = response.headers["Content-Security-Policy"]
    assert "style-src 'self' 'unsafe-inline';" in policy
    assert "font-src 'self';" in policy
    assert "connect-src 'self';" in policy
    assert "https:" not in policy and "http:" not in policy
    assert visual_client.get("/assets/app.js").status_code == 200


def test_corpus_coverage_counts_shared_sources_separately_from_annotation_entities():
    from drawform.eval.corpus import load_corpus
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location("audit_measurement", root / "research/measure_corpus_baseline.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sample = next(s for s in load_corpus(root / "corpus/cadsy") if s.sample_id == "part_0001")
    row = module.measure(sample.directory / "model.step", sample.annotations)
    assert row["unplaced"] == 0
    assert row["covered"] == row["planned"]
    assert row["drawn"] < row["covered"], "该案例确有一条标注覆盖多个来源，不能用空对照证明"


@pytest.mark.parametrize("fixture,valid,invalid", [
    ("plate_step", "H7", "h7"), ("stepped_shaft_step", "h7", "H7")])
def test_real_answer_api_rejects_wrong_case_and_publishes_the_valid_fit(
        request, fixture, valid, invalid, service_factory):
    from tests.test_live_answers import _drawn, _submit, _answer, _wait
    service = service_factory(seeded=False)
    job_id, initial, questions = _drawn(service, request.getfixturevalue(fixture))
    question = next(q for q in questions if q["kind"] == "special_tolerance"
                    and any(o["id"] == valid for o in q["options"]))
    originals = {kind: service.http.get(url, headers=AUTH).data
                 for kind, url in initial["files"].items()}
    assert _submit(service, job_id, [_answer(question, invalid)]).status_code == 400
    assert service.queue.get(job_id).state == "drawn"
    assert _submit(service, job_id, [_answer(question, valid)]).status_code == 202
    status = _wait(service, job_id)
    assert status["state"] == "drawn" and status["version"] == 1
    document = service.http.get(status["files"]["json"], headers=AUTH).get_json()
    assert any(c["tolerance"] == valid for c in document["constraints"])
    for kind, original in originals.items():
        assert service.http.get(initial["files"][kind], headers=AUTH).data == original


def test_odd_pcd_upload_is_drawn_with_all_four_artifacts(odd_bolt_circle_step, service_factory):
    from tests.test_live_answers import _drawn
    service = service_factory(seeded=False)
    _, status, _ = _drawn(service, odd_bolt_circle_step)
    assert set(status["files"]) == {"dxf", "svg", "pdf", "json"}
    document = service.http.get(status["files"]["json"], headers=AUTH).get_json()
    assert [d["value"] for d in document["dimensions"] if d["kind"] == "pcd"] == pytest.approx([50])


@pytest.mark.parametrize("count", [3, 6, 10])
def test_rotated_and_translated_hole_patterns_publish_a_real_native_pcd(count, tmp_path):
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Ax2, gp_Pnt, gp_Dir
    from tests.conftest import _write_step
    shape = BRepPrimAPI_MakeBox(gp_Pnt(100, -80, 0), 80, 80, 12).Shape()
    for index in range(count):
        angle = .27 + 2 * math.pi * index / count
        axis = gp_Ax2(gp_Pnt(140 + 25 * math.cos(angle), -40 + 25 * math.sin(angle), -1),
                      gp_Dir(0, 0, 1))
        shape = BRepAlgoAPI_Cut(shape, BRepPrimAPI_MakeCylinder(axis, 3, 14).Shape()).Shape()
    step = _write_step(shape, tmp_path / "translated.step")
    out = tmp_path / "drawing"
    out.mkdir()
    part = render_part(step, out)
    files = dict(part.files)
    assert part.verdict.allowed and set(files) == {"dxf", "svg", "pdf", "json"}
    document = json.loads(files["json"].read_text())
    dimensions = document["dimensions"]
    index = next(i for i, d in enumerate(dimensions) if d["kind"] == "pcd")
    native = ezdxf.readfile(files["dxf"]).modelspace()
    entity = list(native.query("DIMENSION"))[index]
    factor = document["drawing_scale"]
    assert entity.get_measurement() == pytest.approx(50 * factor, abs=1e-6)
    assert dimensions[index]["value"] == pytest.approx(50, abs=1e-6)
    assert any(c.dxf.radius == pytest.approx(25 * factor, abs=1e-6)
               for c in native.query('CIRCLE[layer=="CENTER"]'))

"""回写必须与当前 STEP 几何、一般公差归口和当前图纸问答保持一致。"""

from dataclasses import replace
import json

import pytest
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

from drawform.dimensioning.constraints import Sized
from drawform.dimensioning.general import broken_promises, general_tolerances
from drawform.dimensioning.scheme import derive_scheme
from drawform.pipeline import render_part
from drawform.service.answers import Answer, AnswerError, apply_answers, bind_scheme
from drawform.service.questions import questions_for
from tests.conftest import _write_step
from tests.test_answer_versions import _first
from tests.test_live_questions import AUTH, service_factory
from tests.test_questions import _derived


def test_stale_question_geometry_is_rejected_before_any_drawing_is_written(
        plate_step, stepped_shaft_step, tmp_path):
    model, features, scheme = _derived(plate_step)
    questions = questions_for(model, features, scheme)
    question = next(q for q in questions if q.kind == "special_tolerance")
    with pytest.raises(AnswerError):
        render_part(stepped_shaft_step, tmp_path, questions_in=(Answer(question.id, "H7"),),
                    questions_ref=questions, with_questions=True)
    assert list(tmp_path.iterdir()) == []


def _offset_part(tmp_path, x, radius=0.1):
    shape = BRepAlgoAPI_Cut(BRepPrimAPI_MakeBox(80.0, 50.0, 12.0).Shape(),
                           BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(x, 10, -1),
                                                         gp_Dir(0, 0, 1)), radius, 14).Shape()).Shape()
    model, features, scheme = _derived(_write_step(shape, tmp_path / "offset.step"))
    bound = bind_scheme(scheme, model)
    return bound, questions_for(model, features, bound)


@pytest.mark.parametrize("x", [0.25, 79.75])
def test_changing_datum_refreshes_the_actual_general_tolerance_gap_count(x, tmp_path):
    scheme, questions = _offset_part(tmp_path, x)
    question = next(q for q in questions if q.kind == "datum_choice" and q.datum == "A")
    face = max((f for f in scheme.datum_candidates if f.axis == "x"), key=lambda f: f.position)
    result, notes = apply_answers(scheme, questions, (Answer(question.id, face.id),))
    assert not notes
    gaps = broken_promises(general_tolerances(result.plan))
    original = broken_promises(general_tolerances(scheme.plan))
    assert len(gaps) != len(original)
    assert any(f"另有 {len(gaps)} 条" in note for note in result.notes)


def test_resolving_the_last_gap_removes_the_obsolete_general_tolerance_warning(tmp_path):
    model, features, _ = _derived(_write_step(BRepPrimAPI_MakeBox(2, 30, 12).Shape(),
                                             tmp_path / "small.step"))
    scheme = derive_scheme(model, features, general_tolerance="ISO 2768-v")
    questions = questions_for(model, features, scheme)
    question = next(q for q in questions if q.kind == "special_tolerance")
    assert any("另有 1 条" in note for note in scheme.notes)
    result, notes = apply_answers(scheme, questions, (Answer(question.id, "H7"),))
    assert not notes and not broken_promises(general_tolerances(result.plan))
    assert not any("另有" in note for note in result.notes)
    assert "一般公差 ISO 2768-v" in result.notes


def test_datum_recalculation_uses_geometry_even_after_an_earlier_datum_choice(tmp_path):
    scheme, questions = _offset_part(tmp_path, 15, radius=5)
    question = next(q for q in questions if q.kind == "datum_choice" and q.datum == "A")
    faces = sorted((f for f in scheme.datum_candidates if f.axis == "x"), key=lambda f: f.position)
    first, _ = apply_answers(scheme, questions, (Answer(question.id, faces[-1].id),))
    second, notes = apply_answers(first, questions, (Answer(question.id, faces[0].id),))
    assert not notes
    values = [c.value for c in second.plan.constraints if isinstance(c, Sized) and c.dof == "x"]
    assert values and all(value == pytest.approx(15.0) for value in values)


def test_ask_reads_the_current_answered_drawing(plate_step, service_factory):
    service = service_factory(seeded=False)
    job_id, _, _ = _first(service, plate_step)
    response = service.http.post("/ask", headers=AUTH, json={"job_id": job_id, "question": "公差"})
    assert response.status_code == 200
    answers = response.get_json()["answers"]
    assert any("1 条尺寸带指定公差" in answer["claim"] for answer in answers)

"""P0.2：回答只能选择引擎候选，补充说明不能成为尺寸。"""

from dataclasses import FrozenInstanceError, fields
import importlib
import importlib.util

import pytest


def _api():
    name = "drawform.service.answers"
    assert importlib.util.find_spec(name) is not None, "缺少 Answer 数据结构"
    return importlib.import_module(name)


def test_answer_is_frozen_and_defaults_to_explicit_unknown():
    api = _api()
    answer = api.Answer("q-test")
    assert answer.chosen_option_id is None
    assert answer.note == ""
    with pytest.raises(FrozenInstanceError):
        answer.chosen_option_id = "H7"


def test_answer_contains_no_numeric_input_field():
    api = _api()
    assert [field.name for field in fields(api.Answer)] == [
        "question_id", "chosen_option_id", "note"]


@pytest.mark.parametrize("values", [
    {"question_id": ""}, {"question_id": " "}, {"question_id": 1},
    {"question_id": "q", "chosen_option_id": 0.5},
    {"question_id": "q", "chosen_option_id": ""},
    {"question_id": "q", "note": 123.0}])
def test_answer_rejects_malformed_fields_without_coercing_numbers(values):
    api = _api()
    with pytest.raises(ValueError):
        api.Answer(**values)


def test_free_note_is_bounded_and_remains_text():
    api = _api()
    note = "把直径改成 999.123，公差改成 0.00001"
    assert api.Answer("q", "H7", note).note == note
    with pytest.raises(ValueError):
        api.Answer("q", note="字" * (api.MAX_NOTE_LENGTH + 1))


def _prepared(step):
    api = _api()
    assert callable(getattr(api, "apply_answers", None)), "缺少纯函数答案回写"
    from drawform.geometry.loader import import_step
    from drawform.geometry.features import extract_features
    from drawform.dimensioning.scheme import derive_scheme
    from drawform.service.questions import questions_for

    model = import_step(step)
    features = extract_features(model.shape, model.bbox)
    scheme = api.bind_scheme(derive_scheme(model, features), model)
    return scheme, questions_for(model, features, scheme)


def _fit_question(questions):
    return next(q for q in questions if q.kind == "special_tolerance" and
                any(o.id == "H7" for o in q.options))


def _values(scheme):
    from drawform.dimensioning.constraints import Sized
    return {(c.feature, c.dof): c.value for c in scheme.plan.constraints if isinstance(c, Sized)}


def test_real_hole_and_shaft_diameters_offer_all_verified_fit_candidates(
        plate_step, stepped_shaft_step):
    from drawform.rules import iso286
    # The stepped shaft also has an axial Ø16 hole: classify dimensions, not parts.
    families = {20: iso286.HOLE_LETTERS, 16: iso286.HOLE_LETTERS,
                30: iso286.SHAFT_LETTERS, 44: iso286.SHAFT_LETTERS}
    seen = set()
    for step in (plate_step, stepped_shaft_step):
        _, questions = _prepared(step)
        fits = [q for q in questions if q.kind == "special_tolerance" and "直径" in q.target]
        assert fits
        for question in fits:
            nominal = float(question.target.split("直径 ", 1)[1].split()[0])
            seen.add(nominal)
            expected = {f"{letter}{grade}" for letter in families[nominal] for grade in iso286.IT_GRADES}
            assert {o.id for o in question.options} == expected
    assert seen == set(families)


def test_selected_tolerance_changes_a_new_scheme_without_changing_any_dimension_value(plate_step):
    api = _api()
    scheme, questions = _prepared(plate_step)
    question = _fit_question(questions)
    result, notes = api.apply_answers(scheme, questions, (api.Answer(question.id, "H7"),))
    assert result is not scheme
    assert _values(result) == _values(scheme)
    assert all(c.tolerance is None for c in scheme.plan.constraints if hasattr(c, "tolerance"))
    assert sum(getattr(c, "tolerance", None) == "H7" for c in result.plan.constraints) == 1
    assert not notes


@pytest.mark.parametrize("bad_question,bad_option", [(True, False), (False, True)])
def test_invalid_answers_are_rejected_without_partial_changes(plate_step, bad_question, bad_option):
    api = _api()
    scheme, questions = _prepared(plate_step)
    question = _fit_question(questions)
    result, notes = api.apply_answers(scheme, questions, (
        api.Answer(question.id, "H7"),
        api.Answer("foreign-question" if bad_question else next(q.id for q in questions
                                                              if q.kind == "datum_choice"),
                   "foreign-option" if bad_option else "H7")))
    assert result == scheme
    assert notes


def test_empty_answers_do_not_change_the_scheme(plate_step):
    api = _api()
    scheme, questions = _prepared(plate_step)
    result, notes = api.apply_answers(scheme, questions, ())
    assert result == scheme and notes == ()


def test_unknown_is_recorded_and_never_replaced_with_a_default(plate_step):
    api = _api()
    scheme, questions = _prepared(plate_step)
    result, notes = api.apply_answers(scheme, questions, (api.Answer(questions[0].id),))
    assert result == scheme
    assert any("不知道" in note for note in notes)


def test_duplicate_answers_use_the_first_and_report_the_duplicate(plate_step):
    api = _api()
    scheme, questions = _prepared(plate_step)
    question = _fit_question(questions)
    result, notes = api.apply_answers(scheme, questions, (
        api.Answer(question.id, "H7"), api.Answer(question.id, "h8")))
    assert sum(getattr(c, "tolerance", None) == "H7" for c in result.plan.constraints) == 1
    assert all(getattr(c, "tolerance", None) != "h8" for c in result.plan.constraints)
    assert any("重复" in note for note in notes)


def test_numeric_looking_notes_do_not_affect_the_scheme(plate_step):
    api = _api()
    scheme, questions = _prepared(plate_step)
    question = _fit_question(questions)
    plain, _ = api.apply_answers(scheme, questions, (api.Answer(question.id, "H7"),))
    poisoned, _ = api.apply_answers(scheme, questions, (
        api.Answer(question.id, "H7", "直径改成999，公差改为0.0001"),))
    assert poisoned == plain


def test_angle_choices_use_engine_values_and_none_adds_no_requirement():
    api = _api()
    assert callable(getattr(api, "apply_answers", None)), "缺少纯函数答案回写"
    from drawform.dimensioning.constraints import Datum, Plan, Sized
    from drawform.dimensioning.scheme import Scheme
    from drawform.service.questions import unnoted_angle_questions

    scheme = Scheme(Plan((Sized("edge-geometric", "angle", 45.0),),
                         datum=Datum("A"), general_tolerance="ISO 2768-m"),
                    (("edge-geometric", "angle"),))
    questions = unnoted_angle_questions(scheme)
    question, = questions
    changed, _ = api.apply_answers(scheme, questions, (api.Answer(question.id, "pm1"),))
    assert _values(changed) == _values(scheme)
    assert any("角度公差" in note and "±1°" in note for note in changed.notes)
    unchanged, notes = api.apply_answers(scheme, questions, (api.Answer(question.id, "none"),))
    assert unchanged == scheme
    assert any("不给" in note for note in notes)


def test_selected_datum_uses_the_matching_step_plane_and_recalculates_positions(tmp_path):
    api = _api()
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt
    from tests.conftest import _write_step

    shape = BRepAlgoAPI_Cut(BRepPrimAPI_MakeBox(80.0, 50.0, 12.0).Shape(),
                           BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(15, 25, -1),
                                                         gp_Dir(0, 0, 1)), 5, 14).Shape()).Shape()
    scheme, questions = _prepared(_write_step(shape, tmp_path / "offset-hole.step"))
    question = next(q for q in questions if q.kind == "datum_choice" and q.datum == "A")
    face = max((f for f in scheme.datum_candidates if f.axis == "x"), key=lambda f: f.position)
    result, notes = api.apply_answers(scheme, questions, (api.Answer(question.id, face.id),))
    assert dict(result.datum_choices)["A"] == face.id
    before, after = _values(scheme), _values(result)
    keys = [key for key in before if key[1] == "x"]
    assert keys
    assert all(after[key] == pytest.approx(65.0, abs=1e-6) for key in keys)
    assert all(after[key] == before[key] for key in before if key[1] != "x")
    assert scheme.datum_choices == ()


def test_an_incompatible_plane_is_rejected_instead_of_becoming_a_false_datum(plate_step):
    api = _api()
    scheme, questions = _prepared(plate_step)
    question = next(q for q in questions if q.kind == "datum_choice" and q.datum == "A")
    wrong = next(o for o in question.options if o.axis == "y")
    with pytest.raises(api.AnswerError) as caught:
        api.validate_answers(questions, (api.Answer(question.id, wrong.id),))
    assert caught.value.reason == "incompatible_datum"

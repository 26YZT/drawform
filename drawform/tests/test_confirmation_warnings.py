"""待确认信息进入 Verdict/JSON，但不得变成新的交付阻断条件。"""

import json

import pytest

from drawform.delivery import ALLOW, BLOCK, DeliveryBlocked, Verdict, require_allowed
from drawform.pipeline import render_part
from drawform.service.answers import Answer
from drawform.service import questions


def _render(step, directory, **kwargs):
    directory.mkdir()
    return render_part(step, directory, stem="same", **kwargs)


def _document(part):
    return json.loads(dict(part.files)["json"].read_text())


@pytest.mark.parametrize("decision", [ALLOW, BLOCK])
def test_warning_payload_does_not_change_a_gate_decision(decision):
    verdict = Verdict(decision, warnings=("待人工确认",))
    assert verdict.allowed is (decision == ALLOW)
    if decision == ALLOW:
        assert require_allowed(verdict) is verdict
    else:
        with pytest.raises(DeliveryBlocked):
            require_allowed(verdict)


def test_default_render_exposes_warnings_without_enumerating_candidates(plate_step, tmp_path, monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("默认调用不应为警告枚举候选")

    monkeypatch.setattr(questions, "_fit_options", unexpected)
    monkeypatch.setattr(questions, "_plane_options", unexpected)
    part = _render(plate_step, tmp_path / "default")
    assert part.questions == ()
    assert part.verdict.allowed and part.verdict.warnings
    assert _document(part)["verdict"]["warnings"] == list(part.verdict.warnings)


def test_unknown_answers_end_pending_questions_but_keep_explicit_uncertainty(plate_step, tmp_path):
    first = _render(plate_step, tmp_path / "first", with_questions=True)
    count = len(first.questions)
    assert count > 0
    assert first.verdict.warnings[0].startswith(f"待人工确认 {count} 项：")
    after = _render(plate_step, tmp_path / "unknown", with_questions=True,
                    questions_in=tuple(Answer(q.id, None) for q in first.questions))
    assert after.questions == () and after.verdict.allowed
    assert len(after.verdict.warnings) == 1
    assert after.verdict.warnings[0].startswith(f"工程师明确选择不知道 {count} 项：")
    assert _document(after)["verdict"]["warnings"] == list(after.verdict.warnings)
    assert dict(first.files)["svg"].read_bytes() == dict(after.files)["svg"].read_bytes()


def test_a_selected_tolerance_removes_only_its_own_pending_warning(plate_step, tmp_path):
    first = _render(plate_step, tmp_path / "first", with_questions=True)
    target = next(q for q in first.questions if q.kind == "special_tolerance")
    after = _render(plate_step, tmp_path / "selected", with_questions=True,
                    questions_in=(Answer(target.id, "H7"),))
    assert len(after.questions) == len(first.questions) - 1 > 0
    assert len(after.verdict.warnings) == 1
    assert after.verdict.warnings[0].startswith(f"待人工确认 {len(after.questions)} 项：")
    assert after.verdict.decision == first.verdict.decision == ALLOW
    assert after.verdict.failed == first.verdict.failed == ()


def test_a_part_without_confirmation_questions_has_no_spurious_warning(tmp_path):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from tests.conftest import _write_step

    step = _write_step(BRepPrimAPI_MakeBox(80, 50, 12).Shape(), tmp_path / "plain.step")
    part = _render(step, tmp_path / "out", with_questions=True)
    assert part.questions == ()
    assert part.verdict.warnings == ()
    assert _document(part)["verdict"]["warnings"] == []

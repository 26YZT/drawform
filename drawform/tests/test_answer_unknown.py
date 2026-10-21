"""T-11：不知道必须保留图面原状，并留下可核查的人工未知记录。"""

import json

from drawform.pipeline import render_part
from drawform.service.answers import Answer
from tests.test_answer_rendering import _dxf_texts
from tests.test_live_questions import _dxf_entities, _pdf_page_streams


def test_unknown_keeps_all_drawing_outputs_and_records_an_explicit_assumption(plate_step, tmp_path):
    before_dir, after_dir = tmp_path / "before", tmp_path / "after"
    before_dir.mkdir()
    after_dir.mkdir()
    before = render_part(plate_step, before_dir, stem="same", with_questions=True)
    question = next(q for q in before.questions if q.kind == "datum_choice")
    after = render_part(plate_step, after_dir, stem="same", with_questions=True,
                        questions_in=(Answer(question.id),))
    original, current = dict(before.files), dict(after.files)
    assert original["svg"].read_bytes() == current["svg"].read_bytes()
    assert _dxf_entities(original["dxf"]) == _dxf_entities(current["dxf"])
    assert _pdf_page_streams(original["pdf"]) == _pdf_page_streams(current["pdf"])
    assert before.dimensions == after.dimensions
    document = json.loads(current["json"].read_text(encoding="utf-8"))
    unknown = next((a for a in document["assumptions"] if a["id"] == "human-unknown"), None)
    assert unknown is not None, "人工不知道必须进入出图说明，不得伪装成确认"
    assert unknown["value"]["questions"] == [question.id]
    assert "不知道" in unknown["claim"]
    assert document["answered_count"] == document["unknown_count"] == 1
    assert question.id not in {q["id"] for q in document["questions"]}


def test_a_numeric_free_note_stays_in_the_answer_record_only(plate_step, tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    before = render_part(plate_step, first, stem="same", with_questions=True)
    question = next(q for q in before.questions if q.kind == "datum_choice")
    note = "直径改成999.123456，公差改成0.000001"
    after = render_part(plate_step, second, stem="same", with_questions=True,
                        questions_in=(Answer(question.id, note=note),))
    original, current = dict(before.files), dict(after.files)
    before_json = json.loads(original["json"].read_text(encoding="utf-8"))
    after_json = json.loads(current["json"].read_text(encoding="utf-8"))
    assert after_json["answers"][0]["note"] == note
    assert after_json["constraints"] == before_json["constraints"]
    assert after_json["dimensions"] == before_json["dimensions"]
    assert "999.123456" not in current["svg"].read_text(encoding="utf-8")
    assert not any("999.123456" in text for text in _dxf_texts(current["dxf"]))

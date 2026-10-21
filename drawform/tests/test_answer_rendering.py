"""回答后仍走唯一出图流程：只改指定内容，保留原生尺寸及 STEP 数值。"""

from collections import Counter
import inspect
import json
import xml.etree.ElementTree as ET

import ezdxf
import pytest

from drawform import pipeline
from drawform.service.answers import Answer, AnswerError


def _render(step, directory, **kwargs):
    assert "questions_in" in inspect.signature(pipeline.render_part).parameters, "未接答案回写出图"
    directory.mkdir()
    return pipeline.render_part(step, directory, stem="same", with_questions=True, **kwargs)


def _texts(path):
    root = ET.parse(path).getroot()
    return Counter(("".join(node.itertext()), node.attrib.get("x"), node.attrib.get("y"))
                   for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "text")


def _dxf_texts(path):
    doc = ezdxf.readfile(str(path))
    return [entity.dxf.text if entity.dxftype() == "TEXT" else entity.text
            for entity in doc.entitydb.values() if entity.dxftype() in ("TEXT", "MTEXT")]


def test_h7_changes_exactly_its_dimension_and_title_note_without_moving_svg_text(plate_step, tmp_path):
    before = _render(plate_step, tmp_path / "before")
    question = next(q for q in before.questions if q.kind == "special_tolerance")
    after = _render(plate_step, tmp_path / "after", questions_in=(Answer(question.id, "H7"),))
    old_files, new_files = dict(before.files), dict(after.files)
    old, new = _texts(old_files["svg"]), _texts(new_files["svg"])
    assert Counter((x, y) for _, x, y in old.elements()) == Counter((x, y) for _, x, y in new.elements())
    assert sum((old - new).values()) == sum((new - old).values()) == 2
    assert any(text == "⌀20H7" for text, _, _ in new)
    assert sum(text.count("一般公差") for text, _, _ in new) == 1
    assert sum(text.count("一般公差") for text in _dxf_texts(new_files["dxf"])) == 1
    assert any("H7" in text for text in _dxf_texts(new_files["dxf"]))
    original = list(ezdxf.readfile(str(old_files["dxf"])).modelspace().query("DIMENSION"))
    updated = list(ezdxf.readfile(str(new_files["dxf"])).modelspace().query("DIMENSION"))
    assert len(updated) == len(original) and updated
    assert [d.get_measurement() for d in updated] == [d.get_measurement() for d in original]
    assert [d.dxf.text_midpoint for d in updated] == [d.dxf.text_midpoint for d in original]
    assert sum("H7" in d.dxf.text for d in updated) == 1
    old_json = json.loads(old_files["json"].read_text())
    new_json = json.loads(new_files["json"].read_text())
    assert [c["value"] for c in new_json["constraints"]] == [c["value"] for c in old_json["constraints"]]
    assert new_json["answered_count"] == 1 and new_json["unknown_count"] == 0
    assert question.id not in {q.id for q in after.questions}
    assert new_files["pdf"].read_bytes().startswith(b"%PDF")


def test_invalid_answer_leaves_no_drawing_files(plate_step, tmp_path):
    with pytest.raises(AnswerError):
        _render(plate_step, tmp_path / "bad", questions_in=(Answer("foreign", "H7"),))
    assert list((tmp_path / "bad").iterdir()) == []


def test_datum_answer_moves_the_real_anchor_and_recalculates_native_dimensions(tmp_path):
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt
    from tests.conftest import _write_step
    from tests.test_answers import _prepared

    shape = BRepAlgoAPI_Cut(BRepPrimAPI_MakeBox(80.0, 50.0, 12.0).Shape(),
                           BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(15, 25, -1),
                                                         gp_Dir(0, 0, 1)), 5, 14).Shape()).Shape()
    step = _write_step(shape, tmp_path / "offset.step")
    before = _render(step, tmp_path / "before")
    scheme, _ = _prepared(step)
    face = max((f for f in scheme.datum_candidates if f.axis == "x"), key=lambda f: f.position)
    question = next(q for q in before.questions if q.kind == "datum_choice" and q.datum == "A")
    after = _render(step, tmp_path / "after", questions_in=(Answer(question.id, face.id),))
    old, new = [json.loads(dict(part.files)["json"].read_text()) for part in (before, after)]
    assert next(m for m in old["datums"] if m["letter"] == "A")["anchor"] != next(
        m for m in new["datums"] if m["letter"] == "A")["anchor"]
    positions = [d for d in new["dimensions"] if d["kind"] == "position" and d["axis"] == "x"]
    assert positions and all(d["value"] == pytest.approx(65.0, abs=1e-6) for d in positions)
    actual = [d.get_measurement() for d in ezdxf.readfile(str(dict(after.files)["dxf"])).modelspace().query("DIMENSION")]
    assert any(value == pytest.approx(65.0, abs=1e-6) for value in actual)

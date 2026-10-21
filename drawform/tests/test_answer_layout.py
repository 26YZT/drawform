"""已确认的 P0.2 口径：换基准只允许关联图元变化，不能挪动整张图。"""

import json
import xml.etree.ElementTree as ET

import pytest
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

from drawform.pipeline import render_part
from drawform.service.answers import Answer, bind_scheme
from drawform.service.questions import questions_for
from tests.conftest import _write_step
from tests.test_questions import _derived


def test_changing_one_datum_does_not_move_views_or_unrelated_svg_entities(tmp_path):
    shape = BRepAlgoAPI_Cut(BRepPrimAPI_MakeBox(80.0, 50.0, 12.0).Shape(),
                           BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(15, 25, -1),
                                                         gp_Dir(0, 0, 1)), 5, 14).Shape()).Shape()
    step = _write_step(shape, tmp_path / "offset.step")
    model, features, scheme = _derived(step)
    bound = bind_scheme(scheme, model)
    question = next(q for q in questions_for(model, features, bound)
                    if q.kind == "datum_choice" and q.datum == "A")
    face = max((f for f in bound.datum_candidates if f.axis == "x"), key=lambda f: f.position)
    first_dir, next_dir = tmp_path / "first", tmp_path / "next"
    first_dir.mkdir()
    next_dir.mkdir()
    first = render_part(step, first_dir, stem="same", with_questions=True)
    changed = render_part(step, next_dir, stem="same", with_questions=True,
                          questions_in=(Answer(question.id, face.id),))
    old_files, new_files = dict(first.files), dict(changed.files)
    before, after = [json.loads(files["json"].read_text()) for files in (old_files, new_files)]
    assert before["views"] == after["views"], "选择基准不应让整张图重新居中"
    old_dims = {tuple(d["source"]): d for d in before["dimensions"]}
    new_dims = {tuple(d["source"]): d for d in after["dimensions"]}
    for key, dimension in old_dims.items():
        if dimension["kind"] == "position" and dimension["axis"] == "x":
            assert new_dims[key]["value"] == pytest.approx(65.0)
        else:
            assert new_dims[key] == dimension
    old_svg, new_svg = [ET.parse(files["svg"]).getroot() for files in (old_files, new_files)]
    for layer in ("FRAME", "VISIBLE", "HIDDEN", "CENTER", "TITLE"):
        old = old_svg.find(f"{{*}}g[@id='{layer}']")
        new = new_svg.find(f"{{*}}g[@id='{layer}']")
        assert (None if old is None else ET.tostring(old)) == (None if new is None else ET.tostring(new))
    def unrelated_texts(root):
        return sorted((node.text, node.get("x"), node.get("y"))
                      for node in root.findall("{*}text") if node.text not in ("A", "15", "65"))
    assert unrelated_texts(old_svg) == unrelated_texts(new_svg)

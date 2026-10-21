"""更换已核实幅面后，纸张尺寸与模型测量必须各自保持真实。"""

import json
import re
import xml.etree.ElementTree as ET

import ezdxf
import pytest
from OCP.BRep import BRep_Builder
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.TopoDS import TopoDS_Compound
from OCP.gp import gp_Pnt

from drawform.delivery import DeliveryBlocked
from drawform.pipeline import render_part
from tests.conftest import _write_step


@pytest.mark.parametrize("sheet,width,height", [
    ("A4", 297.0, 210.0), ("A3", 420.0, 297.0), ("A2", 594.0, 420.0),
])
def test_sheet_choice_preserves_model_measurements_and_all_print_page_sizes(
        tmp_path, plate_step, sheet, width, height):
    baseline_dir = tmp_path / "baseline"
    output_dir = tmp_path / sheet
    baseline_dir.mkdir()
    output_dir.mkdir()
    baseline = dict(render_part(plate_step, baseline_dir, sheet="A3").files)
    files = dict(render_part(plate_step, output_dir, sheet=sheet).files)
    original = json.loads(baseline["json"].read_text())
    data = json.loads(files["json"].read_text())

    assert data["sheet"] == sheet
    assert data["constraints"] == original["constraints"]
    assert data["dimensions"], "没有尺寸会使真实测量检查空转"
    assert [(d["source"], d["value"]) for d in data["dimensions"]] == [
        (d["source"], d["value"]) for d in original["dimensions"]]

    svg = ET.parse(files["svg"]).getroot()
    assert float(svg.get("width").removesuffix("mm")) == pytest.approx(width)
    assert float(svg.get("height").removesuffix("mm")) == pytest.approx(height)
    box = re.search(rb"/MediaBox\s*\[\s*([-\d.\s]+)\]", files["pdf"].read_bytes())
    assert box, "PDF 缺失实际打印页面尺寸"
    assert tuple(map(float, box.group(1).split())) == pytest.approx(
        (0, 0, width * 72 / 25.4, height * 72 / 25.4), abs=0.01)

    doc = ezdxf.readfile(files["dxf"])
    assert not doc.audit().errors
    assert doc.layout().dxf.paper_width == pytest.approx(width)
    assert doc.layout().dxf.paper_height == pytest.approx(height)
    native = list(doc.modelspace().query("DIMENSION"))
    assert len(native) == len(data["dimensions"])
    for entity, dim in zip(native, data["dimensions"]):
        assert entity.get_measurement() == pytest.approx(dim["value"], abs=1e-7)


def test_a2_still_refuses_multiple_solids_without_writing_any_drawing(tmp_path):
    compound = TopoDS_Compound()
    builder = BRep_Builder()
    builder.MakeCompound(compound)
    builder.Add(compound, BRepPrimAPI_MakeBox(20, 10, 5).Shape())
    builder.Add(compound, BRepPrimAPI_MakeBox(gp_Pnt(40, 0, 0), 20, 10, 5).Shape())
    step = _write_step(compound, tmp_path / "assembly.step")
    output = tmp_path / "drawing"
    output.mkdir()

    with pytest.raises(DeliveryBlocked) as error:
        render_part(step, output, sheet="A2")

    assert error.value.verdict.failed == ("multiple_solids",)
    assert not list(output.iterdir())

"""Local native-entity and actual PDF/image OCR intake checks."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import ezdxf
from PIL import Image, ImageDraw, ImageFont
import pytest

from drawform.workbench.drawing_intake import IntakeError, run


def request(tmp_path, source, format, name="analysis"):
    destination = tmp_path / name
    payload = {"input_path": str(source.resolve()), "format": format,
               "output_dir": str(destination.resolve()),
               "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
    path = tmp_path / f"{name}-request.json"
    path.write_text(json.dumps(payload))
    return path, destination


def plate(tmp_path, *, thickness=True, units=4, holes=((50, 30, 10),), conflict=False):
    doc = ezdxf.new("R2010")
    doc.units = units
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (100, 0), (100, 60), (0, 60)], close=True)
    for x, y, radius in holes:
        msp.add_circle((x, y), radius)
    if thickness:
        msp.add_text("thickness=8", dxfattribs={"height": 3, "insert": (0, 75)})
    dim = msp.add_linear_dim(base=(0, -10), p1=(0, 0), p2=(100, 0), angle=0,
                             text="200" if conflict else "<>")
    dim.render()
    source = tmp_path / "plate.dxf"
    doc.saveas(source)
    return source


def raster(tmp_path, format, *, thickness=True, length=True, conflict=False, unknown=False, extra_note=None):
    image = Image.new("RGB", (1600, 1100), "white")
    draw = ImageDraw.Draw(image)
    font_path = "/System/Library/Fonts/Supplemental/Arial.ttf"
    font = ImageFont.truetype(font_path, 44)
    draw.rectangle((180, 170, 1150, 700), outline="black", width=5)
    draw.ellipse((570, 330, 760, 520), outline="black", width=5)
    lines = ["RECTANGULAR PLATE"]
    if length:
        lines.append("length=100 mm")
    lines.append("width=60 mm")
    if thickness:
        lines.append("thickness=8 mm")
    lines.append("CENTER THROUGH HOLE DIAMETER=20 mm")
    if conflict:
        lines.append("length=120 mm")
    if unknown:
        lines.append("ADD A BLIND HOLE")
    for i, text in enumerate(lines):
        draw.text((70, 740 + i * 50 if i < 6 else 1040), text, font=font, fill="black")
    if extra_note:
        draw.text((70, 1040), extra_note, font=font, fill="black")
    source = tmp_path / {"pdf": "source.pdf", "png": "source.png", "jpeg": "source.jpg"}[format]
    image.save(source, {"pdf": "PDF", "png": "PNG", "jpeg": "JPEG"}[format], resolution=150, quality=95)
    return source


def test_native_dxf_extracts_real_outline_holes_and_dimension_evidence(tmp_path):
    source = plate(tmp_path, holes=((30, 20, 5), (70, 40, 5)))
    path, out = request(tmp_path, source, "dxf")
    data = run(path)
    assert data["kind"] == "plate" and not data.get("unsupported")
    assert data["parameters"] == {"length_mm": 100, "width_mm": 60, "thickness_mm": 8}
    assert data["holes"] == [{"x_mm": -20, "y_mm": -10, "diameter_mm": 10},
                              {"x_mm": 20, "y_mm": 10, "diameter_mm": 10}]
    assert data["units"] == "mm"
    assert [q["parameter"] for q in data["questions"]] == ["through"]
    assert any(f["parameter"] == "native_dimension" and f["evidence"]["entity_handle"] for f in data["facts"])
    assert (out / "preview.png").read_bytes().startswith(b"\x89PNG")
    assert json.loads((out / "analysis.json").read_text())["source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()


def test_missing_unit_thickness_and_through_are_questions_not_guesses(tmp_path):
    source = plate(tmp_path, thickness=False, units=0)
    path, _ = request(tmp_path, source, "dxf")
    data = run(path)
    assert data["parameters"]["thickness_mm"] is None
    assert data["units"] is None
    assert {q["parameter"] for q in data["questions"]} == {"units", "thickness_mm", "through"}


def test_dimension_override_conflict_is_readonly_and_not_user_selectable(tmp_path):
    source = plate(tmp_path, conflict=True)
    path, _ = request(tmp_path, source, "dxf")
    data = run(path)
    assert data["unsupported"] == "source_conflict"
    assert any(q.get("readonly") and not q["choices"] for q in data["questions"])
    assert any(f["parameter"] == "displayed_dimension_override" for f in data["facts"])


def test_washer_uses_actual_concentric_circle_entities(tmp_path):
    doc = ezdxf.new("R2010")
    doc.units = 4
    msp = doc.modelspace()
    msp.add_circle((10, 20), 25)
    msp.add_circle((10, 20), 10)
    msp.add_text("thickness=5 mm through", dxfattribs={"insert": (0, 50), "height": 3})
    source = tmp_path / "washer.dxf"
    doc.saveas(source)
    path, _ = request(tmp_path, source, "dxf")
    data = run(path)
    assert data["kind"] == "washer" and not data.get("unsupported")
    assert data["parameters"] == {"outer_diameter_mm": 50, "inner_diameter_mm": 20, "thickness_mm": 5}
    assert data["questions"] == []


def test_unknown_native_feature_is_not_omitted(tmp_path):
    source = plate(tmp_path)
    doc = ezdxf.readfile(source)
    doc.modelspace().add_arc((30, 30), 5, 0, 90)
    doc.saveas(source)
    path, _ = request(tmp_path, source, "dxf")
    data = run(path)
    assert data.get("unsupported") and any(q.get("readonly") for q in data["questions"])


@pytest.mark.skipif(sys.platform != "darwin", reason="local macOS Vision/PDFKit runtime")
@pytest.mark.parametrize("format", ["png", "pdf", "jpeg"])
def test_actual_local_ocr_reads_role_labels_and_retains_page_bbox(tmp_path, format):
    source = raster(tmp_path, format)
    path, out = request(tmp_path, source, format)
    data = run(path)
    assert not data.get("unsupported"), data
    assert data["kind"] == "plate" and data["parameters"] == {"length_mm": 100, "width_mm": 60, "thickness_mm": 8}
    assert data["holes"] == [{"x_mm": 0, "y_mm": 0, "diameter_mm": 20}]
    assert data["questions"] == []
    assert any(f["evidence"].get("page_index") == 0 and len(f["evidence"]["bbox"]) == 4 for f in data["facts"])
    assert (out / "ocr.json").is_file() and (out / "preview.png").is_file()


@pytest.mark.skipif(sys.platform != "darwin", reason="local macOS Vision/PDFKit runtime")
def test_actual_local_ocr_missing_thickness_requests_only_that_dimension(tmp_path):
    source = raster(tmp_path, "png", thickness=False)
    path, _ = request(tmp_path, source, "png")
    data = run(path)
    assert not data.get("unsupported"), data
    assert data["parameters"]["thickness_mm"] is None
    assert [q["parameter"] for q in data["questions"]] == ["thickness_mm"]


@pytest.mark.skipif(sys.platform != "darwin", reason="local macOS Vision/PDFKit runtime")
def test_actual_local_ocr_missing_length_does_not_estimate_from_pixels(tmp_path):
    source = raster(tmp_path, "png", length=False)
    path, _ = request(tmp_path, source, "png")
    data = run(path)
    assert not data.get("unsupported"), data
    assert data["parameters"]["length_mm"] is None
    assert data["parameters"]["width_mm"] == 60
    assert [q["parameter"] for q in data["questions"]] == ["length_mm"]


@pytest.mark.skipif(sys.platform != "darwin", reason="local macOS Vision/PDFKit runtime")
@pytest.mark.parametrize("variant", ["conflict", "unknown"])
def test_actual_local_ocr_conflicts_and_unknown_features_block(tmp_path, variant):
    source = raster(tmp_path, "png", **{variant: True})
    path, _ = request(tmp_path, source, "png")
    data = run(path)
    assert data.get("unsupported"), data
    assert any(q.get("readonly") for q in data["questions"])


def test_source_hash_and_nonempty_output_are_rejected(tmp_path):
    source = plate(tmp_path)
    path, out = request(tmp_path, source, "dxf")
    payload = json.loads(path.read_text())
    payload["source_sha256"] = "0" * 64
    path.write_text(json.dumps(payload))
    with pytest.raises(IntakeError, match="摘要"):
        run(path)
    payload["source_sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
    path.write_text(json.dumps(payload))
    out.mkdir()
    (out / "unexpected").write_text("keep")
    with pytest.raises(IntakeError, match="空目录"):
        run(path)


def test_four_native_lines_form_a_rectangle_without_private_metadata(tmp_path):
    doc = ezdxf.new("R2010")
    doc.units = 4
    points = [(10, 20), (110, 20), (110, 80), (10, 80)]
    for index, point in enumerate(points):
        doc.modelspace().add_line(point, points[(index+1) % 4])
    source = tmp_path / "lines.dxf"
    doc.saveas(source)
    path, _ = request(tmp_path, source, "dxf")
    data = run(path)
    assert data["kind"] == "plate" and data["holes"] == [] and not data.get("unsupported")
    assert data["parameters"] == {"length_mm": 100, "width_mm": 60, "thickness_mm": None}
    assert [q["parameter"] for q in data["questions"]] == ["thickness_mm"]


def test_dimlfac_is_not_silently_treated_as_model_geometry(tmp_path):
    source = plate(tmp_path)
    doc = ezdxf.readfile(source)
    dimension = next(iter(doc.modelspace().query("DIMENSION")))
    override = dimension.override()
    override.update({"dimlfac": 2})
    override.commit()
    doc.saveas(source)
    path, _ = request(tmp_path, source, "dxf")
    data = run(path)
    assert data["unsupported"] == "source_conflict"
    assert any("dimlfac" in note for note in data["notes"])


def test_native_hole_count_note_cannot_override_actual_circle_count(tmp_path):
    source = plate(tmp_path, holes=((30, 20, 5), (70, 40, 5)))
    doc = ezdxf.readfile(source)
    doc.modelspace().add_text("4x%%c10 THROUGH", dxfattribs={"height": 3})
    doc.saveas(source)
    path, _ = request(tmp_path, source, "dxf")
    data = run(path)
    assert data["unsupported"] == "source_conflict"
    assert any("数量" in note for note in data["notes"])


def test_actual_child_cli_writes_contract_and_real_preview(tmp_path):
    source = plate(tmp_path)
    path, output = request(tmp_path, source, "dxf")
    source_root = Path(__file__).resolve().parents[1] / "src"
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(source_root)}
    result = subprocess.run([sys.executable, "-m", "drawform.workbench.drawing_intake", str(path)],
                            env=env, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr.decode()
    assert json.loads(result.stdout)["status"] == "analyzed"
    assert json.loads((output / "analysis.json").read_text())["source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert (output / "preview.png").read_bytes().startswith(b"\x89PNG")


@pytest.mark.parametrize("annotation", ["thickness=1/2inch", "DIAMETER=20/2", "diameter=20H7"])
def test_partial_or_fitted_numbers_are_not_silently_taken_as_nominal_dimensions(tmp_path, annotation):
    source = plate(tmp_path, thickness=False)
    doc = ezdxf.readfile(source)
    doc.modelspace().add_text(annotation, dxfattribs={"height": 3})
    doc.saveas(source)
    path, _ = request(tmp_path, source, "dxf")
    data = run(path)
    assert data.get("unsupported") and any(q.get("readonly") for q in data["questions"])


@pytest.mark.skipif(sys.platform != "darwin", reason="local macOS Vision/PDFKit runtime")
@pytest.mark.parametrize("note", ["ADD A BOSS", "ADD A RIB", "MAKE A STEPPED PROFILE", "ADD A TAPPED FEATURE", "ADD A CUSTOM LUG"])
def test_actual_ocr_extra_geometric_requirements_are_never_silently_discarded(tmp_path, note):
    source = raster(tmp_path, "png", extra_note=note)
    path, output = request(tmp_path, source, "png")
    data = run(path)
    ocr = json.loads((output / "ocr.json").read_text())
    assert any(note == item["text"] for page in ocr["pages"] for item in page["texts"])
    assert data.get("unsupported") in {"unsupported_feature", "unexplained_annotation"}, data
    assert any(q.get("readonly") for q in data["questions"])
    assert data["kind"] == "plate" and data["parameters"]["length_mm"] == 100
    assert any(item["status"] == "unexplained" and item["text"] == note
               and item["evidence"]["page_index"] == 0 and len(item["evidence"]["bbox"]) == 4
               for item in data["text_consumption"])
    if note == "ADD A CUSTOM LUG":
        assert data["unsupported"] == "unexplained_annotation"
        assert any(item["parameter"] == "unexplained_annotation" and item["value"] == note
                   and item["evidence"]["text"] == note for item in data["facts"])
        # Matching a legal role must not consume other requirements on that line.
        mixed = raster(tmp_path, "png", extra_note="length=100 mm ADD A CUSTOM LUG")
        mixed_path, _ = request(tmp_path, mixed, "png", "mixed-analysis")
        mixed_data = run(mixed_path)
        assert mixed_data["unsupported"] == "unexplained_annotation", mixed_data
        assert any(item["status"] == "unexplained" and "length_mm" in item["roles"]
                   and "LUG" in item["residual"] for item in mixed_data["text_consumption"])
        # An extra damaged label is not a missing role if a valid value exists.
        duplicate = raster(tmp_path, "png", extra_note="ength=200mm")
        duplicate_path, _ = request(tmp_path, duplicate, "png", "duplicate-analysis")
        duplicate_data = run(duplicate_path)
        assert duplicate_data["unsupported"] == "unexplained_annotation", duplicate_data
        assert duplicate_data["parameters"]["length_mm"] == 100
        assert any(item["status"] == "unexplained" and "200" in item["text"]
                   for item in duplicate_data["text_consumption"])
        # Notes on a PDF page without dimensions still require accounting.
        first = Image.open(raster(tmp_path, "png")).convert("RGB")
        second = Image.new("RGB", first.size, "white")
        font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 44)
        ImageDraw.Draw(second).text((70, 100), "REMOVE A WEDGE", font=font, fill="black")
        pdf = tmp_path / "two-pages.pdf"
        first.save(pdf, "PDF", resolution=150, save_all=True, append_images=[second])
        pdf_path, _ = request(tmp_path, pdf, "pdf", "pdf-notes-analysis")
        pdf_data = run(pdf_path)
        assert pdf_data["unsupported"] == "unexplained_annotation", pdf_data
        assert any(item["status"] == "unexplained" and item["evidence"]["page_index"] == 1
                   and "WEDGE" in item["text"] for item in pdf_data["text_consumption"])


@pytest.mark.skipif(sys.platform != "darwin", reason="local macOS Vision/PDFKit runtime")
def test_actual_ocr_normal_roles_have_a_complete_consumption_record(tmp_path):
    source = raster(tmp_path, "png")
    path, output = request(tmp_path, source, "png")
    data = run(path)
    assert not data.get("unsupported"), data
    assert data["questions"] == []
    assert data.get("text_consumption"), data
    ocr = json.loads((output / "ocr.json").read_text())
    assert len(data["text_consumption"]) == sum(len(page["texts"]) for page in ocr["pages"])
    assert all(item["status"] in {"consumed_role", "allowed_text"} for item in data["text_consumption"])
    allowed = raster(tmp_path, "png", extra_note="DRAWING NO: PLATE-01")
    allowed_path, _ = request(tmp_path, allowed, "png", "metadata-analysis")
    allowed_data = run(allowed_path)
    assert not allowed_data.get("unsupported"), allowed_data
    assert any(item["status"] == "allowed_text" and "PLATE-01" in item["text"]
               for item in allowed_data["text_consumption"])
    unread = raster(tmp_path, "png", length=False, extra_note="ength=100mm")
    unread_path, _ = request(tmp_path, unread, "png", "unread-analysis")
    unread_data = run(unread_path)
    assert not unread_data.get("unsupported"), unread_data
    assert unread_data["parameters"]["length_mm"] is None
    assert [item["parameter"] for item in unread_data["questions"]] == ["length_mm"]
    assert any(item["status"] == "unread_role" and item["roles"] == ["length_mm"]
               for item in unread_data["text_consumption"])
    assert not any(item["parameter"] == "length_mm" for item in unread_data["facts"])

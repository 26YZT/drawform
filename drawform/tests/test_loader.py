import pytest

from drawform.geometry.loader import StepImportError, import_step


def test_import_step_reports_accurate_bounding_box(plate_step):
    model = import_step(plate_step)

    assert model.bbox == pytest.approx((80.0, 50.0, 12.0), abs=1e-6)


def test_import_step_rejects_unreadable_file(broken_step):
    with pytest.raises(StepImportError) as exc:
        import_step(broken_step)

    assert "broken.step" in str(exc.value)


def test_import_step_rejects_shape_with_no_volume(tmp_path):
    """STEP 能解析但几何为空（例如只有参考几何），不能当成合法零件往下走。"""
    empty = tmp_path / "empty.step"
    empty.write_text(
        "ISO-10303-21;\nHEADER;\nFILE_DESCRIPTION((''),'2;1');\n"
        "FILE_NAME('empty.step','2026-01-01',(''),(''),'','','');\n"
        "FILE_SCHEMA(('AUTOMOTIVE_DESIGN'));\nENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n",
        encoding="utf-8",
    )

    with pytest.raises(StepImportError):
        import_step(empty)

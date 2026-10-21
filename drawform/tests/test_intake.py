"""取数体检：数据到手的第一道判定，决定这批图能用于哪几类对照。

这里守的是"能不能用"，不是"画得好不好"。判定错的方向很贵：把读不出尺寸的图当成
可用样本，后面所有基准选择/尺寸选择的统计都是在拿版式冒充数据。
"""

from __future__ import annotations

import ezdxf
import pytest

from drawform.eval.intake import (
    USE_LAYOUT,
    USE_PAIRING,
    USE_RULER,
    USE_TOLERANCE,
    inspect_dxf,
)


def _write(doc, tmp_path, name="sample.dxf"):
    path = tmp_path / name
    doc.saveas(str(path))
    return path


def _drawing_with_dims(count=3, title_block=True):
    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    for i in range(count):
        msp.add_line((0.0, i * 10.0), (40.0, i * 10.0))
        msp.add_linear_dim(base=(0.0, i * 10.0 + 5.0),
                           p1=(0.0, i * 10.0), p2=(40.0, i * 10.0)).render()
    if title_block:
        block = doc.blocks.new("TITLEBLOCK")
        block.add_attdef("NAME", insert=(0.0, 0.0))
        ref = msp.add_blockref("TITLEBLOCK", (60.0, 0.0))
        ref.add_auto_attribs({"NAME": "某厂"})
    return doc


def test_native_dimensions_make_the_sheet_usable_for_pairing_and_ruler(tmp_path):
    report = inspect_dxf(_write(_drawing_with_dims(), tmp_path))

    assert report.ok is True
    assert report.dims == 3
    assert USE_PAIRING in report.uses
    assert USE_RULER in report.uses
    assert report.flags == ()


def test_a_sheet_without_native_dimensions_is_not_claimed_to_have_them(tmp_path):
    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    for i in range(12):
        msp.add_line((0.0, i * 5.0), (40.0, i * 5.0))
    for i in range(6):
        msp.add_text("40", dxfattribs={"height": 3.5,
                                       "insert": (10.0, i * 5.0 + 2.0)})
    report = inspect_dxf(_write(doc, tmp_path))

    assert report.ok is True
    assert report.dims == 0
    assert "no_native_dimensions" in report.flags
    assert report.uses == (USE_LAYOUT,)


def test_a_dwg_is_reported_unreadable_and_usable_for_nothing(tmp_path):
    path = tmp_path / "sample.dwg"
    path.write_bytes(b"MCAD\x00\x00\x00\x00" + b"\x00" * 4096)
    report = inspect_dxf(path)

    assert report.ok is False
    assert report.reason == "not_dxf"
    assert report.uses == ()


def test_garbled_lettering_is_flagged_instead_of_counted_as_vocabulary(tmp_path):
    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    msp.add_text("材料 锟斤拷拷", dxfattribs={"insert": (0.0, 0.0)})
    msp.add_text("40", dxfattribs={"insert": (0.0, 10.0)})
    report = inspect_dxf(_write(doc, tmp_path))

    assert report.garbled == 1
    assert "garbled_text" in report.flags


def test_a_dimension_carrying_a_tolerance_counts_toward_the_tolerance_use(tmp_path):
    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    msp.add_line((0.0, 0.0), (40.0, 0.0))
    msp.add_linear_dim(base=(0.0, 5.0), p1=(0.0, 0.0), p2=(40.0, 0.0),
                       text="<>%<\\A1;+0.010;%<\\A1;-0.000").render()
    report = inspect_dxf(_write(doc, tmp_path))

    assert report.tolerances == 1
    assert USE_TOLERANCE in report.uses


def test_a_fit_code_alongside_the_diameter_counts_as_a_tolerance(tmp_path):
    """`⌀20H7` 是国产图上最常见的公差写法，只认 ± 会把整批图判成"没有公差"。"""
    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    msp.add_line((0.0, 0.0), (40.0, 0.0))
    msp.add_linear_dim(base=(0.0, 5.0), p1=(0.0, 0.0), p2=(40.0, 0.0),
                       text="\\c1;\\S\\U20H7;").render()
    msp.add_text("⌀20H7", dxfattribs={"insert": (0.0, 20.0)})
    report = inspect_dxf(_write(doc, tmp_path))

    assert report.tolerances == 1


def test_dimensions_without_any_vector_line_do_not_enable_the_ruler(tmp_path):
    doc = ezdxf.new("R2018")
    doc.modelspace().add_linear_dim(base=(0.0, 5.0), p1=(0.0, 0.0),
                                    p2=(40.0, 0.0)).render()
    report = inspect_dxf(_write(doc, tmp_path))

    assert report.dims == 1
    assert USE_PAIRING in report.uses
    assert USE_RULER not in report.uses
    assert "no_vector_lines" in report.flags


def test_a_sheet_without_block_attributes_says_so(tmp_path):
    report = inspect_dxf(_write(_drawing_with_dims(1, title_block=False), tmp_path))

    assert report.attribs == 0
    assert "no_block_attributes" in report.flags


def test_block_attributes_are_counted(tmp_path):
    doc = ezdxf.new("R2018")
    block = doc.blocks.new("TITLEBLOCK")
    block.add_attdef("NAME", insert=(0.0, 0.0))
    ref = doc.modelspace().add_blockref("TITLEBLOCK", (0.0, 0.0))
    ref.add_auto_attribs({"NAME": "某厂"})
    report = inspect_dxf(_write(doc, tmp_path))

    assert report.attribs == 1
    assert "no_block_attributes" not in report.flags


@pytest.mark.parametrize("fixture_name", ["stepped_plate_step", "bearing_seat_step"])
def test_our_own_dxf_would_pass_its_own_intake_check(fixture_name, request, tmp_path):
    """体检器必须先认出我们自己画的图，否则它判"集团图纸没有尺寸"就毫无说服力。"""
    from drawform.pipeline import render_part

    step = request.getfixturevalue(fixture_name)
    render_part(step, tmp_path)

    report = inspect_dxf(tmp_path / f"{step.stem}.dxf")
    assert report.ok is True
    assert report.dims >= 1, f"{fixture_name} 写出的 DXF 读不回尺寸"
    assert USE_PAIRING in report.uses and USE_RULER in report.uses



def test_dimensions_defined_inside_a_block_are_counted(tmp_path):
    """尺寸在块定义里是合法画法（装配图、模板、批量出图都会这样）。
    只遍历布局会把一张能用的图判成"没有尺寸"，那是方向最坏的错。"""
    doc = ezdxf.new("R2018")
    block = doc.blocks.new("VIEWBLOCK")
    block.add_line((0.0, 0.0), (40.0, 0.0))
    block.add_linear_dim(base=(0.0, 5.0), p1=(0.0, 0.0), p2=(40.0, 0.0)).render()
    doc.modelspace().add_blockref("VIEWBLOCK", (0.0, 0.0))

    report = inspect_dxf(_write(doc, tmp_path))

    assert report.dims == 1
    assert USE_PAIRING in report.uses

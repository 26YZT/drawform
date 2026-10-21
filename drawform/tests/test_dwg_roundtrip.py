"""DWG 往返判定：把 DXF 交给 AutoCAD 存成 DWG 再导回 DXF，尺寸还在不在。

这台机器上没有 CAD，所以**转换那一步只能到集团某台装了 AutoCAD 的机器上跑**；
这里做的是"跑完之后谁来判、怎么判"。判错方向的两种都要防：
把丢了尺寸的转换判成成功（交出去的是打不开或没有实尺的 DWG），
以及把正常重新生成的块判成失败（以后没人再信这个检查）。
"""

from __future__ import annotations

import ezdxf
import pytest

from drawform.eval.dwg_roundtrip import (
    DIMENSIONS_LOST,
    shown_label,
    LABELS_LOST,
    TARGET_UNREADABLE,
    compare_pair,
)


@pytest.fixture(scope="session")
def own_dxf(bearing_seat_step, tmp_path_factory):
    """对照组：我们自己出的图，往返到自己身上必须判"通过"。"""
    import pathlib

    from drawform.pipeline import render_part

    out = tmp_path_factory.mktemp("rt")
    render_part(bearing_seat_step, out)
    return out / f"{bearing_seat_step.stem}.dxf"


def _copy_with(source, target, mutate):
    doc = ezdxf.readfile(str(source))
    mutate(doc)
    doc.saveas(str(target))
    return target


def test_our_own_sheet_compared_against_itself_passes(own_dxf, tmp_path):
    verdict = compare_pair(own_dxf, own_dxf)

    assert verdict.ok is True, f"判定器对自家产出误报：{verdict.failed} {verdict.lost}"
    assert verdict.src_dims == verdict.dst_dims > 0


def test_a_target_that_lost_its_dimensions_fails(own_dxf, tmp_path):
    def strip(doc):
        for space in list(doc.layouts) + list(doc.blocks):
            for entity in [e for e in space if e.dxftype() == "DIMENSION"]:
                space.delete_entity(entity)

    target = _copy_with(own_dxf, tmp_path / "stripped.dxf", strip)
    verdict = compare_pair(own_dxf, target)

    assert DIMENSIONS_LOST in verdict.failed
    assert verdict.dst_dims == 0


def test_a_changed_number_is_named_not_just_counted(own_dxf, tmp_path):
    """只比条数会放过"80 变成 8"这种最坏的情况：条数对得上，图纸却全错。

    改的是尺寸几何块里那串**显示文字** —— 改 DIMENSION 的 dimtext 不会动已经渲染好的块，
    那种改法测不出判据。
    """
    def change_one(doc):
        dims = [e for space in doc.layouts for e in space if e.dxftype() == "DIMENSION"]
        block = dims[0].get_geometry_block()
        letter = next(e for e in block if e.dxftype() in ("TEXT", "MTEXT"))
        letter.dxf.text = "8"

    target = _copy_with(own_dxf, tmp_path / "changed.dxf", change_one)
    verdict = compare_pair(own_dxf, target)

    assert LABELS_LOST in verdict.failed
    assert verdict.lost, "要能点名丢了哪个显示值"


def test_an_unreadable_target_is_reported_as_such(own_dxf, tmp_path):
    bad = tmp_path / "not_a_dxf.dxf"
    bad.write_bytes(b"MCAD\x00\x00\x00\x00" + b"\x00" * 2048)

    verdict = compare_pair(own_dxf, bad)

    assert verdict.ok is False
    assert verdict.failed == (TARGET_UNREADABLE,)


def test_extra_dimensions_are_reported_but_not_a_failure(own_dxf, tmp_path):
    """转换器多吐几条尺寸不是事故（那是超集），点名即可 —— 判据只守"有没有丢"。"""
    def add_one(doc):
        msp = doc.modelspace()
        msp.add_linear_dim(base=(0.0, -30.0), p1=(0.0, -30.0),
                           p2=(10.0, -30.0), text="10").render()

    target = _copy_with(own_dxf, tmp_path / "extra.dxf", add_one)
    verdict = compare_pair(own_dxf, target)

    assert verdict.ok is True
    assert "10" in verdict.added


def test_losing_one_of_four_identical_dimensions_is_still_a_loss(tmp_path):
    """同一个值在一张图上标四次是常事。按"有几种数字"比，会把丢一条判成无损。"""
    import pathlib

    from drawform.eval.dwg_roundtrip import compare_pair

    src = (pathlib.Path(__file__).resolve().parents[2]
           / "tools" / "dwg_verify" / "samples" / "part_0004.dxf")

    def drop_one(doc):
        for space in doc.layouts:
            for entity in list(space):
                if entity.dxftype() == "DIMENSION" and shown_label(entity) == "40":
                    space.delete_entity(entity)
                    return

    target = _copy_with(src, tmp_path / "one_less.dxf", drop_one)
    verdict = compare_pair(src, target)

    assert (verdict.src_dims, verdict.dst_dims) == (17, 16)
    assert DIMENSIONS_LOST in verdict.failed
    assert "40" in verdict.lost

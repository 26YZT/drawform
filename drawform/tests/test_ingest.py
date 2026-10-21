"""把外部 DXF 读成 `Drawing`，好让生产用的互斥检测器能量真图。

为什么要这一层：`text/line`（我们 668 处）与 `leader/line`（185 处）两条规则
**从来没有对照组**，所以一直只当线索、不敢拿来改产物。MISUMI 那 1988 张公开图
有矢量线也有文字，够量一次"真图上这条规则会不会也报几百处"。

纪律：探针必须和生产用同一份实现 —— 这里只负责把 DXF 变成 `Drawing`，
判定仍然走 `output.collide.find_collisions`。读不了的实体**计数并报**，绝不静默丢，
否则"真图 0 处冲突"可能只是"我们什么都没读进来"。
"""

from __future__ import annotations

import ezdxf
import pytest

from drawform.eval.ingest import read_sheet
from drawform.output.collide import find_collisions, text_box
from drawform.output.drawing import CHAR_WIDTH


def _write(tmp_path, build, name="sheet.dxf"):
    doc = ezdxf.new("R2018")
    build(doc.modelspace())
    path = tmp_path / name
    doc.saveas(str(path))
    return path


def test_lines_and_circles_become_drawing_items(tmp_path):
    def build(msp):
        msp.add_line((0.0, 0.0), (40.0, 0.0), dxfattribs={"layer": "OUTLINE"})
        circle = msp.add_circle((20.0, 10.0), 5.0, dxfattribs={"layer": "OUTLINE"})
        msp.add_lwpolyline([(0.0, 0.0), (0.0, 10.0)], dxfattribs={"layer": "CENTER"})
        del circle

    ingested = read_sheet(_write(tmp_path, build))

    assert len(ingested.drawing.items) == 3
    assert {item.layer for item in ingested.drawing.items} == {"OUTLINE", "CENTER"}


def test_a_line_crossing_a_text_is_reported_by_the_production_detector(tmp_path):
    """这条是整层的意义所在：读进来的图要能被**生产那份**检测器判出冲突。"""
    def build(msp):
        msp.add_text("40", dxfattribs={"insert": (0.0, 0.0), "height": 3.5,
                                       "layer": "DIM_TEXT"})
        msp.add_line((-20.0, 1.0), (60.0, 1.0), dxfattribs={"layer": "OUTLINE"})

    ingested = read_sheet(_write(tmp_path, build))
    kinds = [c.kind for c in find_collisions(ingested.drawing)]

    assert kinds == ["text/line"]


def test_the_letter_height_stored_in_the_file_is_used_for_the_box(tmp_path):
    """外部图的字高不是我们的 3.5 mm。按文件里的算，否则对照组是假的。"""
    def build(msp):
        msp.add_text("40", dxfattribs={"insert": (0.0, 0.0), "height": 12.0})

    ingested = read_sheet(_write(tmp_path, build))
    box = text_box(ingested.drawing.texts[0])

    assert box[3] - box[1] == pytest.approx(12.0)
    assert box[2] - box[0] == pytest.approx(CHAR_WIDTH * 12.0 * len("40"))


def test_entities_we_cannot_read_are_counted_not_silently_dropped(tmp_path):
    def build(msp):
        msp.add_line((0.0, 0.0), (10.0, 0.0))
        msp.add_solid([(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)], dxfattribs={"layer": "0"})

    ingested = read_sheet(_write(tmp_path, build))

    assert ingested.drawing.items, "至少那条线要进来"
    assert ingested.skipped.get("SOLID", 0) == 1

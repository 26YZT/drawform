"""PDF 出口。与 SVG 共用 `output.drawing` 那份几何，只换序列化器。

刻意不做「先出 SVG 再转 PDF」：转换器要重新理解 mm 线宽、GB/T 线型、字头方向，
每理解错一处就多出一张对不上的图。共用同一份坐标、各自只写自己那套语法，
才是"网页上的图与纸上的图是同一张"的可证实现法。
"""
import re
import zlib

import pytest

from drawform.output.drawing import build_drawing
from drawform.output.pdf import MissingGlyph, write_pdf
from tests.test_svg import _placed, _sheet

MM_TO_PT = 72.0 / 25.4


def _text_blocks(raw: bytes) -> int:
    """页面内容流里的文字块数。matplotlib 默认压缩内容流，不解压什么都看不见。"""
    total = 0
    for blob in re.findall(rb"stream\r?\n(.*?)endstream", raw, re.S):
        try:
            total += zlib.decompress(blob).count(b"BT")
        except zlib.error:
            total += blob.count(b"BT")
    return total


def test_pdf_page_is_the_declared_sheet_size(tmp_path, bearing_seat_step):
    """PDF 以点为单位、matplotlib 以英寸构图。少乘一次 mm→pt 就得到一张缩小的图
    —— 客户拿到的是 A4 纸上印了半张 A3。
    """
    _model, _features, _projections, placed = _placed(bearing_seat_step)
    box = re.search(rb"/MediaBox \[([-\d. ]+)\]",
                    write_pdf(placed, tmp_path / "sheet.pdf").read_bytes())
    assert box, "PDF 里没有 MediaBox，页面尺寸无从谈起"

    _x, _y, width, height = (float(v) for v in box.group(1).split())
    assert width == pytest.approx(420.0 * MM_TO_PT, abs=0.6)
    assert height == pytest.approx(297.0 * MM_TO_PT, abs=0.6)


def test_every_lettering_on_the_sheet_is_written_into_the_pdf(tmp_path,
                                                             bearing_seat_step):
    """图上的每个字都必须真的进页面。matplotlib 找不到字形是**静默留白**，
    文件照样生成、大小看着正常 —— 只能数内容流里的文字块。
    """
    placed, dims, _markup = _sheet(tmp_path, bearing_seat_step, "screen.svg")
    drawing = build_drawing(placed, "A3", dims)
    raw = write_pdf(placed, tmp_path / "paper.pdf", dimensions=dims).read_bytes()

    assert b"/Font" in raw, "页面里没有字体对象，说明一个字都没写进去"
    assert drawing.texts, "这张图上没有文字，那条断言会空转"
    assert _text_blocks(raw) == len(drawing.texts), \
        f"图上有 {len(drawing.texts)} 段文字，PDF 里只有 {_text_blocks(raw)} 段"


def test_a_glyph_no_installed_font_can_draw_stops_the_pdf(tmp_path,
                                                          bearing_seat_step):
    """服务器上没有中文字体时，宁可报错也不要交出一张全是空框的图。

    这是真实部署风险：Linux 上的 matplotlib 通常只带 DejaVu，
    「一般公差 ISO 2768-m」会画成空白。探针用 U+FFF0 —— 一个未分配的码位，
    只有诊断用的兜底字体声称能画，而那种字体本身就不该出现在交付物里。
    """
    _model, _features, _projections, placed = _placed(bearing_seat_step)

    with pytest.raises(MissingGlyph):
        write_pdf(placed, tmp_path / "tofu.pdf", notes=("\uFFF0",))

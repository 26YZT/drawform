"""技术注记与审核标识不能挤进标题栏，也不能被模拟接受状态抹掉。"""
import xml.etree.ElementTree as ET

import ezdxf
import pytest

from drawform.output.paper_notes import UNREVIEWED, NotesOverflow, tolerance_labels, width_of, wrap
from drawform.output.sheet import make_frame
from drawform.output.titleblock import BLOCK_W, TitleMeta, title_block
from drawform.pipeline import render_part


def test_long_tolerance_wraps_outside_grid_without_losing_numbers():
    inner = make_frame().inner
    text = '一般公差 ISO 2768-m；专门公差：' + '、'.join(['4.224H7'] * 12)
    rows = tolerance_labels(text, inner)
    assert len(rows) > 1
    assert ''.join(row[0] for row in rows).replace(' ', '') == text.replace(' ', '')
    assert all(x + width_of(row, h) <= inner[2] - BLOCK_W - 5 for row, x, y, h in rows)
    assert sum(row.count('4.224H7') for row, *_ in rows) == 12


def test_unbreakable_note_is_reported_instead_of_truncated():
    with pytest.raises(NotesOverflow, match='未截断'):
        wrap('123456789.987654321', 8)


def test_title_label_widths_and_cell_values_remain_bounded():
    inner = make_frame().inner
    lines, texts = title_block(TitleMeta(code='part-001'), inner)
    cells = [run for run in lines if len(run) == 5]
    for content, x, y, height in texts:
        containing = [run for run in cells if run[0][0] < x < run[1][0] and run[0][1] < y < run[2][1]]
        cell = min(containing, key=lambda r: (r[1][0]-r[0][0])*(r[2][1]-r[0][1]))
        assert x + width_of(content, height) <= cell[1][0]
        assert y + height <= cell[2][1]


def test_all_generated_formats_keep_unreviewed_notice(plate_step, tmp_path, monkeypatch):
    from matplotlib.axes import Axes
    pdf_texts = []
    original = Axes.text

    def record(self, x, y, text, *args, **kwargs):
        pdf_texts.append(text)
        return original(self, x, y, text, *args, **kwargs)

    monkeypatch.setattr(Axes, 'text', record)
    result = render_part(plate_step, tmp_path)
    files = dict(result.files)
    root = ET.fromstring(files['svg'].read_text())
    assert sum(node.text == UNREVIEWED for node in root.findall('{*}text')) == 1
    assert next(node for node in root.findall('{*}text') if node.text == '更改文件号').get('font-size') == '2.5'
    doc = ezdxf.readfile(files['dxf'])
    assert sum(entity.dxf.text == UNREVIEWED for entity in doc.modelspace().query('TEXT')) == 1
    assert pdf_texts.count(UNREVIEWED) == 1
    assert files['pdf'].read_bytes().startswith(b'%PDF')

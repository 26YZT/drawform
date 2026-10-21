"""图面注记：保留原文，不截断数值；未审状态不由 accepted 或签字字符串推断。"""

import re
import unicodedata

UNREVIEWED = "未经制图审核"
HEIGHT = 3.5
PITCH = 6.0


class NotesOverflow(ValueError):
    pass


def width_of(text, height):
    return sum(1.0 if unicodedata.east_asian_width(c) in ('W', 'F') else .7 for c in text) * height


def wrap(text, width, height=HEIGHT):
    """中文可按字换行，数值/代号连续串整体保留，不能把12.35拆成两行。"""
    rows, current = [], ''
    for token in re.findall(r'[A-Za-z0-9_.+−±⌀×°/\-]+|.', text):
        if width_of(token, height) > width:
            raise NotesOverflow('图面注记存在放不下的完整数值或代号，请调整图幅或元数据；未截断内容')
        if current and width_of(current + token, height) > width:
            rows.append(current.rstrip());current = token.lstrip()
        else:
            current += token
    if current:rows.append(current.rstrip())
    return rows


def tolerance_labels(text, inner):
    from drawform.output.titleblock import BLOCK_W, BLOCK_H
    x0,y0,x1,_ = inner
    rows = wrap(text, x1-BLOCK_W-x0-5.0) if text else []
    if len(rows)*PITCH > BLOCK_H-2*PITCH:
        raise NotesOverflow('公差说明超出独立技术注记区，未写入标题栏其他单元格')
    return [(row,x0,y0+HEIGHT+(len(rows)-i-1)*PITCH,HEIGHT) for i,row in enumerate(rows)]


def _other_rows(notes, inner, tolerance_note):
    from drawform.output.titleblock import BLOCK_W, BLOCK_H
    x0,y0,x1,_ = inner
    rows=[UNREVIEWED]
    for note in notes:
        if tolerance_note and (note==tolerance_note or note.startswith('一般公差')):continue
        if note==UNREVIEWED:continue
        rows.extend(wrap(note,x1-BLOCK_W-x0-5.0))
    return rows


def required_height(notes, inner, tolerance_note=''):
    """Actual note rows reserve paper space; text and numerical tokens stay intact."""
    from drawform.output.titleblock import BLOCK_H
    rows = _other_rows(notes, inner, tolerance_note)
    tolerance = tolerance_labels(tolerance_note,inner)
    lower = max((y+height for _s,_x,y,height in tolerance),default=inner[1])
    height = max(BLOCK_H, lower-inner[1]+(len(rows)-1)*PITCH+2*HEIGHT+1.0)
    if height >= inner[3]-inner[1]:
        raise NotesOverflow('图面技术注记区放不下全部说明，未裁切、缩字或删除内容')
    return height


def other_labels(notes, inner, tolerance_note=''):
    rows = _other_rows(notes, inner, tolerance_note)
    x0,y0,_,_ = inner
    top = y0+required_height(notes,inner,tolerance_note)-HEIGHT-1.0
    return [(row,x0,top-i*PITCH,HEIGHT) for i,row in enumerate(rows)]

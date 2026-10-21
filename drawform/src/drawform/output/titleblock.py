"""标题栏：按 GB/T 10609.1-2008 图2 的三列式分区。

抄录与核对记录在 `docs/design/gbt10609_titleblock.md`，本文件只放实现用的数。

已按原文核对的（2026-10-08 取得标准正文第2页与附录A 图A.1）：
  · 外廓 180 宽 × 56max 高（图2/图3 一致）；
  · 三列宽 80 / 50 / 50（图A.1 底部标注与 180−80−50 互算一致）；
  · 左列六格宽 10/10/16/16/12/16（上行=更改区表头）与 12/12/16/12/12/16（下行=签字区）；
  · 四区构成：更改区、签字区、其他区、名称及代号区（4.1 条）；
  · 签字区角色：设计、审核、工艺、标准化、批准（4.2.2 条）；
  · 单位名称"必要时可不予填写"（4.2.4.1）、重量以千克为单位可不写单位（4.2.3.3）、
    采用第一角画法时投影符号可省略标注（4.2.3.6）。

**未逐格核对的**：中列与右列的内部行高。左列行高取 8mm 是因为 56 = 7 × 8 整除、
且图A.1 左列纵向标注是"行数 × 8"的形式；中/右列只是把 56 按格数等分。
所以 `cell_heights_verified` 只覆盖左列，其余在出图说明里明说。

不可谈判的一条：**STEP 里没有图样名称、材料、任何签字人。这些格子必须留空。**
留空是"等人来填"，编一个是假图纸。
"""

from __future__ import annotations

from dataclasses import dataclass

# --- 外廓与三列宽（已核对）-------------------------------------------------
BLOCK_W = 180.0
BLOCK_H = 56.0          # 56max：不超过 56，不是定值
CHANGE_W = 80.0         # 左列：更改区 + 签字区
OTHER_W = 50.0          # 中列：其他区
NAME_W = 50.0           # 右列：名称及代号区

ROW_H = 8.0             # 左列行高，56 = 7 × 8
CHANGE_ROWS = 2         # 更改区：表头行 + 一行填写
SIGN_ROWS = 5           # 签字区：设计/审核/工艺/标准化/批准

CHANGE_COLS = (10.0, 10.0, 16.0, 16.0, 12.0, 16.0)
CHANGE_LABELS = ("标记", "处数", "分区", "更改文件号", "签名", "年、月、日")
SIGN_COLS = (12.0, 12.0, 16.0, 12.0, 12.0, 16.0)
SIGN_ROLES = ("设计", "审核", "工艺", "标准化", "批准")

#: 其他区的格子。名称照 4.2.3 条逐项列全，不再只挑三个。
OTHER_CELLS = (("material", "材料标记"), ("stage", "阶段标记"), ("weight", "重量"),
               ("scale", "比例"), ("sheets", "共 张 第 张"))

#: 名称及代号区。投影符号按 4.2.3.6 在第一角画法下省略。
NAME_CELLS = (("org", "单位名称"), ("name", "图样名称"), ("code", "图样代号"))

LABEL_H = 2.5
VALUE_H = 3.5


@dataclass(frozen=True)
class TitleMeta:
    """标题栏内容。全部可空 —— 空是事实，不是待修的缺陷。"""

    name: str = ""
    code: str = ""
    org: str = ""
    material: str = ""
    stage: str = ""
    weight: str = ""
    scale: str = ""
    sheets: str = ""
    signers: tuple[str, ...] = ()      # 设计/审核/工艺/标准化/批准，按序，缺位留空
    changes: tuple = ()                # (标记, 处数, 分区, 更改文件号, 签名, 日期)
    tolerance_note: str = ""

    @classmethod
    def from_step_name(cls, stem: str, tolerance_note: str = "") -> "TitleMeta":
        """只有文件名可用时：填图样代号，其余全空。

        不拿 stem 冒充图样名称 —— 文件名和零件名称不是一回事，填进去看着更完整，
        但那正是假图纸的样子。
        """
        return cls(code=stem, tolerance_note=tolerance_note)

    def signer(self, index: int) -> str:
        return self.signers[index] if index < len(self.signers) else ""

    def filled_cells(self) -> list[tuple[str, str]]:
        """所有 (标签, 值) 对，供出图说明统计"填了几格、空了几格"。"""
        pairs = [(label, getattr(self, key, "")) for key, label in OTHER_CELLS]
        pairs += [(label, getattr(self, key, "")) for key, label in NAME_CELLS]
        pairs += [(role, self.signer(i)) for i, role in enumerate(SIGN_ROLES)]
        return pairs


def _cell(lines, texts, x, y, w, h, label, value):
    """一格：边框 + 小字标签 + 大字值。值空着就留白，不填占位符。"""
    from drawform.output.paper_notes import NotesOverflow, wrap

    lines.append(((x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)))
    labels = wrap(label, w - 3.0, LABEL_H)
    values = wrap(value, w - 4.0, VALUE_H)
    label_bottom = y + h - 1.0 - len(labels) * (LABEL_H + .5)
    if values and y + 2.0 + len(values) * (VALUE_H + .5) > label_bottom:
        raise NotesOverflow(f'标题栏“{label}”内容超出单元格；未截断或跨格绘制')
    for i, row in enumerate(labels):
        texts.append((row, x + 1.5, y + h - LABEL_H - 1.0 - i * (LABEL_H + .5), LABEL_H))
    for i, row in enumerate(values):
        texts.append((row, x + 2.0, y + 2.0 + (len(values) - i - 1) * (VALUE_H + .5), VALUE_H))


def title_block(meta: TitleMeta, inner) -> tuple[list, list]:
    """返回 (折线, 文字)，坐标为图纸毫米、原点在设计区左下角。

    `inner` 是图框设计区四元组 —— 标题栏贴它的右下角，这是不需要数表也能定的一件事。
    """
    _, y0, x1, _ = inner
    left, bottom = x1 - BLOCK_W, y0
    lines: list = []
    texts: list = []

    lines.append(((left, bottom), (left + BLOCK_W, bottom), (left + BLOCK_W, bottom + BLOCK_H),
                  (left, bottom + BLOCK_H), (left, bottom)))
    # 三列分隔
    for offset in (CHANGE_W, CHANGE_W + OTHER_W):
        lines.append(((left + offset, bottom), (left + offset, bottom + BLOCK_H)))

    # 左列：上更改区、下签字区
    sign_h = SIGN_ROWS * ROW_H
    lines.append(((left, bottom + sign_h), (left + CHANGE_W, bottom + sign_h)))

    x = left
    for w, label in zip(CHANGE_COLS, CHANGE_LABELS):
        _cell(lines, texts, x, bottom + BLOCK_H - CHANGE_ROWS * ROW_H, w,
              CHANGE_ROWS * ROW_H, label, "")
        x += w
    x = left
    for w, role in zip(SIGN_COLS, SIGN_ROLES):
        _cell(lines, texts, x, bottom, w, sign_h, role, "")
        x += w

    # 中列：其他区，按格数等分高度（内部行高未逐格核对，见模块 docstring）
    each = BLOCK_H / len(OTHER_CELLS)
    for index, (key, label) in enumerate(OTHER_CELLS):
        _cell(lines, texts, left + CHANGE_W, bottom + BLOCK_H - (index + 1) * each,
              OTHER_W, each, label, getattr(meta, key, ""))

    # 右列：名称及代号区
    each = BLOCK_H / len(NAME_CELLS)
    for index, (key, label) in enumerate(NAME_CELLS):
        _cell(lines, texts, left + CHANGE_W + OTHER_W,
              bottom + BLOCK_H - (index + 1) * each, NAME_W, each, label,
              getattr(meta, key, ""))

    if meta.tolerance_note:
        from drawform.output.paper_notes import tolerance_labels
        texts.extend(tolerance_labels(meta.tolerance_note, inner))
    return lines, texts

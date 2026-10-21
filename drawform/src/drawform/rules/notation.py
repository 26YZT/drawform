"""工程图标注文本语法（ISO 风格，小数用逗号）。

解析结果是有序的 (kind, value) 序列而不是一个多字段结构：真实标注是位置敏感的
（"⌀4 [深度]6 [沉孔]6,5 [深度]4" 里两个深度分属主孔与沉孔），拍平成字段会丢掉这个信息。
认不出的片段一律以 unknown 原样留着，绝不猜含义 —— "H 12" 至今没有可靠解释。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

NUMBER = r"\d+(?:,\d+)?"

#: 按顺序尝试；⌴/⌵ 必须在 ⌀ 之前，pcd 必须在裸数字之前，count 必须在 size 之前，
#: angle 必须在 size 之前（否则 "82°" 会被读成一个普通数值而丢掉角度单位）。
PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    ("count", re.compile(rf"(?P<count>\d+)×")),
    ("pcd", re.compile(rf"EQUALLY SPACED ON ⌀(?P<pcd>{NUMBER})")),
    ("counterbore", re.compile(rf"⌴⌀?(?P<counterbore>{NUMBER})")),
    ("countersink", re.compile(rf"⌵⌀?(?P<countersink>{NUMBER})")),
    ("diameter", re.compile(rf"⌀(?P<diameter>{NUMBER})")),
    ("depth", re.compile(rf"↧(?P<depth>{NUMBER})")),
    ("tolerance", re.compile(rf"±(?P<tolerance>{NUMBER})")),
    ("chamfer", re.compile(rf"(?<![A-Za-z0-9])C(?P<chamfer>{NUMBER})")),
    ("radius", re.compile(rf"(?<![A-Za-z0-9])R(?P<radius>{NUMBER})")),
    ("angle", re.compile(rf"(?P<angle>{NUMBER})°")),
    ("across_flats", re.compile(r"A/F")),
    ("spotface", re.compile(r"(?<![A-Za-z])SF(?![A-Za-z])")),
    ("size", re.compile(rf"(?<![\d,])(?P<size>{NUMBER})")),
    ("sep", re.compile(r"×|PCD")),
)


@dataclass(frozen=True)
class Token:
    kind: str
    value: float | int | bool | str


@dataclass(frozen=True)
class DatumReference:
    """一个基准参考格。`E-F` 是复合基准，`HⓂ` 带修饰符 —— 都原样留在 raw 里。"""

    raw: str
    letters: tuple[str, ...]
    modifiers: tuple[str, ...] = ()

    @property
    def is_compound(self) -> bool:
        return "-" in self.raw

    def __str__(self) -> str:
        return self.raw


@dataclass(frozen=True)
class FeatureControl:
    """一个形位公差框格。

    字段形状是按**真实图纸**定的（`research/nist_gdt_labels.csv` 300 条），
    不是按合成语料的简化格式定的：真实框格把特征名与公差带写在同一格
    （`Position ⌀.015|C|A|B`），基准是**有序链**而不是单个字母。
    解不出来的部分一律留在 `raw` 里，绝不猜。
    """

    symbol: str
    tolerance: float | None
    zone_is_diametral: bool = False
    zone_is_spherical: bool = False
    zone_modifiers: tuple[str, ...] = ()
    datums: tuple[DatumReference, ...] = ()
    attach: tuple[str, ...] = ()
    quantity: int = 1
    raw: str = ""


def _num(text: str) -> float:
    return float(text.replace(",", "."))


def _number(text: str) -> float | None:
    """安全版：读不出数值就返回 None。

    旧 `_num` 直接 float()，遇到真实框格里的基准字母就抛 ValueError —— 而这条路径
    在生产上由 eval/corpus.py 调用，一条读不懂的注记不该让整批语料跑挂。
    """
    try:
        return _num(text)
    except ValueError:
        return None


#: 圈号修饰符（Ⓜ 最大实体、Ⓟ 投影带、Ⓕ 自由状态…）。Unicode Enclosed Alphanumerics 段。
#: 我们**没有这些符号的可靠释义**，只保留原字符，不翻译。
_CIRCLED = r"[\u24b6-\u24e9]"
_COUNT = re.compile(r"^(\d+)\s*[X×]\s*")
_ZONE_START = re.compile(r"(S?⌀\s*)?[.\d\u24b6-\u24e9]")


def _parse_zone(text: str):
    """拆公差带格 → (数值, 是否直径, 是否球, 修饰符)。拆不出数值就返回 None。"""
    body = text.strip()
    match = re.search(_CIRCLED, body)
    head, tail = (body[:match.start()], body[match.start():]) if match else (body, "")
    modifiers = tuple(re.findall(_CIRCLED, tail))
    head = head.strip()
    spherical = diametral = False
    if head.startswith("S⌀"):
        spherical = diametral = True
        head = head[2:]
    elif head.startswith("⌀"):
        diametral = True
        head = head[1:]
    return _number(head.strip()), diametral, spherical, modifiers


def _split_datum(cell: str) -> DatumReference:
    body = cell.strip()
    return DatumReference(raw=body, letters=tuple(re.findall(r"[A-Z]", body)),
                          modifiers=tuple(re.findall(_CIRCLED, body)))


def _parse_frame(line: str) -> FeatureControl:
    raw = line.strip()
    quantity = 1
    count = _COUNT.match(raw)
    if count:
        quantity = int(count.group(1))
        raw = raw[count.end():]

    cells = [c.strip() for c in raw.split("|")]
    head = cells[0]
    # 两种真实格式：合成语料把符号与公差带分成两格（`cylindricity|0,05`），
    # 真图纸写在同一格（`Position ⌀.015|C|A|B`）。用第 2 格像不像公差带来分辨。
    if len(cells) > 1 and _parse_zone(cells[1])[0] is not None:
        symbol, zone, rest = head, cells[1], cells[2:]
    else:
        start = _ZONE_START.search(head)
        if start is None:
            return FeatureControl(symbol=head, tolerance=None, quantity=quantity,
                                  raw=line.strip())
        symbol, zone, rest = head[:start.start()].strip(), head[start.start():], cells[1:]

    value, diametral, spherical, modifiers = _parse_zone(zone)
    return FeatureControl(symbol=symbol, tolerance=value, zone_is_diametral=diametral,
                          zone_is_spherical=spherical, zone_modifiers=modifiers,
                          datums=tuple(_split_datum(c) for c in rest if c),
                          quantity=quantity, raw=line.strip())


def parse_fcf(text: str) -> tuple[FeatureControl, ...]:
    """形位公差框格：一行一个框格，行内用 | 分隔格。

    真实图纸里框格旁边常挂着附加说明（`All Around`、`APPLIES TO ALL…`）。
    它不是新格：没有竖线、没有数量前缀、又读不出公差值 —— 三个条件同时成立
    才算上一格的附加说明，否则一律另起一格。
    """
    frames: list[FeatureControl] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        parsed = _parse_frame(line)
        is_attach = (frames and "|" not in line and _COUNT.match(line) is None
                     and parsed.tolerance is None)
        if is_attach:
            last = frames[-1]
            frames[-1] = replace(last, attach=last.attach + (line.strip(),))
        else:
            frames.append(parsed)
    return tuple(frames)


def parse_notation(text: str) -> tuple[Token, ...]:
    tokens: list[Token] = []
    junk: list[str] = []
    index = 0

    def flush_junk() -> None:
        fragment = "".join(junk).strip()
        junk.clear()
        if fragment:
            tokens.append(Token("unknown", fragment))

    while index < len(text):
        if text[index].isspace():
            index += 1
            continue
        for kind, pattern in PATTERNS:
            match = pattern.match(text, index)
            if not match:
                continue
            flush_junk()
            if kind != "sep":
                captured = match.groupdict().get(kind)
                tokens.append(Token(kind, _num(captured) if captured else True))
            index = match.end()
            break
        else:
            junk.append(text[index])
            index += 1

    flush_junk()
    return tuple(tokens)

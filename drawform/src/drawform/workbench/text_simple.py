"""Complete, finite plate text -> existing schema2. No inferred dimensions.

Supports an explicit rectangular plate in millimetres, with either no holes or
numbered XY through holes relative to the plate centre. Reordered declarations
are canonicalized by their continuous hole number, never by geometry sorting.
Missing fields, implicit patterns, expressions and unconsumed text are errors.
"""
from __future__ import annotations

import re
import unicodedata

from .plate_spec import SpecError
from .simple_spec import validate_spec


NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)"
UNIT = r"(?:毫米|(?<![a-z])mm\b)"
HOLE_MARKER = re.compile(r"(?:孔\s*|\bhole\s*)(\d+)\s*[:：]?", re.I)
CHINESE_COUNTS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
                  "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
                  "十一": 11, "十二": 12, "十三": 13, "十四": 14, "十五": 15, "十六": 16}
ENGLISH_COUNTS = {name: index for index, name in enumerate(
    ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
     "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen"))}
COUNT_TOKEN = "|".join(sorted((*CHINESE_COUNTS, *ENGLISH_COUNTS), key=len, reverse=True)) + r"|[-+]?\d+"
COUNT_DECLARATION = re.compile(
    rf"(?P<count>{COUNT_TOKEN})\s*(?:(?:个)?\s*(?:z\s*(?:向|轴|方向)?\s*)?(?:通孔|贯穿孔)|(?:z[-\s]*(?:axis)?\s*)?through\s+holes?)", re.I)
ORIGIN = (r"以板中心为原点|板中心为原点|孔坐标相对板中心|坐标原点(?:为|是)板中心|"
          r"\borigin\s+(?:at|is)\s+(?:the\s+)?plate\s+cent(?:er|re)\b|"
          r"\brelative\s+to\s+(?:the\s+)?plate\s+cent(?:er|re)\b")
DIMENSIONS = {
    "length_mm": rf"(?:长度|长|\blength|\bl(?=\s*[:=]))\s*(?:为|=|:)?\s*({NUMBER})",
    "width_mm": rf"(?:宽度|宽|\bwidth|\bw(?=\s*[:=]))\s*(?:为|=|:)?\s*({NUMBER})",
    "thickness_mm": rf"(?:厚度|厚|\bthickness|\bt(?=\s*[:=]))\s*(?:为|=|:)?\s*({NUMBER})",
}
HOLE_FIELDS = {
    "x_mm": rf"(?<![a-z])x\s*(?:=|:)?\s*({NUMBER})",
    "y_mm": rf"(?<![a-z])y\s*(?:=|:)?\s*({NUMBER})",
    "diameter_mm": rf"(?:直径|孔径|\bdiameter|ø)\s*(?:为|=|:)?\s*({NUMBER})",
}


def _blank(rest, start, end):
    rest[start:end] = " " * (end - start)


def _consume(rest, text, pattern):
    matches = list(re.finditer(pattern, text, re.I))
    for match in matches:
        _blank(rest, match.start(), match.end())
    return matches


def _fields(text, patterns, rest):
    values = {}
    for key, pattern in patterns.items():
        matches = list(re.finditer(pattern, text, re.I))
        if len(matches) > 1:
            raise SpecError(f'{key}重复出现，请保留一个明确值。', 'SOURCE_FACT_CONFLICT')
        if not matches:
            raise SpecError(f'还缺少明确的{key}，本批完整文字不能省略尺寸。', 'MISSING_CRITICAL_DIMENSION')
        match = matches[0]
        values[key] = float(match.group(1))
        _blank(rest, match.start(), match.end())
    return values


def _no_residual(rest):
    residual = re.sub(r"[\s,，。.;；:：=()（）\[\]{}]", "", "".join(rest))
    if residual:
        raise SpecError('描述仍有未解释的要求或数字；不支持表达式、均布、对称或距边推断。', 'UNCONSUMED_REQUIREMENT')


def parse_simple_plate(text):
    if not isinstance(text, str) or not text.strip() or len(text) > 2000:
        raise SpecError('请填写最多2000字的完整矩形板描述。')
    t = unicodedata.normalize('NFKC', text).lower().replace('⌀', 'ø')
    if re.search(r"厘米|英寸|公分|分米|微米|(?<!毫)米|(?<![a-z])(?:cm|inch(?:es)?|feet|ft|meters?)\b", t, re.I):
        raise SpecError('本段文字板件仅支持毫米，请明确使用毫米/mm。', 'UNSUPPORTED_UNIT')
    if not re.search(UNIT, t, re.I):
        raise SpecError('请在完整文字中明确毫米/mm单位。', 'MISSING_CRITICAL_DIMENSION')
    markers = list(HOLE_MARKER.finditer(t))
    prefix = t[:markers[0].start()] if markers else t
    remaining = list(prefix)
    kind = _consume(remaining, prefix, r"矩形板|长方形板|\brectangular\s+plate\b")
    if len(kind) != 1:
        raise SpecError('请明确一块矩形板，不接受其他或重复零件类型。', 'UNSUPPORTED_FEATURE')
    parameters = _fields(prefix, DIMENSIONS, remaining)
    no_holes = _consume(remaining, prefix, r"无孔|没有孔|\bno\s+holes?\b")
    count_matches = _consume(remaining, prefix, COUNT_DECLARATION.pattern)
    if len(no_holes) + len(count_matches) != 1:
        raise SpecError('请明确无孔，或声明唯一的通孔数量；不能忽略或覆盖重复声明。', 'SOURCE_FACT_CONFLICT')
    if no_holes:
        count = 0
    else:
        token = count_matches[0].group('count').lower()
        count = CHINESE_COUNTS.get(token, ENGLISH_COUNTS.get(token))
        if count is None:
            count = int(token)
    if not 0 <= count <= 16:
        raise SpecError('本段最多支持16个明确通孔。', 'UNSUPPORTED_FEATURE')
    origins = _consume(remaining, prefix, ORIGIN)
    if len(origins) > 1:
        raise SpecError('孔坐标原点重复声明。', 'SOURCE_FACT_CONFLICT')
    if count and not origins:
        raise SpecError('请明确孔的XY坐标以板中心为原点。', 'MISSING_CRITICAL_DIMENSION')
    _consume(remaining, prefix, UNIT)
    _consume(remaining, prefix, r"^\s*(?:(?:请|帮我)\s*)?(?:生成|制作|做)?\s*(?:一块)?")
    _consume(remaining, prefix, r"^\s*(?:please\s+)?(?:make|create)\s+(?:a\s+)?")
    _no_residual(remaining)
    if len(markers) != count:
        raise SpecError('通孔数量与编号孔描述不一致。', 'SOURCE_FACT_CONFLICT')
    numbered = {}
    for index, marker in enumerate(markers):
        identity = int(marker.group(1))
        if identity in numbered:
            raise SpecError('孔编号重复，不能选择其中一个孔描述。', 'SOURCE_FACT_CONFLICT')
        end = markers[index+1].start() if index+1 < len(markers) else len(t)
        body = t[marker.end():end]
        body_remaining = list(body)
        numbered[identity] = _fields(body, HOLE_FIELDS, body_remaining)
        _consume(body_remaining, body, UNIT)
        _no_residual(body_remaining)
    if set(numbered) != set(range(1, count+1)):
        raise SpecError('孔编号须从1连续至声明的孔数，不能缺号或跳号。', 'SOURCE_FACT_CONFLICT')
    return validate_spec({'schema_version': 2, 'kind': 'plate', 'parameters': parameters,
                          'holes': [numbered[i] for i in range(1, count+1)]})

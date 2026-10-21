"""有限中文/英文板件语法。不把猜测或未消费的数字当作制造尺寸。"""
from __future__ import annotations
import math
import re
import unicodedata


class SpecError(ValueError):
    def __init__(self, message, code='INVALID_PLATE_SPEC'):
        super().__init__(message)
        self.code = code


def parse_plate(text, units='mm'):
    if not isinstance(text, str) or not text.strip() or len(text) > 2000:
        raise SpecError('请填写最多2000字的带孔板描述。')
    t = unicodedata.normalize('NFKC', text).lower()
    if units != 'mm' or re.search(r'厘米|英寸|\bcm\b|\binch(?:es)?\b', t):
        raise SpecError('本段仅支持毫米，请将尺寸换算成毫米后输入。', 'UNSUPPORTED_UNIT')
    if re.search(r'盲孔|沉孔|倒角|圆角|螺纹|台阶|口袋|偏移|距边|两个|两孔|多个|多孔|[2-9]\s*个\s*孔', t):
        raise SpecError('首段仅支持一块矩形板和一个中心通孔；请去除其他特征后重试。', 'UNSUPPORTED_FEATURE')
    if not re.search(r'板|plate', t) or not re.search(r'中心|中间|中央|\bcent(?:er|re)\b', t) or not re.search(r'通孔|贯穿|through', t):
        raise SpecError('请明确说明矩形板、中心孔位和通孔；例如：长100、宽60、厚8毫米，中心通孔直径20毫米。', 'MISSING_CRITICAL_DIMENSION')
    number = r'(\d+(?:\.\d+)?)'
    patterns = {'length_mm': r'(?:长度|长|length)\s*(?:为|=|:)?\s*'+number,
                'width_mm': r'(?:宽度|宽|width)\s*(?:为|=|:)?\s*'+number,
                'thickness_mm': r'(?:厚度|厚|thickness)\s*(?:为|=|:)?\s*'+number,
                'hole_diameter_mm': r'(?:直径|孔径|diameter|[øØ⌀])\s*(?:为|=|:)?\s*'+number}
    values = {}; consumed = []; labels = []
    for key, pattern in patterns.items():
        matches = list(re.finditer(pattern, t))
        if len(matches) > 1:
            raise SpecError('同一个尺寸出现多次，请保留一组明确数值。', 'SOURCE_FACT_CONFLICT')
        if matches:
            values[key] = float(matches[0].group(1)); consumed.append(matches[0].span(1)); labels.append(matches[0].span())
    dims = re.search(number+r'\s*[x×*]\s*'+number+r'\s*[x×*]\s*'+number, t)
    if dims:
        for index, key in enumerate(('length_mm', 'width_mm', 'thickness_mm'), 1):
            value = float(dims.group(index))
            if key in values and values[key] != value:
                raise SpecError('长宽厚有冲突，请明确最终尺寸。', 'SOURCE_FACT_CONFLICT')
            values[key] = value; consumed.append(dims.span(index))
        labels.append(dims.span())
    if set(values) != set(patterns):
        names = {'length_mm':'长度', 'width_mm':'宽度', 'thickness_mm':'厚度', 'hole_diameter_mm':'通孔直径'}
        missing = '、'.join(names[k] for k in patterns if k not in values)
        raise SpecError(f'还缺少{missing}，请补充后重新生成。', 'MISSING_CRITICAL_DIMENSION')
    for count in re.finditer(r'(?<!\d)(1)\s*个', t):consumed.append(count.span(1))
    for match in re.finditer(r'-?\d+(?:\.\d+)?', t):
        if match.span() not in consumed and not re.search(r'^(?:一个|1个)', t[max(0, match.start()-1):match.end()+1]):
            raise SpecError('还有未识别的数值或特征，请只保留长、宽、厚和一个中心通孔直径。', 'UNCONSUMED_REQUIREMENT')
    residual=list(t)
    for start,end in labels:
        residual[start:end]=' '*(end-start)
    rest=''.join(residual)
    rest=re.sub(r'毫米|\bmm\b|中心|中间|中央|长方形|矩形|帮我|请|做|生成|一|1个|个|块|的|板|开|在|有|带|打|通孔|贯穿', '', rest)
    rest=re.sub(r'\b(?:plate|with|center|centre|through|hole|make|a)\b','',rest)
    if re.search(r'[a-z0-9\u4e00-\u9fff]',rest):
        raise SpecError('描述中还有本段不能识别的要求，请仅保留矩形板和一个中心通孔。','UNCONSUMED_REQUIREMENT')
    if any(not math.isfinite(v) or not 0 < v <= 1000 for v in values.values()):
        raise SpecError('各尺寸须大于0且不超过1000毫米。')
    if values['hole_diameter_mm'] >= min(values['length_mm'], values['width_mm']):
        raise SpecError('通孔直径必须小于板的长度和宽度。')
    return values

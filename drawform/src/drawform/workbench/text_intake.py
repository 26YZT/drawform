"""Strict local partial text facts; refresh_questions mutates and returns its input.

Only explicit roles become dimensions. Remaining words/numbers, conflicts and
impossible partial geometry raise SpecError instead of becoming questions.
Facts always point into the saved NFKC normalized_text, never guessed raw offsets.
"""
from __future__ import annotations
import hashlib
import math
import re
import unicodedata

from .plate_spec import SpecError
from .simple_spec import finite, number, validate_spec
from .text_simple import NUMBER, UNIT, HOLE_MARKER, CHINESE_COUNTS, ENGLISH_COUNTS, COUNT_TOKEN, ORIGIN


UNSUPPORTED = re.compile(r'盲孔|沉孔|沉头|螺纹|凸台|凸起|圆角|倒角|加强筋|肋板|台阶|阶梯|口袋|凹槽|斜孔|距边|对称|均布|blind|counterbore|countersink|thread|tapped|boss|fillet|chamfer|pocket|slot|\bribs?\b|symmetric|equally\s+spaced', re.I)
NON_MM = re.compile(r'厘米|英寸|公分|分米|微米|(?<!毫)米|(?<![a-z])(?:cm|inch(?:es)?|feet|ft|meters?)\b', re.I)
PLATE_FIELDS = {'length_mm': r'(?:长度|长|\blength|\bl(?=\s*[:=]))',
                'width_mm': r'(?:宽度|宽|\bwidth|\bw(?=\s*[:=]))',
                'thickness_mm': r'(?:厚度|厚|\bthickness|\bt(?=\s*[:=]))'}
ROUND_FIELDS = {'outer_diameter_mm': r'(?:外直径|外径|\bouter\s+diameter)',
                'inner_diameter_mm': r'(?:内直径|内径|\binner\s+diameter)',
                'thickness_mm': PLATE_FIELDS['thickness_mm']}
HOLE_FIELDS = {'x_mm': r'(?<![a-z])x', 'y_mm': r'(?<![a-z])y',
               'diameter_mm': r'(?:直径|孔径|\bdiameter|[øØ⌀])'}
COUNT = rf'(?P<count>{COUNT_TOKEN})\s*(?:(?:个)?\s*(?:z\s*(?:向|轴|方向)?\s*)?(?:通孔|贯穿孔|孔)|(?:through\s+)?holes?)'
LABELS = {'length_mm': '长度', 'width_mm': '宽度', 'thickness_mm': '厚度',
          'outer_diameter_mm': '外径', 'inner_diameter_mm': '内径'}


def refresh_questions(analysis):
    """Recompute questions in place; preserve facts and all existing numeric values."""
    if isinstance(analysis,dict) and analysis.get('kind')=='stepped_shaft':
        from .shaft_input import refresh_shaft_questions
        return refresh_shaft_questions(analysis)
    if not isinstance(analysis, dict) or analysis.get('kind') not in ('plate', 'washer'):
        raise SpecError('文字零件类型不受支持。', 'UNSUPPORTED_FEATURE')
    p = analysis.get('parameters'); holes = analysis.get('holes')
    if not isinstance(p, dict) or not isinstance(holes, list):raise SpecError('文字分析参数格式错误。')
    units = analysis.get('units')
    if units not in (None, 'mm'):raise SpecError('文字澄清只接受毫米。', 'UNSUPPORTED_UNIT')
    keys = tuple(PLATE_FIELDS) if analysis['kind'] == 'plate' else tuple(ROUND_FIELDS)
    if set(p) != set(keys):raise SpecError('文字分析包含未知或缺失的参数字段。')
    for key, value in p.items():
        if value is not None:number(value, key, zero=key == 'inner_diameter_mm')
    mode = analysis.get('hole_mode')
    through = bool(analysis.get('through_confirmed'))
    origin = bool(analysis.get('coordinate_origin_confirmed'))
    requires_origin = bool(analysis.get('requires_explicit_origin'))
    if analysis['kind'] == 'plate':
        if mode not in (None, 'no_holes', 'center', 'center_through', 'explicit_xy'):raise SpecError('未知孔配置。')
        if mode == 'no_holes' and holes or mode is None and holes:raise SpecError('孔配置与已有孔事实冲突。', 'SOURCE_FACT_CONFLICT')
        if mode in ('center', 'center_through'):
            if not holes:holes = [{'x_mm': 0., 'y_mm': 0., 'diameter_mm': None}]
            if len(holes) != 1 or holes[0].get('x_mm') != 0 or holes[0].get('y_mm') != 0:
                raise SpecError('中心孔与已有坐标冲突。', 'SOURCE_FACT_CONFLICT')
            origin = True; requires_origin = False
            if mode == 'center_through':through = True
        if len(holes) > 16:raise SpecError('本批最多16孔。', 'UNSUPPORTED_FEATURE')
        for hole in holes:
            if not isinstance(hole, dict) or set(hole) != set(HOLE_FIELDS):raise SpecError('孔参数格式错误。')
            d = hole['diameter_mm']
            if d is not None:number(d, '孔径')
            for axis, extent in (('x_mm', 'length_mm'), ('y_mm', 'width_mm')):
                value = hole[axis]
                if value is not None:finite(value, '孔坐标')
                limit = p[extent] if p[extent] is not None else 1000.
                if abs(value or 0) + (d or 0)/2 >= limit/2 - 1e-6:
                    raise SpecError('已有孔坐标/孔径无法完整位于板内，不能用补问掩盖。')
        for i, a in enumerate(holes):
            for b in holes[i+1:]:
                if all(h[k] is not None for h in (a, b) for k in ('x_mm', 'y_mm')):
                    if math.hypot(a['x_mm']-b['x_mm'], a['y_mm']-b['y_mm']) <= ((a['diameter_mm'] or 0)+(b['diameter_mm'] or 0))/2 + 1e-6:
                        raise SpecError('已有孔几何相交/相切，不能继续补问。')
    else:
        if holes:raise SpecError('垫圈/圆盘不能带额外孔。')
        if analysis.get('part_type') not in ('washer', 'solid_disc'):raise SpecError('圆形零件类型不明确。')
        inner, outer = p['inner_diameter_mm'], p['outer_diameter_mm']
        if analysis['part_type'] == 'washer' and inner is not None and inner <= 0:
            raise SpecError('垫圈内径必须大于0，不能默认为实心圆盘。')
        if analysis['part_type'] == 'solid_disc' and inner != 0:
            raise SpecError('实心圆盘与非零/未知内径冲突。', 'SOURCE_FACT_CONFLICT')
        if inner is not None and inner >= (outer if outer is not None else 1000):raise SpecError('内径必须小于外径。')
        requires_origin = False; origin = True
    questions = []
    def ask(parameter, label, reason, choices=None):
        q = {'id': parameter, 'parameter': parameter, 'label': label, 'reason': reason,
             'type': 'choice' if choices is not None else 'number', 'unit': 'mm'}
        if choices is not None:q['choices'] = [{'value': value, 'label': title} for value, title in choices]
        questions.append(q)
    if units is None:ask('units', '请确认文字尺寸单位', '原文没有明确单位；本批仅支持毫米。', [('mm', '确认毫米')])
    for key in keys:
        if p[key] is None:ask(key, LABELS[key]+'是多少毫米？', '原文没有明确该尺寸，不自动填写。')
    if analysis['kind'] == 'plate':
        if mode is None:ask('hole_mode', '这块板需要什么孔配置？', '原文没有明确无孔或孔配置。', [('no_holes', '无孔'), ('center_through', '一个中心通孔')])
        if holes and requires_origin and not origin:ask('hole_origin', '孔坐标是否以板中心为原点？', '原文未明确坐标原点。', [('plate_center', '确认板中心为原点')])
        if holes and not through:ask('through', '孔是否沿厚度贯穿？', '原文未声明贯通性质。', [('through', '确认全部贯穿')])
        for i, hole in enumerate(holes):
            for key, title in (('x_mm', 'X坐标'), ('y_mm', 'Y坐标'), ('diameter_mm', '直径')):
                if hole[key] is None:ask(f'holes.{i}.{key}', f'孔{i+1}{title}是多少毫米？', '该孔角色没有明确数值。')
    complete = mode is not None if analysis['kind'] == 'plate' else True
    if complete and all(v is not None for v in p.values()) and all(v is not None for h in holes for v in h.values()):
        validate_spec({'schema_version': 2, 'kind': analysis['kind'], 'parameters': p, 'holes': holes})
    analysis.update(holes=holes, questions=questions, through_confirmed=through,
                    requires_explicit_origin=requires_origin, coordinate_origin_confirmed=origin)
    return analysis


def analyze_text(text):
    if not isinstance(text, str) or not text.strip() or len(text) > 2000:raise SpecError('请填写最多2000字的文字。')
    from .shaft_input import has_shaft_declaration, analyze_shaft_text
    if has_shaft_declaration(text):return analyze_shaft_text(text)
    t = unicodedata.normalize('NFKC', text)
    if NON_MM.search(t):raise SpecError('明确的非毫米单位不受支持。', 'UNSUPPORTED_UNIT')
    if UNSUPPORTED.search(t):raise SpecError('文字包含本批不能建模的明确要求。', 'UNSUPPORTED_FEATURE')
    sha = hashlib.sha256(text.encode()).hexdigest(); norm_sha = hashlib.sha256(t.encode()).hexdigest()
    analysis = {'schema_version': 2, 'input_kind': 'text', 'source_sha256': sha,
                'normalized_text': t, 'normalized_text_sha256': norm_sha, 'normalization': 'NFKC',
                'kind': None, 'part_type': None, 'parameters': {}, 'holes': [], 'units': None,
                'facts': [], 'questions': [], 'notes': [], 'hole_mode': None,
                'through_confirmed': False, 'requires_explicit_origin': False, 'coordinate_origin_confirmed': False}
    rest = list(t)
    def matches(pattern, lo=0, hi=None):
        hi = len(t) if hi is None else hi
        found = []
        for match in re.finditer(pattern, ''.join(rest)[lo:hi], re.I):
            start, end = lo+match.start(), lo+match.end()
            rest[start:end] = ' '*(end-start)
            found.append((match, start, end))
        return found
    def fact(parameter, value, start, end, unit=None):
        analysis['facts'].append({'id': f"fact_{len(analysis['facts'])+1}", 'parameter': parameter,
            'value': value, 'unit': unit or ('mm' if analysis['units'] else 'source_unit'),
            'evidence': {'type': 'text_span', 'span_basis': 'normalized_text', 'span': [start, end],
                         'text': t[start:end], 'source_sha256': sha, 'normalized_text_sha256': norm_sha}})
    def fields(patterns, lo=0, hi=None, path=''):
        values = {}
        for key, label in patterns.items():
            found = matches(rf'{label}\s*(?:为|=|:)?\s*({NUMBER})?', lo, hi)
            if len(found) > 1:raise SpecError('同一数值角色重复/冲突，不能选择一个。', 'SOURCE_FACT_CONFLICT')
            values[key] = None
            if found:
                match, start, end = found[0]
                value = float(match.group(1)) if match.group(1) is not None else None
                values[key] = value; fact(path+key, value, start, end)
        return values
    origin_spans = matches(ORIGIN)
    if len(origin_spans) > 1:raise SpecError('坐标原点重复声明。', 'SOURCE_FACT_CONFLICT')
    if origin_spans:
        _, start, end = origin_spans[0]; fact('hole_origin', 'plate_center', start, end, 'intent')
        analysis['coordinate_origin_confirmed'] = True
    types = matches(r'实心圆盘|\bsolid\s+(?:disc|disk)\b|垫圈|\bwasher\b|矩形板|长方形板|\brectangular\s+plate\b|板|\bplate\b')
    if len(types) != 1:raise SpecError('请明确一个支持的零件类型。', 'UNSUPPORTED_FEATURE')
    match, start, end = types[0]; name = t[start:end]
    part_type = 'solid_disc' if re.search(r'实心圆盘|solid', name, re.I) else 'washer' if re.search(r'垫圈|washer', name, re.I) else 'plate'
    analysis.update(kind='plate' if part_type == 'plate' else 'washer', part_type=part_type)
    fact('part_type', part_type, start, end, 'intent')
    unit_roles = matches(r'(?:尺寸单位|单位|\bunits?\b)\s*(?:为|是|=|:)?\s*(毫米|mm)?')
    if len(unit_roles) > 1:raise SpecError('单位角色重复声明。', 'SOURCE_FACT_CONFLICT')
    for match, start, end in unit_roles:
        if match.group(1) is not None:analysis['units'] = 'mm'
        fact('units', 'mm' if match.group(1) is not None else None, start, end, 'unit')
    for _, start, end in matches(UNIT):
        analysis['units'] = 'mm'; fact('units', 'mm', start, end, 'unit')
    if part_type != 'plate':
        analysis['parameters'] = fields(ROUND_FIELDS)
        if part_type == 'solid_disc' and analysis['parameters']['inner_diameter_mm'] is None:
            analysis['parameters']['inner_diameter_mm'] = 0.; fact('inner_diameter_mm', 0., types[0][1], types[0][2])
        if part_type == 'washer':
            analysis['through_confirmed'] = True
            fact('through', 'through', types[0][1], types[0][2], 'intent')
            analysis['notes'].append('贯穿性质来自明确的垫圈圆环类型；不支持盲孔、沉孔或螺纹附加要求。')
        analysis['hole_mode'] = 'no_holes'
    else:
        markers = list(HOLE_MARKER.finditer(t)); boundary = markers[0].start() if markers else len(t)
        analysis['parameters'] = fields(PLATE_FIELDS, hi=boundary)
        triples = matches(rf'({NUMBER})\s*[x×]\s*({NUMBER})\s*[x×]\s*({NUMBER})', hi=boundary)
        if triples:
            if len(triples) != 1 or any(f['parameter'] in PLATE_FIELDS for f in analysis['facts']):
                raise SpecError('长宽厚角色或完整三值元组重复，不能选择一个。', 'SOURCE_FACT_CONFLICT')
            match, start, _ = triples[0]
            for index, key in enumerate(PLATE_FIELDS, 1):
                analysis['parameters'][key] = float(match.group(index))
                fact(key, float(match.group(index)), start+match.start(index)-match.start(), start+match.end(index)-match.start())
            analysis['notes'].append('完整三值元组采用既有长×宽×厚顺序；不足三值不推测尺寸角色。')
        none = matches(r'无孔|没有孔|\bno\s+holes?\b', hi=boundary)
        counts = matches(COUNT, hi=boundary)
        if len(none)+len(counts) > 1:raise SpecError('孔配置/数量重复或冲突。', 'SOURCE_FACT_CONFLICT')
        count = None
        if counts:
            token = counts[0][0].group('count').lower()
            count = CHINESE_COUNTS.get(token, ENGLISH_COUNTS.get(token))
            if count is None:count = int(token)
            if not 0 <= count <= 16:raise SpecError('本批孔数量只接受0至16。', 'UNSUPPORTED_FEATURE')
            fact('hole_count', count, counts[0][1], counts[0][2], 'count')
        through_spans = list(re.finditer(r'通孔|贯穿|\bthrough\b', t, re.I))
        analysis['through_confirmed'] = bool(through_spans)
        for m in through_spans:fact('through', 'through', m.start(), m.end(), 'intent')
        if markers:
            if none or count is None or count != len(markers):raise SpecError('孔编号描述与声明孔数不一致。', 'SOURCE_FACT_CONFLICT')
            numbered = {}
            for i, marker in enumerate(markers):
                identity = int(marker.group(1))
                if identity in numbered:raise SpecError('孔编号重复。', 'SOURCE_FACT_CONFLICT')
                rest[marker.start():marker.end()] = ' '*(marker.end()-marker.start())
                end = markers[i+1].start() if i+1 < len(markers) else len(t)
                numbered[identity] = fields(HOLE_FIELDS, marker.end(), end, f'holes.{identity-1}.')
            if set(numbered) != set(range(1, count+1)):raise SpecError('孔编号必须连续。', 'SOURCE_FACT_CONFLICT')
            analysis.update(holes=[numbered[i] for i in range(1, count+1)], hole_mode='explicit_xy', requires_explicit_origin=True)
        elif none or count == 0:
            analysis['hole_mode'] = 'no_holes'
        else:
            centers = matches(r'中心|中间|中央|\bcent(?:er|re)\b')
            holes = matches(r'通孔|贯穿孔|孔(?!径)|\b(?:through\s+)?holes?\b')
            if centers or holes or count:
                if none or count not in (None, 1) or not holes and not counts:
                    raise SpecError('未编号的孔配置不明确，不能推测多个孔。', 'SOURCE_FACT_CONFLICT')
                if len(centers) > 1:raise SpecError('中心位置重复声明。', 'SOURCE_FACT_CONFLICT')
                hole = fields({'diameter_mm': HOLE_FIELDS['diameter_mm']}, path='holes.0.')
                hole.update(x_mm=0. if centers else None, y_mm=0. if centers else None)
                analysis.update(holes=[hole], hole_mode=('center_through' if analysis['through_confirmed'] else 'center') if centers else 'explicit_xy', requires_explicit_origin=not bool(centers))
                if centers:
                    _, start, end = centers[0]
                    fact('holes.0.x_mm', 0., start, end); fact('holes.0.y_mm', 0., start, end)
                for _, start, end in matches(r'一个|1\s*个|\bone\b|\bsingle\b'):fact('hole_count', 1, start, end, 'count')
    matches(r'帮我|请|生成|制作|做|一块|的|开|在|有|带|打|\b(?:please|make|create|a|with)\b')
    residue = re.sub(r'[\s,，。.;；:：=、?？()（）\[\]{}]', '', ''.join(rest))
    if residue:raise SpecError('仍有未解释的文字或裸数字，不能转换为补问。', 'UNCONSUMED_REQUIREMENT')
    return refresh_questions(analysis)

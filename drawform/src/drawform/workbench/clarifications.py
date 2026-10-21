"""Source-bound answers shared by drawing and finite text intake."""
from __future__ import annotations
import hashlib
import json
import re
from .plate_spec import SpecError
from .simple_spec import finite, number, spec_from_analysis


def basis(analysis, revision):
    # Keep the existing drawing basis serialization stable across upgrades.
    return hashlib.sha256(json.dumps({'analysis': analysis, 'revision': revision},
        sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


class Clarifications:
    def __init__(self, service):
        self.s = service

    def answer(self, sid, owner, body, key):
        from .modeling import Conflict
        s = self.s
        if not isinstance(key, str) or not 1 <= len(key) <= 128:
            raise SpecError('请提供有效的澄清请求编号。')
        if (not isinstance(body, dict) or set(body) != {'basis_sha256', 'revision', 'answers'}
                or not isinstance(body.get('answers'), dict) or not body['answers']):
            raise SpecError('请回答至少一个关键问题，并绑定当前输入依据。')
        try:
            payload = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False,
                                                allow_nan=False).encode()).hexdigest()
        except (TypeError, ValueError, RecursionError):
            raise SpecError('回答必须为有限的JSON数值或允许选项。')
        with s.lock, s.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            data = s.raw(sid, owner)
            s.verify_input_source(data, owner)
            receipt = db.execute('SELECT payload_sha FROM clarification_receipts WHERE owner=? AND session=? AND key=?',
                                 (owner, sid, key)).fetchone()
            if receipt:
                if receipt['payload_sha'] != payload:
                    raise Conflict('澄清请求编号与原内容不一致。')
                return s.get(sid, owner)
            if (data['state'] != 'needs_clarification' or body['basis_sha256'] != data.get('basis_sha256')
                    or type(body['revision']) is not int or body['revision'] != data.get('clarification_revision')):
                raise Conflict('问题依据已改变，请刷新后回答。')
            analysis = json.loads(json.dumps(data['analysis']))
            questions = {q['id']: q for q in analysis['questions']}
            if set(body['answers']) - set(questions):
                raise SpecError('回答包含未知问题，不能覆盖已确认的输入事实。')
            values = []
            for qid, value in body['answers'].items():
                q = questions[qid]
                parameter = q['parameter']
                if q.get('type') == 'blocked' or q.get('readonly'):
                    raise SpecError('输入存在明确冲突，请更正原始输入；不能覆盖原文或原图事实。', 'SOURCE_FACT_CONFLICT')
                coordinate = re.fullmatch(r'holes\.(\d+)\.(x_mm|y_mm|diameter_mm)', parameter)
                if q['type'] == 'number':
                    value = finite(value, q['label']) if coordinate and coordinate[2] != 'diameter_mm' else number(value, q['label'])
                    if coordinate and abs(value) > 1000:
                        raise SpecError('孔位置须在-1000至1000毫米范围内。')
                elif q['type'] == 'choice':
                    if value not in [c['value'] for c in q.get('choices', [])]:
                        raise SpecError('请选择当前问题允许的选项。')
                else:
                    raise SpecError('问题类型不受支持。')
                if parameter == 'units':
                    analysis['units'] = value
                elif parameter == 'through':
                    analysis['through_confirmed'] = value == 'through'
                    analysis['through_basis'] = 'user_answer'
                elif parameter == 'shaft_solid':
                    if value != 'solid' or analysis.get('kind') != 'stepped_shaft':
                        raise SpecError('仅能确认当前二段轴为同轴实心、无孔。')
                    analysis['shaft_solid_confirmed'] = True
                elif parameter == 'hole_position':
                    if value != 'center' or len(analysis['holes']) != 1:
                        raise SpecError('本批只能明确确认单孔中心位置。')
                    analysis['holes'][0].update(x_mm=0.0, y_mm=0.0)
                elif parameter == 'hole_diameter_mm':
                    if len(analysis['holes']) != 1:
                        raise SpecError('该孔径不能无依据套用于多孔。')
                    analysis['holes'][0]['diameter_mm'] = value
                elif coordinate:
                    index = int(coordinate[1])
                    if index >= len(analysis['holes']):
                        raise SpecError('孔编号不在当前问题范围内。')
                    analysis['holes'][index][coordinate[2]] = value
                elif parameter == 'hole_origin':
                    if value != 'plate_center':
                        raise SpecError('请确认坐标原点为板中心。')
                    analysis['coordinate_origin_confirmed'] = True
                elif parameter == 'hole_mode':
                    if analysis.get('holes'):
                        raise SpecError('已知孔配置不能覆盖。')
                    analysis['hole_mode'] = value
                    if value == 'center_through':
                        analysis['holes'] = [{'x_mm': 0.0, 'y_mm': 0.0, 'diameter_mm': None}]
                        analysis['through_confirmed'] = True
                        analysis['through_basis'] = 'user_answer'
                        analysis['coordinate_origin_confirmed'] = True
                    elif value == 'no_holes':
                        analysis['holes'] = []
                    else:
                        raise SpecError('孔配置不受支持。')
                elif parameter in ('length_mm', 'width_mm', 'thickness_mm', 'outer_diameter_mm', 'inner_diameter_mm',
                                   'diameter_1_mm', 'length_1_mm', 'diameter_2_mm', 'length_2_mm'):
                    analysis['parameters'][parameter] = value
                else:
                    raise SpecError('问题参数不在白名单中。')
                values.append((qid, q, value))
            confirmations = []
            for qid, q, value in values:
                fact = {'id': 'answer_' + str(data['clarification_revision']) + '_' + qid,
                        'parameter': q['parameter'], 'value': value, 'unit': analysis.get('units'),
                        'evidence': {'type': 'user_answer', 'text': q['label'], 'question_id': qid,
                                     'clarification_session_id': sid}}
                analysis.setdefault('facts', []).append(fact)
                confirmations.append(fact)
            if analysis.get('kind') == 'stepped_shaft':
                from .shaft_input import refresh_shaft_questions
                refresh_shaft_questions(analysis)
            elif data.get('input_kind') == 'text':
                from .text_intake import refresh_questions
                refresh_questions(analysis)
            else:
                analysis['questions'] = [q for q in analysis['questions'] if q['id'] not in body['answers']]
            revision = data['clarification_revision'] + 1
            data.update(analysis=analysis, questions=analysis['questions'], clarification_revision=revision,
                        basis_sha256=basis(analysis, revision), parameters=analysis['parameters'],
                        holes=analysis['holes'], clarifications=data.get('clarifications', []) + confirmations)
            next_state = 'needs_clarification'
            if not analysis['questions']:
                spec = spec_from_analysis(analysis)
                data.update(spec=spec, model_kind=spec['kind'], parameters=spec['parameters'], holes=spec['holes'], backend='local_simple_v2')
                next_state = 'queued'
            if s.auto_resume and next_state=='queued':
                from .attempts import bind_intent
                bind_intent(data,'build')
            s.write_json(s.directory(sid)/'clarifications'/(payload+'.json'),
                         {'request': body, 'confirmations': confirmations, 'source_sha256': data['source_sha256']})
            db.execute('INSERT INTO clarification_receipts VALUES(?,?,?,?)', (owner, sid, key, payload))
            db.execute('UPDATE sessions SET state=?,data=? WHERE id=?',
                       (next_state, json.dumps(data, ensure_ascii=False), sid))
            db.commit()
        if next_state == 'queued':
            s.pending.put((sid, owner))
        return s.get(sid, owner)

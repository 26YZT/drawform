"""Replay the frozen public CTC01 technical choices; never count this as human review."""
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import ezdxf
import pytest

from drawform import pipeline
from drawform.geometry.references import ReferenceRequired
from drawform.service.answers import Answer, bind_scheme
from drawform.output.drawing import DatumMark, DrawingItem, Text, SheetOverflow, datum_anchor_slide, _dimension
from drawform.output.annotation_layout import settle_annotations
from drawform.output.sheet import make_frame
from drawform.dimensioning.place import PlacedDimension
from tests.test_questions import _derived


# Existing technical replay from 2026-10-16. These are not new engineering choices.
REFERENCE = {
    'basis_sha': '2c2ce49fd8032e1743b68f044af76eb3a8d7f96cc8ea8389f1af800b52851527',
    'answers': [
        {'question_id': 'reference-bore-ref-04b1e9527c1a4bafab42d949', 'option_id': 'plane-ref-b3caa669d21de0a704818d48'},
        {'question_id': 'reference-bore-ref-58899838ae74da667a912537', 'option_id': 'plane-ref-3ad22d4d4e686566d5cab81b'},
        {'question_id': 'reference-bore-ref-b48bc88ab0a106bfed44ddab', 'option_id': 'plane-ref-c2f801ac6dc093cc0d0c8efc'},
        {'question_id': 'reference-bore-ref-cd0ca8442d169972d6d4300e', 'option_id': 'plane-ref-6738b09ddd01d1f414fae6e2'},
    ],
}
# Exact source/value/quantity digest of the sealed 2026-10-16 initial CTC01 JSON.
FROZEN_CONSTRAINTS = '9fe5e55c559d4a441686226718b2fb81731dda6bf95102d8b4b5c0ea94bca088'


def test_exact_face_endpoint_cannot_put_a_datum_leader_through_a_zero_length_dimension():
    mark = DatumMark('top', (30, 7.5), (-1, 0), 'A', standoff=23, reach=7.5)
    lanes = (((20, .5), (20, 16)), ((22, 0), (22, 2.5321526911186476e-16)))
    assert datum_anchor_slide(mark, lanes) is None


def test_exact_face_endpoint_remains_available_when_the_zero_dimension_is_outside_the_leader():
    mark = DatumMark('top', (30, 7.5), (-1, 0), 'A', standoff=23, reach=7.5)
    lanes = (((20, .5), (20, 16)), ((40, 0), (40, 2.5321526911186476e-16)))
    assert datum_anchor_slide(mark, lanes) == 7.5


def _linear_label_case(symbol):
    dim = PlacedDimension('linear', 'top', (40, 50), (100, 50), (70, 60), '60', 60,
                          source=('outline', 'length'))
    owner = f'dim:{dim.source}'
    lines, _heads, _labels = _dimension(dim, (0, 0))
    items = [DrawingItem(owner, 'DIM', line) for line in lines]
    items.append(DrawingItem('datum:A', 'DATUM', symbol))
    # Force relocation so that the actual connection to the dimension is tested.
    texts = [Text('60', 70, 80, 'middle', owner=owner),
             Text('固定文字', 70, 80, 'middle', owner='fixed')]
    return dim, owner, texts, items


def test_linear_label_leader_can_attach_to_a_clear_point_on_the_same_dimension_line():
    dim, owner, texts, items = _linear_label_case(((65, 60), (75, 60)))
    final_texts, final_items = settle_annotations(texts, items, (dim,), {'top': (0, 0)}, make_frame('A3'))
    routes = [item.points for item in final_items[len(items):] if item.owner == owner]
    assert len(routes) == 1
    anchor = routes[0][0]
    assert anchor[1] == 60 and 40 <= anchor[0] <= 100
    assert not 64.8 <= anchor[0] <= 75.2
    assert [t.content for t in final_texts] == [t.content for t in texts]
    assert final_texts[1] == texts[1]
    assert dim.p1 == (40, 50) and dim.p2 == (100, 50) and dim.base == (70, 60) and dim.value == 60


def test_linear_label_leader_still_refuses_when_the_whole_real_dimension_line_is_blocked():
    dim, _owner, texts, items = _linear_label_case(((35, 60), (105, 60)))
    with pytest.raises(SheetOverflow):
        settle_annotations(texts, items, (dim,), {'top': (0, 0)}, make_frame('A3'))


def _document(part):
    return json.loads(dict(part.files)['json'].read_text())


def _verify_outputs(part):
    files = dict(part.files)
    assert set(files) == {'dxf', 'svg', 'pdf', 'json'}
    assert all(path.stat().st_size for path in files.values())
    document = _document(part)
    drawing = ezdxf.readfile(files['dxf'])
    assert not drawing.audit().errors
    native = tuple(drawing.modelspace().query('DIMENSION'))
    assert len(native) == len(document['dimensions']) == 83
    for entity, dimension in zip(native, document['dimensions']):
        assert entity.get_measurement() == pytest.approx(dimension['value'], abs=1e-6)
    sources = [tuple(source) for dim in document['dimensions']
               for source in (dim['source'], *dim['shared_sources'])]
    assert len(sources) == len(set(sources)) == 119
    assert document['sheet'] == 'A2' and document['drawing_scale'] == .1
    svg = ET.parse(files['svg']).getroot()
    assert svg.get('width') == '594mm' and svg.get('height') == '420mm'
    assert any(t.text == '未经制图审核' for t in svg.findall('{*}text'))
    assert len(document['reference_confirmations']) == 4
    return document


@pytest.fixture(scope='module')
def dense_source(tmp_path_factory):
    step = Path(__file__).parent / 'fixtures/nist_ctc01_ap242.stp'
    assert hashlib.sha256(step.read_bytes()).hexdigest() == '85a5752da05f53c456ca3a9e038c90358e1d5a3141d1f0d6e5f0970f2356e821'
    folder = tmp_path_factory.mktemp('frozen-dense')
    waiting_dir = folder / 'waiting'
    waiting_dir.mkdir()
    with pytest.raises(ReferenceRequired) as waiting:
        pipeline.render_part(step, waiting_dir, sheet='A2', with_questions=True)
    assert waiting.value.snapshot['basis_sha'] == REFERENCE['basis_sha']
    assert not list(waiting_dir.iterdir())
    first_dir = folder / 'base'
    first_dir.mkdir()
    first = pipeline.render_part(step, first_dir, stem='dense', sheet='A2',
                                 with_questions=True, reference_in=REFERENCE)
    document = _verify_outputs(first)
    rows = [(c['feature'], c['dof'], c['value'], c['quantity']) for c in document['constraints']]
    assert hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(',', ':')).encode()).hexdigest() == FROZEN_CONSTRAINTS
    return step, first, document


def test_dense_fit_answers_keep_frozen_numeric_sources_views_and_parent_files(dense_source, tmp_path):
    step, first, before = dense_source
    frozen_bytes = {kind: path.read_bytes() for kind, path in first.files}
    answers = tuple(Answer(q.id, 'H7') for q in first.questions if q.kind == 'special_tolerance'
                    and any(o.id == 'H7' for o in q.options))
    fitted_dir = tmp_path / 'fit'
    fitted_dir.mkdir()
    fitted = pipeline.render_part(step, fitted_dir, stem='dense', sheet='A2',
                             with_questions=True, reference_in=REFERENCE,
                             questions_in=answers, questions_ref=first.questions)
    after = _verify_outputs(fitted)
    assert after['views'] == before['views']
    assert after['reference_confirmations'] == before['reference_confirmations']
    assert [(c['feature'], c['dof'], c['value'], c['quantity']) for c in after['constraints']] == [
        (c['feature'], c['dof'], c['value'], c['quantity']) for c in before['constraints']]
    for old, new in zip(before['dimensions'], after['dimensions']):
        assert {k: v for k, v in new.items() if k != 'text'} == {k: v for k, v in old.items() if k != 'text'}
    fitted_bytes = {kind: path.read_bytes() for kind, path in fitted.files}
    model, _features, scheme = _derived(step)
    bound = bind_scheme(scheme, model)
    question = next(q for q in fitted.questions if q.kind == 'datum_choice' and q.datum == 'A')
    face = max((f for f in bound.datum_candidates if f.axis == question.axis), key=lambda f: f.position)
    assert any(option.id == face.id and option.axis == question.axis for option in question.options)
    next_dir = tmp_path / 'datum-after-fit'
    next_dir.mkdir()
    changed = _verify_outputs(pipeline.render_part(step, next_dir, stem='dense', sheet='A2',
                              with_questions=True, reference_in=REFERENCE,
                              questions_in=(*answers, Answer(question.id, face.id)), questions_ref=first.questions))
    assert changed['views'] == after['views']
    for previous, current in zip(after['constraints'], changed['constraints']):
        if previous['dof'] != 'x':
            assert current == previous
    for previous, current in zip(after['dimensions'], changed['dimensions']):
        if previous['kind'] != 'position' or previous['axis'] != 'x':
            assert current == previous
    assert all(path.read_bytes() == fitted_bytes[kind] for kind, path in fitted.files)
    assert all(path.read_bytes() == frozen_bytes[kind] for kind, path in first.files)


def test_dense_datum_replay_changes_only_its_related_numeric_geometry(dense_source, tmp_path):
    step, first, before = dense_source
    model, features, scheme = _derived(step)
    bound = bind_scheme(scheme, model)
    question = next(q for q in first.questions if q.kind == 'datum_choice' and q.datum == 'A')
    face = max((f for f in bound.datum_candidates if f.axis == question.axis), key=lambda f: f.position)
    assert any(option.id == face.id and option.axis == question.axis for option in question.options)
    after = _verify_outputs(pipeline.render_part(step, tmp_path, stem='dense', sheet='A2',
                             with_questions=True, reference_in=REFERENCE,
                             questions_in=(Answer(question.id, face.id),), questions_ref=first.questions))
    assert after['views'] == before['views']
    assert after['reference_confirmations'] == before['reference_confirmations']
    original = {(c['feature'], c['dof']): c for c in before['constraints']}
    updated = {(c['feature'], c['dof']): c for c in after['constraints']}
    assert original.keys() == updated.keys()
    for key, row in original.items():
        if key[1] != 'x':
            assert updated[key] == row
    assert any(updated[k]['value'] != row['value'] for k, row in original.items() if k[1] == 'x')
    for old, new in zip(before['dimensions'], after['dimensions']):
        if old['kind'] != 'position' or old['axis'] != 'x':
            assert new == old

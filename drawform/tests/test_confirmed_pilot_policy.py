"""1A–5A：独立零件分母、明确采用、人工净改动和按审核人分组的观察。"""

import json
import shutil

import pytest

from drawform.eval.pilot import PilotError, record_corrections, record_observation, register_case, report
from tests.test_pilot_records import add_job, answer, append, pilot, question, review


def test_review_quality_and_end_to_end_denominators_do_not_hide_refusals(pilot):
    book, work = pilot
    for i, state in enumerate(('drawn', 'refused', 'crashed')):
        job = f'{i:012x}'
        add_job(work, job, state=state)
        register_case(book, work, job, str(i))
    review(work, '0'*12, 'accepted')
    result = report(book, work)
    assert result['first_pass']['rate'] == 1
    assert result['end_to_end_direct'] == {'numerator': 1, 'denominator': 3, 'rate': 1/3}
    assert result['not_reviewed'] == {'refused': 1, 'crashed': 1}


def test_manual_upload_waits_for_review_and_later_acceptance_is_amended(pilot):
    book, work = pilot
    job = add_job(work)['job']
    register_case(book, work, job, 'part')
    append(work/'reviews.jsonl', {'job':job, 'decision':'amended', 'reviewer':'甲',
                                 'effective_sha':'uploaded-sha', 'reasons':[]})
    assert report(book, work)['first_pass']['denominator'] == 0
    review(work, job, 'accepted')
    result = report(book, work)
    assert result['first_pass']['amended'] == 1 and result['first_pass']['accepted'] == 0


def test_normal_answer_render_does_not_become_manual_correction(pilot):
    book, work = pilot
    job = add_job(work, questions=[question('q')])['job']
    register_case(book, work, job, 'part')
    answer(work, job, [{'question_id':'q', 'chosen_option_id':'z-option'}])
    review(work, job, 'accepted')
    assert report(book, work)['first_pass']['accepted'] == 1
    assert report(book, work)['changes']['manual_net']['rate'] is None


def test_byte_identical_upload_is_not_counted_as_a_manual_edit(pilot):
    book,work=pilot
    job=add_job(work)['job']
    register_case(book,work,job,'part')
    append(work/'reviews.jsonl',{'job':job,'decision':'amended','reviewer':'甲','reasons':[],
                                'draft_sha':'same-sha','effective_sha':'same-sha'})
    assert report(book,work)['first_pass']['denominator']==0
    review(work,job,'accepted')
    assert report(book,work)['first_pass']['accepted']==1


@pytest.mark.parametrize('size,options,unknown,expected', [(4,2,False,[]), (5,2,False,['all_rapid_first']),
                                                      (5,1,False,[]), (5,2,True,['all_unknown'])])
def test_five_observations_and_multiple_candidates_are_required(pilot, size, options, unknown, expected):
    book, work = pilot
    qs = [question(str(i)) for i in range(size)]
    for q in qs:q['options'] = q['options'][:options]
    job = add_job(work, questions=qs)['job']
    register_case(book, work, job, 'part')
    answer(work, job, [{'question_id':q['id'], 'chosen_option_id':None if unknown else 'z-option'} for q in qs])
    for q in qs:
        record_observation(book, work, job, q['id'], elapsed_seconds=5, first_option_id='z-option', observer='观察员')
    result = report(book, work, rapid_seconds=5)
    assert result['alerts'] == expected
    if options == 1:assert result['rapid_first']['count'] == 0


def test_observations_from_different_reviewers_are_not_pooled(pilot):
    book, work = pilot
    for i in range(2):
        qs = [question(f'{i}-{n}') for n in range(4)]
        job = add_job(work, f'{i:012x}', questions=qs)['job']
        register_case(book, work, job, str(i))
        append(work/'reviews.jsonl', {'job':job,'event':'answers','state':'drawn','request_key':str(i),
                                    'reviewer':str(i), 'answers':[{'question_id':q['id'],'chosen_option_id':None} for q in qs]})
    assert report(book, work)['alerts'] == []


def corrections_record(items):
    return {'reviewer':'人工核对夹具','complete':True,'answer_changes_excluded':True,'items':items}


@pytest.mark.parametrize('net,expected', [(True,1),(False,0)])
def test_same_object_twice_counts_once_net_and_twice_effort(pilot, net, expected):
    book, work = pilot
    row = add_job(work)
    job = row['job']
    for n in (1,2):
        shutil.copy(work/job/'sheet.dxf', work/job/f'amended-{n}.dxf')
        row['files'].append([f'amended-{n}',f'amended-{n}.dxf'])
    append(work/'jobs.jsonl',row)
    register_case(book, work, job, 'part')
    review(work,job,'amended')
    record_corrections(book,work,job,corrections_record([{'object_id':'dimension-A','net_changed':net,
                        'versions':[1,2],'reason':'定位调整后再次调整','evidence':'核对图纸A位置'}]))
    result = report(book,work)['changes']
    assert result['manual_net']['rate'] == expected
    assert result['manual_adjacent_sum']['rate'] == 2


def test_confirmed_zero_is_distinct_from_missing_and_stale_record(pilot):
    book, work = pilot
    job = add_job(work)['job']
    register_case(book,work,job,'part')
    review(work,job,'accepted')
    assert report(book,work)['changes']['manual_net']['rate'] is None
    record_corrections(book,work,job,corrections_record([]))
    assert report(book,work)['changes']['manual_net']['rate'] == 0
    path=work/job/'sheet.dxf'
    path.write_bytes(path.read_bytes()+b'\n')
    assert report(book,work)['changes']['manual_net']['rate'] is None


def test_engine_answer_version_cannot_be_claimed_as_manual_correction(pilot):
    book,work=pilot
    row=add_job(work)
    job=row['job']
    shutil.copy(work/job/'sheet.dxf',work/job/'amended-1.dxf')
    row['files'].append(['amended-1','amended-1.dxf'])
    row['answer_runs']=[{'state':'drawn','version':1,'questions':[]}]
    append(work/'jobs.jsonl',row)
    register_case(book,work,job,'part')
    with pytest.raises(PilotError,match='人工回传'):
        record_corrections(book,work,job,corrections_record([{'object_id':'x','net_changed':True,
                           'versions':[1],'reason':'答案','evidence':'v1'}]))

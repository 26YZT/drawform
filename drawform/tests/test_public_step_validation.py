"""公开模型验证器不得把模拟状态洗成人工审核，也不得混入未筛选或被改写的输入。"""

import importlib.util
import json
from pathlib import Path
import sys

import pytest

SCRIPTS=Path(__file__).resolve().parents[1]/'deploy'
sys.path.insert(0,str(SCRIPTS))
try:
    spec=importlib.util.spec_from_file_location('public_step_validation',SCRIPTS/'verify_public_step.py')
    validation=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(validation)
finally:
    sys.path.remove(str(SCRIPTS))


@pytest.fixture
def catalog(tmp_path):
    # 此夹具只测清单读取，不送入 OCCT；实际模型由 HTTPS 演练验证。
    data=b'file identity fixture'
    (tmp_path/'model.step').write_bytes(data)
    (tmp_path/'license.txt').write_text('license evidence fixture')
    digest=validation.sha(data)
    body={'scope':'public_step_technical_validation','human_review':False,'production_release':False,
          'selection_locked_before_render':True,'license_evidence':{'file':'license.txt','sha256':validation.sha((tmp_path/'license.txt').read_bytes())},
          'models':[{'id':'public-'+digest[:12],'file':'model.step','sha256':digest,'bytes':len(data),
                     'license':'CC-BY-3.0','license_screening':'included','author_attribution':['test author'],
                     'source_url':'https://example.invalid/pinned-model','commit':'a'*40}]}
    path=tmp_path/'catalog.json';path.write_text(json.dumps(body))
    return path,body


@pytest.mark.parametrize('field',['human_review','production_release'])
def test_simulated_approval_cannot_be_declared_as_human_or_production_approval(catalog,field):
    path,body=catalog
    body[field]=True;path.write_text(json.dumps(body))
    with pytest.raises(ValueError,match='无人工审核'):
        validation.read_catalog(path)


def test_changed_model_or_license_is_rejected_before_service_start(catalog):
    path,body=catalog
    assert validation.read_catalog(path)['models']
    (path.parent/'model.step').write_bytes(b'changed')
    with pytest.raises(ValueError,match='指纹'):
        validation.read_catalog(path)
    (path.parent/'license.txt').write_text('different declaration')
    with pytest.raises(ValueError,match='许可证据'):
        validation.read_catalog(path)


def test_excluded_license_and_missing_authorship_cannot_enter_run(catalog):
    path,body=catalog
    body['models'][0]['license_screening']='excluded';path.write_text(json.dumps(body))
    with pytest.raises(ValueError,match='许可'):
        validation.read_catalog(path)
    body['models'][0]['license_screening']='included';body['models'][0]['author_attribution']=[]
    path.write_text(json.dumps(body))
    with pytest.raises(ValueError,match='署名'):
        validation.read_catalog(path)


def test_escaped_paths_and_duplicate_files_cannot_inflate_samples(catalog):
    path,body=catalog
    body['models'].append(dict(body['models'][0]));path.write_text(json.dumps(body))
    with pytest.raises(ValueError,match='重复'):
        validation.read_catalog(path)
    body['models']=body['models'][:1];body['models'][0]['file']='../model.step';path.write_text(json.dumps(body))
    with pytest.raises(ValueError,match='目录内'):
        validation.read_catalog(path)


def test_automatic_answers_only_select_an_engine_option_or_explicit_unknown():
    cards=[{'id':'q1','kind':'special_tolerance','options':[{'id':'H7'},{'id':'H8'}]},
           {'id':'q2','kind':'datum_choice','options':[{'id':'geometry-fingerprint'}]},
           {'id':'q3','kind':'special_tolerance','options':[{'id':'H7'}]}]
    answers=validation.answer_payload(cards)['answers']
    assert [a['option_id'] for a in answers]==['H7',None,None]
    assert all('value' not in a for a in answers)
    cards[0]['options']=[]
    with pytest.raises(ValueError,match='候选为空'):
        validation.answer_payload(cards)


def test_summary_keeps_refusals_failures_and_simulated_acceptance_separate():
    rows=[{'outcome':kind} for kind in ('simulated_accepted','engine_refused','engine_crashed','error')]
    result=validation.summary(rows,5)
    assert result['requested']==5 and result['processed']==4
    assert result['simulated_accepted']==result['engine_refused']==result['engine_crashed']==result['error']==1
    assert result['true_human_approved']==0 and result['real_parts_first_pass_rate'] is None
    assert result['production_release'] is False

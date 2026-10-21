""""这张图为什么这么画"的问答层。

KR 里写的是"工程知识 RAG 与 Agent 能力"。这里做的是**只对自己算得出的事实做检索**：
出图说明里的每一条都是本次跑批算出来的（基准取了哪个坐标、1020 条约束里几条带公差、
哪一环被留空、哪些故意没画、哪些标准条款没核对），问答不许越过这些事实编话。

所以本模块最重要的性质不是"答得多"，而是**答不上来时明确说答不上来**。
集团自己的制图规范还没拿到（见 `docs/intake/老图纸取数清单.md` 第 7 节），
在那之前任何"按贵集团惯例"的回答都是编的。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from drawform.knowledge import NO_ANSWER, ask, index_of
from drawform.output.assumptions import assumptions_for

CORPUS = Path(__file__).resolve().parents[2] / "corpus" / "cadsy"


@pytest.fixture(scope="session")
def facts():
    """索引必须建在真产出上：手写几条假事实来测检索，等于测自己写的字典。"""
    from drawform.delivery import gate_for, require_allowed
    from drawform.dimensioning.place import place_datum_marks, place_scheme
    from drawform.dimensioning.scheme import derive_scheme
    from drawform.geometry.features import extract_features
    from drawform.geometry.loader import import_step
    from drawform.geometry.projection import FRONT, LEFT, TOP, project_view

    # part_0004：有孔、有定位尺寸、有螺栓圆、有封闭尺寸链 —— 事实最全的一类真件。
    model = import_step(CORPUS / "samples" / "part_0004" / "model.step")
    require_allowed(gate_for(model))
    features = extract_features(model.shape, model.bbox)
    scheme = derive_scheme(model, features)
    projections = {d.name: project_view(model.shape, d) for d in (FRONT, TOP, LEFT)}
    dims = place_scheme(scheme, projections)
    marks = place_datum_marks(scheme, projections, dims)
    verdict = gate_for(model, scheme, dims)
    return index_of(assumptions_for(model, scheme, dims, marks, verdict))



def test_the_index_covers_every_assumption_it_was_given(facts):
    assert len(facts) >= 4
    assert all(f.claim and f.basis for f in facts)


def test_asking_about_the_datum_returns_the_datum_fact(facts):
    hits = ask("这些尺寸是从哪个面量起的？", facts)

    assert [f.fact_id for f in hits] and hits[0].fact_id == "datum-face"
    assert "包围盒" in hits[0].claim


def test_asking_about_tolerances_says_the_truth_about_zero_of_them(facts):
    hits = ask("这张图的公差是多少？", facts)

    assert hits[0].fact_id == "general-tolerance"
    assert "公差" in hits[0].claim


def test_an_unanswerable_question_is_refused_instead_of_invented(facts):
    """热处理温度、材料密度、供应商价格都不在算得出的事实里 —— 只能拒答。"""
    hits = ask("这批件该用什么热处理工艺？", facts)

    assert hits == (NO_ANSWER,)
    assert "没有" in NO_ANSWER.claim


def test_no_fact_claims_to_have_been_checked_against_group_drawings(facts):
    """今天一条都没有跟集团真实图纸核对过。面板必须把这件事说出来，不能装作核对过。"""
    assert all(f.checked is False for f in facts)


def test_an_assumption_we_have_no_words_for_is_not_indexed():
    """将来有人给《出图说明》加一条而没配检索词表时，它必须**不被回答**，
    而不是被一条半对半错的解释蒙过去。"""
    entries = [{"id": "datum-face", "claim": "从包围盒最小角量起", "basis": "place.py",
                "impact": "换基准全部变"},
               {"id": "surface-treatment", "claim": "镀层按…", "basis": "…", "impact": "…"}]

    indexed = index_of(entries)

    assert [f.fact_id for f in indexed] == ["datum-face"]


def test_a_question_that_matches_several_facts_keeps_the_strongest_hit_first(facts):
    hits = ask("标题栏和视图是按哪个标准画的，哪些还没核对？", facts)

    assert hits[0].fact_id == "title-block-unverified"
    assert hits[1].fact_id == "views-fixed"

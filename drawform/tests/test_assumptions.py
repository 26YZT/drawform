"""出图说明：机器替人做了哪些决定，必须随图交付。

为什么这是产品功能而不是日志：拿不到真工程师回答（见项目记忆），所以"停下来问人"
这条路是死的。但**不装懂**是可以做到的 —— 把系统实际做出的决定、依据、以及它会影响
哪些数字，写成一份工程师能核、能改的清单。

红线：每一条都必须**从已有数据结构里算出来**。写死一句"基准取包围盒最小角"而不去
读实际用的那个面，就是又造了一个看起来确定、实际没验证的东西 —— 这个项目已经栽过
四次。
"""

import json

import pytest

from drawform.delivery import gate_for, unplaced_dimensions
from drawform.dimensioning.constraints import Chain, Sized
from drawform.dimensioning.place import place_datum_marks, place_scheme
from drawform.dimensioning.scheme import derive_scheme
from drawform.geometry.features import extract_features
from drawform.geometry.loader import import_step
from drawform.geometry.projection import FRONT, LEFT, TOP, project_view
from drawform.output.assumptions import assumptions_for
from drawform.output.sheet import make_frame


def _prepared(model):
    features = extract_features(model.shape, model.bbox)
    scheme = derive_scheme(model, features)
    projections = {d.name: project_view(model.shape, d) for d in (FRONT, TOP, LEFT)}
    dimensions = place_scheme(scheme, projections)
    marks = place_datum_marks(scheme, projections, dimensions)
    return scheme, projections, dimensions, marks


def _by_id(items):
    return {item["id"]: item for item in items}


def test_the_datum_assumption_names_the_faces_actually_used(plate_step):
    """基准这条不能只写一句"取包围盒最小角"，要给出**这张图真的用了哪个坐标**。

    写死的措辞和真实落位之间一旦分叉（比如以后基准改成对称面），这份说明就会
    开始说谎，而它存在的唯一理由就是不说谎。
    """
    model = import_step(plate_step)
    scheme, projections, dimensions, marks = _prepared(model)

    datum = _by_id(assumptions_for(model, scheme, dimensions, marks))["datum-face"]

    assert datum["basis"] == "place.py 取投影包围盒最小边"
    # 记世界坐标，不是"最小角"这种听起来对、实际查不了的描述；
    # 且只列真被引用的基准 —— 板上的孔都沿 z 钻，C 没被用到就不该出现在清单里
    assert datum["value"] == {"A": pytest.approx(model.origin[0]),
                              "B": pytest.approx(model.origin[1])}
    assert "定位尺寸" in datum["impact"]


def test_the_tolerance_assumption_counts_what_is_actually_specified(plate_step):
    """"全部走一般公差"这句话必须由计数支撑，不能是印象。"""
    model = import_step(plate_step)
    scheme, projections, dimensions, marks = _prepared(model)

    tol = _by_id(assumptions_for(model, scheme, dimensions, marks))["general-tolerance"]

    sized = [c for c in scheme.plan.constraints if isinstance(c, Sized)]
    assert tol["value"]["total"] == len(sized)
    assert tol["value"]["with_explicit_tolerance"] == sum(1 for c in sized
                                                          if c.tolerance is not None)
    assert tol["value"]["note"] == "一般公差 ISO 2768-m"


def test_an_open_chain_link_is_reported_with_which_link_was_left_out(stepped_plate_step):
    """留一环是决定，不是遗漏。要说清留的是哪一环、为什么。"""
    model = import_step(stepped_plate_step)
    scheme, projections, dimensions, marks = _prepared(model)

    chains = [c for c in scheme.plan.constraints if isinstance(c, Chain)]
    assert chains, "夹具应当产生链"

    item = _by_id(assumptions_for(model, scheme, dimensions, marks))["open-chains"]

    assert item["value"]["count"] == len(chains)
    reported = {link for entry in item["value"]["chains"] for link in entry["left_open"]}
    assert reported == {c.reference[0][0] for c in chains}


def test_a_deliberately_undrawn_dimension_is_listed(plate_step):
    """故意没画的（奇数孔 PCD 那类）要和"漏了"分开写，否则读的人以为可以等它出现。

    板上没有未落位尺寸，所以这里**抽掉一条已落位的**来构造那个状态 —— 测的是
    "有未落位时必须列出来"这个映射，不是某个具体零件。
    """
    from dataclasses import replace

    model = import_step(plate_step)
    scheme, projections, dimensions, marks = _prepared(model)
    dropped = tuple(d for d in dimensions if d.source != ("outline", "length"))
    verdict = gate_for(model, scheme, dropped)

    items = _by_id(assumptions_for(model, scheme, dropped, marks, verdict=verdict))

    assert ("outline", "length") in [tuple(p) for p in items["not-drawn"]["value"]["unplaced"]]


def test_no_not_drawn_entry_when_everything_landed(plate_step):
    """全都落位时不该冒出一条空的"故意没画"。空条目会让人以为系统在隐瞒什么。"""
    model = import_step(plate_step)
    scheme, projections, dimensions, marks = _prepared(model)
    verdict = gate_for(model, scheme, dimensions)

    items = _by_id(assumptions_for(model, scheme, dimensions, marks, verdict=verdict))

    assert "not-drawn" not in items


def test_views_are_declared_as_fixed_not_chosen(plate_step):
    """视图是写死的三视图，不是按零件选的 —— 这是产品目前没参与的那个决策。"""
    model = import_step(plate_step)
    scheme, projections, dimensions, marks = _prepared(model)

    item = _by_id(assumptions_for(model, scheme, dimensions, marks))["views-fixed"]

    assert item["value"]["chosen"] is False
    assert sorted(item["value"]["views"]) == ["front", "left", "top"]


def test_every_assumption_carries_a_basis_and_an_impact(plate_step, tmp_path):
    """没有依据或没有影响的条目不算一条假设，只是噪音。"""
    model = import_step(plate_step)
    scheme, projections, dimensions, marks = _prepared(model)

    for item in assumptions_for(model, scheme, dimensions, marks):
        assert item["id"] and item["claim"], item
        assert item["basis"], f"{item['id']} 没说依据，读的人无从核对"
        assert item["impact"], f"{item['id']} 没说什么会因此改变"

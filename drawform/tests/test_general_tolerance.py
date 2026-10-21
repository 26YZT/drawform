"""把标题栏那句「一般公差 ISO 2768-m」落到每条尺寸上。

图上**不画**逐条公差框格 —— 未注公差按画法就是由标题栏统一声明的，画上去反而不合规范。
这一层要买的是另外两件事：

1. **校对人问"这条 ⌀20 到底允许差多少"时，系统要答得出**，而且答案是从核对过的数表
   推出来的，不是现编的；
2. **交付门要能发现"这句承诺盖不住图上的某条尺寸"**（尺寸超出 >0.5–4000、或该等级
   在该段是空格）。那等于图纸上写了一句不成立的话，比不写更糟。

⚠️ 数表只按 ISO 2768-1 的**线性**那张核对过（两份独立转录逐格一致）。角度未注公差在
同一标准里是另一张表（按边长分档），GB/T 1804 数值虽然一致但我们没核对过它 ——
所以这两种情况一律标"未覆盖"，**不许拿线性值凑**。
"""

from __future__ import annotations

import json

import pytest

from drawform.dimensioning.constraints import Chain, Plan, Sized
from drawform.dimensioning.general import broken_promises, general_tolerances


def _plan(*items, grade="ISO 2768-m") -> Plan:
    return Plan(constraints=items, general_tolerance=grade)


def test_the_declared_general_tolerance_reaches_every_size_without_its_own():
    rows = general_tolerances(_plan(
        Sized("outline", "length", 80.0),
        Sized("bore:1", "diameter", 20.0),
    ))

    # 期望值一律照 rules/iso2768.py 的表读，不凭记忆写：>30–120 的 m 是 0.3，
    # >6–30 的 m 是 0.2。第一版我把 20 那条写成 0.1，正是本项目"数表不许凭记忆填"
    # 那条规矩在测试侧的复发。
    assert [(r["dof"], r["plus_minus"]) for r in rows] == [("length", 0.3),
                                                           ("diameter", 0.2)]
    assert rows[0]["band"] == ">30–120", "要能说出这条落在哪个尺寸段，人才核对得了"
    assert all(r["covered"] for r in rows)


def test_a_dimension_with_its_own_tolerance_is_not_overwritten():
    """专门公差压过一般公差。给 H7 的孔再套一个 ±0.2，等于把图纸改成自相矛盾。"""
    rows = general_tolerances(_plan(
        Sized("bore:1", "diameter", 20.0, tolerance="H7"),
        Sized("bore:2", "diameter", 20.0),
    ))

    assert [(r["feature"], r["covered"]) for r in rows] == [("bore:2", True)]


def test_an_angular_dimension_never_gets_a_linear_number():
    """角度未注公差是另一张表。45° 配 ±0.3 **mm** 是胡说，哪怕数字看起来合理。"""
    rows = general_tolerances(_plan(Sized("hole", "angle", 45.0)))

    assert rows[0]["covered"] is False
    assert "角度" in rows[0]["why"]


def test_the_assumption_names_the_real_reason_not_a_default_one(plate_step):
    """impact 那句必须读真实缺口，不能写死「角度是另一张表」。

    真实语料里未覆盖的 5 条全是 0.5 mm 的壁厚/深度，一句写死的话会把复核人
    引到一个根本不存在的原因为什么要修 —— 这与本模块开头"不装懂"的立场相反。
    """
    from drawform.dimensioning.scheme import derive_scheme
    from drawform.geometry.features import Bore
    from drawform.geometry.loader import import_step
    from drawform.output.assumptions import _tolerance

    model = import_step(plate_step)
    scheme = derive_scheme(model, [Bore("b", 3.0, (20.0, 20.0, 0.0),
                                        (0.0, 0.0, 1.0), 0.5,
                                        axis_span=(0.0, 0.5), mouth=0.5)])

    entry = _tolerance(model, scheme)

    assert entry["value"]["uncovered"] == 1
    assert "覆盖段" in entry["impact"], entry["impact"]
    assert "角度" not in entry["impact"], "这张图上根本没有角度尺寸"


def test_a_plan_with_no_gap_does_not_invent_one(bearing_seat_step):
    from drawform.dimensioning.scheme import derive_scheme
    from drawform.geometry.features import extract_features
    from drawform.geometry.loader import import_step
    from drawform.output.assumptions import _tolerance

    model = import_step(bearing_seat_step)
    scheme = derive_scheme(model, extract_features(model.shape, model.bbox))

    entry = _tolerance(model, scheme)

    assert entry["value"]["uncovered"] == 0
    assert "未覆盖" not in entry["impact"], "没有缺口就别在图上留一句让人找茬的话"


def test_a_zero_position_is_a_coincidence_not_a_missing_tolerance():
    """0 表达的是"与基准面共面"，不是一段长度。

    它既不该被算成"承诺盖不住"（那会让 4 件正常零件莫名背上提示），
    也不该从图上消失（那条尺寸是那个孔在该方向上唯一的定位依据）。
    """
    rows = general_tolerances(_plan(Sized("bore-edge", "y", 0.0)))

    assert rows[0]["covered"] is False
    assert rows[0]["kind"] == "coincidence"
    assert broken_promises(rows) == ()


def test_a_gb_declaration_is_not_filled_with_iso_numbers():
    """数值虽然一致，但我们只核对过 ISO 2768-1 那一份。换标准就要重新核对。"""
    rows = general_tolerances(_plan(Sized("outline", "length", 80.0),
                                    grade="GB/T 1804-m"))

    assert rows[0]["covered"] is False
    assert "未核对" in rows[0]["why"]


def test_a_size_outside_the_table_is_reported_not_dropped():
    rows = general_tolerances(_plan(Sized("outline", "length", 5000.0)))

    assert rows[0]["covered"] is False
    assert rows[0]["kind"] == "out_of_range", "超出范围与表里留空是两回事，要分得开"
    assert "4000" in rows[0]["why"], "要说清为什么盖不住，不能只说「不行」"


def test_a_blank_cell_in_the_standard_stays_blank():
    """f 级在 >2000–4000 段是空的。标准没给值就是没给值，不补。"""
    rows = general_tolerances(_plan(Sized("outline", "length", 3000.0),
                                    grade="ISO 2768-f"))

    assert rows[0]["covered"] is False


def test_an_uncovered_size_is_written_into_the_note_not_refused(plate_step):
    """0.5 mm 的壁厚是正当尺寸。

    第一版我把这条做成一个新的拦项，实测真实语料 **4/65 件**因此拒画 —— 而缺陷只是
    标题栏那句话说过宽了。拒画让工具少做 6% 的活，把话写准两行就够。
    """
    from drawform.dimensioning.scheme import derive_scheme
    from drawform.geometry.features import Bore
    from drawform.geometry.loader import import_step

    model = import_step(plate_step)
    shallow = Bore("bore-shallow", 3.0, (20.0, 20.0, 0.0), (0.0, 0.0, 1.0), 0.5,
                   axis_span=(0.0, 0.5), mouth=0.5)
    scheme = derive_scheme(model, [shallow])

    note = next(n for n in scheme.notes if n.startswith("一般公差"))
    assert "不在该表覆盖段" in note and "1 条" in note, note


def test_a_part_with_an_uncovered_size_still_passes_the_gate():
    """钉住这个决定：交付门不许因为一般公差的覆盖段新增拦项。"""
    from drawform.delivery import check_delivery

    verdict = check_delivery(is_valid=True, unclassified_faces=0,
                             plan=_plan(Sized("outline", "length", 5000.0)),
                             unknowns=(("outline", "length"),))

    assert verdict.allowed, verdict.reasons


def test_an_angular_dimension_never_makes_the_note_hedge():
    """对照组：角度没表可套是**我们的进度**，不是图纸在说谎。

    没有这条，「凡未覆盖即提示」也能全绿 —— 而那句提示会变成每张带角度的图上都
    挂一句没根据的话。
    """
    rows = general_tolerances(_plan(Sized("hole", "angle", 45.0)))

    assert rows[0]["covered"] is False
    assert broken_promises(rows) == ()


def test_an_unverifiable_standard_does_not_hedge_the_note_either():
    """没核对过的标准不能拿 ISO 的数去替它说话，也不该因此改提示措辞。"""
    rows = general_tolerances(_plan(Sized("outline", "length", 80.0),
                                    grade="GB/T 1804-m"))

    assert rows[0]["covered"] is False
    assert broken_promises(rows) == ()


def test_a_plan_that_declares_nothing_is_not_checked():
    """没声明就不检查 —— 那条判据由 `no_tolerance_home` 负责，别在这里重复立法。"""
    assert general_tolerances(Plan(constraints=(Sized("outline", "length", 80.0),))) == ()


def test_chains_are_not_dimensions_with_a_nominal_size():
    """链的 total 是封闭环，不是"这条尺寸允许差多少"的主语。"""
    rows = general_tolerances(_plan(
        Chain("loop", (("a", "gap"), ("b", "gap")), total=80.0),
        Sized("a", "gap", 30.0), Sized("b", "gap", 50.0),
    ))

    assert [r["feature"] for r in rows] == ["a", "b"]


def test_the_json_says_what_each_dimension_is_actually_held_to(bearing_seat_step, tmp_path):
    """交付 JSON 是下游（批量核对、PDM）读的那一份，缺这段就等于没说。"""
    import json

    from drawform.pipeline import render_part

    files = dict(render_part(bearing_seat_step, tmp_path, stem="part").files)
    document = json.loads(files["json"].read_text(encoding="utf-8"))

    rows = [c for c in document["constraints"] if c["tolerance"] is None]
    assert rows, "真实产出里绝大多数尺寸走的就是未注公差"
    for row in rows:
        assert row["general"]["covered"] is True, row
        assert row["general"]["plus_minus"] > 0
        assert row["general"]["standard"] == "ISO 2768-1"


def test_the_assumption_reports_how_far_the_declaration_reaches(bearing_seat_step,
                                                               tmp_path):
    from drawform.pipeline import render_part

    files = dict(render_part(bearing_seat_step, tmp_path, stem="part").files)
    document = json.loads(files["json"].read_text(encoding="utf-8"))
    entry = next(a for a in document["assumptions"] if a["id"] == "general-tolerance")

    assert entry["value"]["covered"] >= 1
    assert entry["value"]["widest"] >= entry["value"]["narrowest"] > 0
    assert entry["value"]["uncovered"] == 0


@pytest.mark.parametrize("grade,expected", [("m", 0.1), ("f", 0.05), ("c", 0.3),
                                            ("v", 0.5)])
def test_all_four_grades_come_from_the_table_not_from_a_formula(grade, expected):
    """单调性守卫：同一尺寸段 f<m<c<v（>3–6 段实际是 0.05/0.1/0.3/0.5）。
    写错列或写成公式，这条会当场变红。"""
    from drawform.rules.iso2768 import general_tolerance

    assert general_tolerance(grade, 5.0) == expected

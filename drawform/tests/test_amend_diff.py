"""改后图 vs 机器底稿：把工程师的改动翻译成能统计的动作。

这一步的产物不是"diff"，是**机器该学什么**。所以两类东西必须分开：
"这条尺寸被删了"（取舍问题）与"这个数被改了"（正确性问题）严重度差一个量级，
混在一起统计就会把"我们标错了"稀释成"工程师有个人偏好"。

判据先过对照组：**自家图 vs 自家图必须报 0**。一把把无改动判出差异的尺子，
报出来的所有"驳回原因分布"都是噪音 —— 这个项目为此栽过一次。
"""

from __future__ import annotations

import pathlib
import shutil

import ezdxf
import pytest

from drawform.eval.amend_diff import compare

DRAFT = (pathlib.Path(__file__).resolve().parents[2]
         / "tools" / "dwg_verify" / "samples" / "part_0004.dxf")


@pytest.fixture
def draft(tmp_path):
    path = tmp_path / "part.dxf"
    shutil.copy(DRAFT, path)
    return path


def _edit(path, mutate):
    doc = ezdxf.readfile(str(path))
    mutate(doc)
    doc.saveas(str(path))
    return path


def _dims(doc):
    return [e for space in doc.layouts for e in space if e.dxftype() == "DIMENSION"]


def drop_one(doc):
    _dims(doc)[0].get_geometry_block()          # 确认拿的是真尺寸
    doc.modelspace().delete_entity(_dims(doc)[0])


def test_the_same_sheet_against_itself_reports_nothing(draft, tmp_path):
    """对照组：没有改动就必须一个动作都不报。"""
    other = tmp_path / "copy.dxf"
    shutil.copy(draft, other)

    diff = compare(draft, other)

    assert diff.as_dict() == {}
    assert diff.reasons == ()
    assert diff.blocking is False


def test_a_deleted_dimension_is_a_choice_not_a_correction(draft, tmp_path):
    target = tmp_path / "b.dxf"
    shutil.copy(draft, target)

    diff = compare(draft, _edit(target, drop_one))

    assert diff.as_dict() == {"removed": 1}
    assert diff.reasons == ("extra_dimension",)
    assert diff.blocking is False


def test_a_number_changed_in_place_is_a_correction(draft, tmp_path):
    """同位置、数值不同 → 这是"机器标错了"，必须单独成项并标为阻断级。"""
    target = tmp_path / "b.dxf"
    shutil.copy(draft, target)

    def change(doc):
        block = _dims(doc)[0].get_geometry_block()
        letter = next(e for e in block if e.dxftype() in ("TEXT", "MTEXT"))
        letter.dxf.text = "999"

    diff = compare(draft, _edit(target, change))

    assert diff.as_dict() == {"value_changed": 1}
    assert "wrong_value" in diff.reasons
    assert diff.blocking is True


def test_the_diff_says_which_dimension_changed_not_only_how_many(draft, tmp_path):
    """只报"改了 2 处"的复核台没人会用 —— 要当场看到是哪一条、从几改成几。

    M4-3 的验收标准写的是「**逐项**接受/修改/驳回」，"逐项"要的就是这份明细。
    计数能统计，不能核对。
    """
    target = tmp_path / "b.dxf"
    shutil.copy(draft, target)

    def change(doc):
        block = _dims(doc)[0].get_geometry_block()
        letter = next(e for e in block if e.dxftype() in ("TEXT", "MTEXT"))
        letter.dxf.text = "999"

    diff = compare(draft, _edit(target, change))

    assert [(i["action"], i["before"], i["after"]) for i in diff.items] == [
        ("value_changed", "80", "999")]
    assert len(diff.items[0]["at"]) == 2, "要带图上位置，页面才能把这条挂回那条尺寸"


def test_a_removed_dimension_lists_what_was_lost(draft, tmp_path):
    target = tmp_path / "b.dxf"
    shutil.copy(draft, target)

    diff = compare(draft, _edit(target, drop_one))

    assert [(i["action"], i["before"]) for i in diff.items] == [("removed", "80")]


def test_an_added_dimension_lists_what_arrived(draft, tmp_path):
    target = tmp_path / "b.dxf"
    shutil.copy(draft, target)

    def add_one(doc):
        doc.modelspace().add_linear_dim(base=(0.0, 400.0), p1=(0.0, 380.0),
                                        p2=(60.0, 380.0), text="42").render()

    diff = compare(draft, _edit(target, add_one))

    added = [i for i in diff.items if i["action"] == "added"]
    assert len(added) == 1, diff.items
    assert added[0]["after"], "新加的这条得说清是什么"


def test_the_item_carries_the_position_of_the_dimension_it_reports(draft, tmp_path):
    """明细上的位置必须是**那条尺寸自己**的锚点。

    位置写成常量（或取了别的尺寸的锚点）时，页面上"挂回图上那一条"就是假的 ——
    计数与文字都对，只有位置是空的，而人正是靠位置去找那条尺寸。
    """
    from drawform.eval.amend_diff import _anchors

    target = tmp_path / "b.dxf"
    shutil.copy(draft, target)

    def change(doc):
        block = _dims(doc)[0].get_geometry_block()
        letter = next(e for e in block if e.dxftype() in ("TEXT", "MTEXT"))
        letter.dxf.text = "999"

    diff = compare(draft, _edit(target, change))
    expected = _anchors(draft)[0][0]

    assert diff.items[0]["at"] == pytest.approx(tuple(expected), abs=0.01)


def test_a_dimension_that_changed_several_things_is_listed_once_per_thing(draft, tmp_path):
    """`80` 改成 `2×⌀81` 同时是三件事：数量、符号、数值都错了。

    折叠成一条就把"我们哪里标错了"这个信号丢掉三分之二 —— 这三类挂的理由分类各不相同
    （`wrong_count` / `wrong_symbol` / `wrong_value`），少记一条统计里就少一类证据。
    """
    target = tmp_path / "b.dxf"
    shutil.copy(draft, target)

    def change(doc):
        block = _dims(doc)[0].get_geometry_block()
        letter = next(e for e in block if e.dxftype() in ("TEXT", "MTEXT"))
        letter.dxf.text = "2×⌀81"

    diff = compare(draft, _edit(target, change))

    assert diff.as_dict() == {"wrong_count": 1, "symbol_changed": 1, "value_changed": 1}
    assert sorted(i["action"] for i in diff.items) == ["symbol_changed",
                                                       "value_changed", "wrong_count"]
    assert {i["before"] for i in diff.items} == {"80"}
    assert {i["after"] for i in diff.items} == {"2×⌀81"}
    assert diff.blocking is True


def test_the_control_group_reports_no_items_at_all(draft, tmp_path):
    """对照组：没有改动就没有任何一条明细。计数为 0 而明细非空，是最难被发现的那种错。"""
    other = tmp_path / "copy.dxf"
    shutil.copy(draft, other)

    assert compare(draft, other).items == ()


def test_a_quantity_prefix_change_is_counted_as_wrong_count(draft, tmp_path):
    target = tmp_path / "b.dxf"
    shutil.copy(draft, target)

    def change(doc):
        for dim in _dims(doc):
            block = dim.get_geometry_block()
            letters = [e for e in block if e.dxftype() in ("TEXT", "MTEXT")]
            if letters and letters[0].dxf.text.startswith("4×"):
                letters[0].dxf.text = letters[0].dxf.text.replace("4×", "3×", 1)
                return

    diff = compare(draft, _edit(target, change))

    assert diff.as_dict().get("wrong_count") == 1
    assert "wrong_value" not in diff.reasons


def test_a_dimension_moved_far_away_is_removed_plus_added_never_a_guess(draft, tmp_path):
    """配不上就退回"删 + 加"。把它当成"改了数值"是猜，而猜会教错机器。"""
    target = tmp_path / "b.dxf"
    shutil.copy(draft, target)

    def move(doc):
        dim = _dims(doc)[0]
        dim.dxf.text_midpoint = (dim.dxf.text_midpoint.x + 60.0,
                                 dim.dxf.text_midpoint.y + 60.0, 0.0)

    diff = compare(draft, _edit(target, move))

    assert diff.as_dict().get("removed") == 1
    assert diff.as_dict().get("added") == 1
    assert "value_changed" not in diff.as_dict()


def test_a_moved_datum_letter_is_counted_as_a_datum_change(draft, tmp_path):
    """基准被挪 = 起点换了。这条不是"数值错"，但它是**取舍里最贵的一种**。"""
    target = tmp_path / "b.dxf"
    shutil.copy(draft, target)

    def move_letter(doc):
        for text in [e for e in doc.modelspace() if e.dxftype() in ("TEXT", "MTEXT")]:
            if text.dxf.text.strip() == "A":
                text.dxf.insert = (text.dxf.insert.x + 12.0, text.dxf.insert.y + 12.0, 0.0)
                return
        raise AssertionError("底稿里没有基准代号 A，这条测试的前提不成立")

    diff = compare(draft, _edit(target, move_letter))

    assert diff.as_dict().get("datum_changed") == 1
    assert "datum_choice" in diff.reasons


def test_touching_the_outline_is_reported_as_a_view_change(draft, tmp_path):
    target = tmp_path / "b.dxf"
    shutil.copy(draft, target)

    def add_line(doc):
        doc.modelspace().add_line((0.0, 0.0), (10.0, 10.0), dxfattribs={"layer": "0"})

    diff = compare(draft, _edit(target, add_line))

    assert diff.as_dict().get("geometry_touched") == 1
    assert diff.reasons == ("view_choice",)


def test_pairing_prefers_the_same_label_over_the_nearest_anchor(tmp_path):
    """删掉一条、另一条挪近一点 —— 按"最近"配会把这读成"80 被改成了 7"。

    显示值没变是"同一条尺寸"最强的证据，位置只是次一级的线索。
    这条判据是被一个存活的变异逼出来的。
    """
    import ezdxf

    def build(path, spec):
        doc = ezdxf.new("R2018")
        msp = doc.modelspace()
        for x, label in spec:
            msp.add_line((x, 0.0), (x + 40.0, 0.0))
            msp.add_linear_dim(base=(x, 5.0), p1=(x, 0.0), p2=(x + 40.0, 0.0),
                               text=label).render()
        for dim in msp.query("DIMENSION"):
            dim.dxf.text_midpoint = (dim.dxf.text_midpoint.x, dim.dxf.text_midpoint.y, 0.0)
        doc.saveas(str(path))
        return path

    before = build(tmp_path / "before.dxf", [(0.0, "80"), (1.5, "7")])
    after = build(tmp_path / "after.dxf", [(0.2, "7")])

    diff = compare(before, after)

    actions = diff.as_dict()
    assert actions.get("removed") == 1
    assert "value_changed" not in actions, f"把删除误读成改数值：{actions}"

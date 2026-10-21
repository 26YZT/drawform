"""出图说明须反映当前标题栏，同时保留尚未核对的边界。"""

import json
import xml.etree.ElementTree as ET

from drawform.knowledge import ask, index_of
from drawform.output.assumptions import _title_block
from drawform.output.titleblock import CHANGE_LABELS, SIGN_ROLES
from drawform.pipeline import render_part


def test_title_block_fact_agrees_with_labels_on_the_actual_drawing(plate_step, tmp_path):
    part = render_part(plate_step, tmp_path)
    files = dict(part.files)
    document = json.loads(files["json"].read_text())
    fact = next(row for row in document["assumptions"] if row["id"] == "title-block-unverified")
    texts = {node.text for node in ET.parse(files["svg"]).getroot().findall("{*}text")}
    assert set(CHANGE_LABELS) | set(SIGN_ROLES) <= texts
    statement = " ".join(fact[key] for key in ("claim", "basis", "impact"))
    assert "缺整个更改区" not in statement
    assert "只有一般角色" not in statement
    assert all(role in statement for role in SIGN_ROLES)
    assert "已有" in fact["claim"] and "更改区" in fact["claim"]


def test_current_sections_do_not_turn_unverified_row_heights_into_a_compliance_claim():
    fact = _title_block()
    assert fact["value"]["outline_verified"] is True
    assert fact["value"]["layout_verified"] is False
    assert "中列" in fact["impact"] and "右列" in fact["impact"]
    assert "未逐格核对" in fact["impact"]
    assert "不得声称" in fact["impact"]
    answer = ask("标题栏按哪个标准", index_of([fact]))
    assert len(answer) == 1 and answer[0].fact_id == fact["id"]
    assert answer[0].checked is False

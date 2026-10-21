"""本版文字变更提示必须取真实父版及引擎坐标，不能在浏览器猜位置。"""

import json
import xml.etree.ElementTree as ET

import pytest

from drawform.service import answers as answer_module
from tests.test_live_answers import AUTH, _answer, _drawn, _fit, _submit, _wait, service_factory


def _doc(service, status):
    return service.http.get(status["files"]["json"], headers=AUTH).get_json()


def _finish(service, job_id, answers):
    response = _submit(service, job_id, answers)
    assert response.status_code == 202, response.data
    status = _wait(service, job_id)
    assert status["state"] == "drawn", status
    return status, _doc(service, status)


def test_fit_markers_point_to_the_changed_dimension_and_title_only(plate_step, service_factory):
    service = service_factory(seeded=False)
    job_id, original, questions = _drawn(service, plate_step)
    original_json = service.http.get(original["files"]["json"], headers=AUTH).data
    assert json.loads(original_json)["annotations"] == []
    status, document = _finish(service, job_id, [_answer(_fit(questions))])
    assert document["annotation_base_version"] == 0
    annotations = document["annotations"]
    assert len(annotations) == 2
    assert any(a["before"] == "⌀20" and a["after"] == "⌀20H7" for a in annotations)
    assert sum(a["after"].startswith("一般公差") for a in annotations) == 1
    svg = ET.fromstring(service.http.get(status["files"]["svg"], headers=AUTH).data)
    for annotation in annotations:
        node = next(n for n in svg.findall("{*}text") if n.text == annotation["after"])
        x, y, width, height = annotation["box"]
        assert width > 0 and height > 0
        assert x <= float(node.get("x")) <= x + width
        assert y <= float(node.get("y")) <= y + height
    assert service.http.get(original["files"]["json"], headers=AUTH).data == original_json


def test_unknown_answers_do_not_invent_changed_regions(plate_step, service_factory):
    service = service_factory(seeded=False)
    job_id, original, questions = _drawn(service, plate_step)
    status, document = _finish(service, job_id, [_answer(q, None) for q in questions])
    assert document["annotations"] == []
    assert status["diff_from_previous"] == {"changed_texts": 0, "changed_positions": 0}
    assert service.http.get(status["files"]["svg"], headers=AUTH).data == service.http.get(
        original["files"]["svg"], headers=AUTH).data


def test_second_answer_markers_compare_the_direct_parent_and_survive_restart(bearing_seat_step, service_factory):
    service = service_factory(seeded=False)
    job_id, _original, questions = _drawn(service, bearing_seat_step)
    fits = [q for q in questions if q["kind"] == "special_tolerance"]
    first_status, first = _finish(service, job_id, [_answer(fits[0], "H7")])
    first_file = service.http.get(first_status["files"]["json"], headers=AUTH).data
    first_dimension = next(a["after"] for a in first["annotations"] if not a["after"].startswith("一般公差"))
    final_status, final = _finish(service, job_id, [_answer(fits[1], "H8")])
    assert final["annotation_base_version"] == 1
    assert len(final["annotations"]) == 2
    assert all(a["after"] != first_dimension for a in final["annotations"])
    assert service.http.get(first_status["files"]["json"], headers=AUTH).data == first_file
    service.queue.stop()
    restarted = service_factory(seeded=False, work=service.work)
    assert _doc(restarted, final_status) == final


def _annotations(tmp_path, before, after):
    a, b = tmp_path / "before.svg", tmp_path / "after.svg"
    a.write_text(before)
    b.write_text(after)
    compare = getattr(answer_module, "drawing_annotations", None)
    assert callable(compare), "缺少服务端引擎 SVG 文字变更定位"
    return compare(a, b)


def _svg(*texts):
    return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 420 297">' + "".join(texts) + '</svg>'


def test_reordering_svg_text_nodes_is_not_a_visible_change(tmp_path):
    a = '<text x="50" y="60" font-size="3.5" text-anchor="middle">20</text>'
    b = '<text x="80" y="90" font-size="3.5" text-anchor="middle">30</text>'
    assert _annotations(tmp_path, _svg(a, b), _svg(b, a)) == []


def test_moved_datum_and_removed_text_use_their_actual_anchors(tmp_path):
    old = '<text x="50" y="60" font-size="3.5" text-anchor="middle">A</text>'
    moved = '<text x="70" y="100" font-size="3.5" text-anchor="middle">A</text>'
    result = _annotations(tmp_path, _svg(old), _svg(moved))
    assert len(result) == 1 and result[0]["before"] == result[0]["after"] == "A"
    x, y, width, height = result[0]["box"]
    assert x <= 70 <= x + width and y <= 100 <= y + height
    removed = _annotations(tmp_path, _svg(old), _svg())
    assert len(removed) == 1 and removed[0]["kind"] == "removed"
    assert removed[0]["before"] == "A" and removed[0]["after"] is None


def test_changed_text_without_finite_engine_coordinates_is_not_guessed(tmp_path):
    invalid = '<text x="nan" y="60" font-size="3.5">20H7</text>'
    with pytest.raises(ValueError, match="坐标"):
        _annotations(tmp_path, _svg(), _svg(invalid))

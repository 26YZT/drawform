"""清单哈希相符，也不能把本来就缺图纸的目录当成可运行备份。"""

import json

import pytest

from drawform.service import maintenance
from tests.test_local_maintenance import JOB, content, work


def answered_work(work):
    index = work / "jobs.jsonl"
    row = json.loads(index.read_text().splitlines()[-1])
    name = "answers/1/drawing.json"
    path = work / JOB / name
    path.parent.mkdir(parents=True)
    path.write_text("{\"version\": 1}")
    row["answer_runs"] = [{"state": "drawn", "files": [["json", name]]}]
    with index.open("a") as handle:
        handle.write(json.dumps(row) + "\n")
    return path


def test_inspection_reports_a_missing_answer_output_without_changing_the_work(work):
    output = answered_work(work)
    output.unlink()
    before = content(work)
    report = maintenance.inspect(work)
    assert report["missing_files"] == [f"{JOB}/answers/1/drawing.json"]
    assert content(work) == before


@pytest.mark.parametrize("missing", ["model.step", "drawing.dxf", "answers/1/drawing.json"])
def test_incomplete_source_is_archived_but_cannot_be_restored_as_runnable(work, tmp_path, missing):
    answered_work(work)
    (work / JOB / missing).unlink()
    before = content(work)
    backup, restored = tmp_path / "backup", tmp_path / "restored"
    maintenance.backup(work, backup)
    assert content(backup / "data") == before
    with pytest.raises(ValueError, match="缺失"):
        maintenance.restore(backup, restored)
    assert not restored.exists()
    assert content(work) == before

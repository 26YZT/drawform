"""可解析 JSON 不等于合法审计；恢复不能把不可读审计悄悄带进服务。"""

import json

import pytest

from drawform.service import maintenance
from tests.test_local_maintenance import JOB, work


@pytest.mark.parametrize("bad", [
    {"job": JOB, "decision": "unknown"},
    {"job": JOB, "event": "answers", "state": "running", "request_key": "k"},
    {"job": JOB, "event": "answers", "state": "drawn"},
])
def test_invalid_audit_records_are_reported_and_not_restored_as_a_runnable_copy(work, tmp_path, bad):
    with (work / "reviews.jsonl").open("a") as handle:
        handle.write(json.dumps(bad) + "\n")
    assert maintenance.inspect(work)["audit_damaged_lines"] == [2]
    backup, restored = tmp_path / "forensic-backup", tmp_path / "restored"
    maintenance.backup(work, backup)
    assert (backup / "data/reviews.jsonl").read_bytes() == (work / "reviews.jsonl").read_bytes()
    with pytest.raises(ValueError, match="审计"):
        maintenance.restore(backup, restored)
    assert not restored.exists()

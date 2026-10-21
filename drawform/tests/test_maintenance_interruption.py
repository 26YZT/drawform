"""复制竞态与中断不能把半份恢复当成可启动工作目录。"""

import hashlib
import json

import pytest

from drawform.service import maintenance
from drawform.service.production import lock_work_directory
from tests.test_local_maintenance import work


def test_a_source_becoming_a_symlink_cannot_change_the_outside_file_permissions(tmp_path, monkeypatch):
    source, target = tmp_path / "source", tmp_path / "target"
    source.mkdir()
    target.mkdir()
    file = source / "file"
    file.write_bytes(b"original")
    outside = tmp_path / "outside"
    outside.write_bytes(b"outside")
    outside.chmod(0o644)
    real_copy = maintenance.shutil.copyfile

    def swap_then_copy(src, dst, **kwargs):
        file.unlink()
        file.symlink_to(outside)
        return real_copy(src, dst, **kwargs)

    monkeypatch.setattr(maintenance.shutil, "copyfile", swap_then_copy)
    entries = [{"path": "file", "bytes": 8, "sha256": hashlib.sha256(b"original").hexdigest()}]
    with pytest.raises(ValueError):
        maintenance._copy(source, target, entries)
    assert outside.stat().st_mode & 0o777 == 0o644
    assert outside.read_bytes() == b"outside"


def test_an_embedded_null_in_an_index_path_is_rejected_before_archiving(work, tmp_path):
    path = work / "jobs.jsonl"
    row = json.loads(path.read_text().splitlines()[-1])
    row["source"] = "bad\0name"
    path.write_text(json.dumps(row) + "\n")
    before = path.read_bytes()
    archive = tmp_path / "archive"
    with pytest.raises(ValueError):
        maintenance.compact(work, archive)
    assert path.read_bytes() == before and not archive.exists()


def test_answer_version_paths_cannot_escape_the_job_directory(work, tmp_path):
    path = work / "jobs.jsonl"
    row = json.loads(path.read_text().splitlines()[-1])
    row["answer_runs"] = [{"state": "drawn", "files": [["json", "../foreign.json"]]}]
    path.write_text(json.dumps(row) + "\n")
    before = path.read_bytes()
    with pytest.raises(ValueError):
        maintenance.compact(work, tmp_path / "archive")
    assert path.read_bytes() == before


def test_a_failed_restore_is_refused_by_the_production_work_directory_lock(work, tmp_path, monkeypatch):
    backup, restored = tmp_path / "backup", tmp_path / "restored"
    maintenance.backup(work, backup)
    real_copy = maintenance.shutil.copyfile
    copies = 0

    def interrupt_copy(src, dst, **kwargs):
        nonlocal copies
        copies += 1
        if copies == 2:
            raise OSError("模拟复制中断")
        return real_copy(src, dst, **kwargs)

    monkeypatch.setattr(maintenance.shutil, "copyfile", interrupt_copy)
    with pytest.raises(OSError, match="模拟复制中断"):
        maintenance.restore(backup, restored)
    assert restored.exists()
    with pytest.raises(RuntimeError, match="恢复"):
        with lock_work_directory(restored):
            pass


def test_a_verified_restore_can_acquire_the_production_lock(work, tmp_path):
    backup, restored = tmp_path / "backup", tmp_path / "restored"
    maintenance.backup(work, backup)
    maintenance.restore(backup, restored)
    with lock_work_directory(restored):
        assert (restored / "reviews.jsonl").read_bytes() == (work / "reviews.jsonl").read_bytes()

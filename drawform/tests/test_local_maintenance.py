"""离线维护不得丢版本或审计；只在临时工作目录验证。"""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from drawform.service.production import lock_work_directory

ROOT = Path(__file__).resolve().parents[1]
JOB = "a" * 12
STALE = "b" * 12


@pytest.fixture
def work(tmp_path):
    directory = tmp_path / "work"
    job = directory / JOB
    job.mkdir(parents=True)
    (job / "model.step").write_bytes(b"storage fixture; never sent to the renderer")
    (job / "drawing.dxf").write_bytes(b"original drawing bytes")
    (job / "amended-1.dxf").write_bytes(b"effective drawing bytes")
    row = {"job": JOB, "state": "drawn", "source": "model.step",
           "files": [["dxf", "drawing.dxf"], ["amended-1", "amended-1.dxf"]],
           "failed": [], "reasons": [], "error": "", "questions": [], "answer_runs": []}
    rows = [{**row, "state": "queued", "files": []}, {**row, "job": STALE}, row]
    (directory / "jobs.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    (directory / "reviews.jsonl").write_text(json.dumps({"job": JOB, "decision": "amended",
                                                          "reviewer": "storage test", "version": 1}) + "\n")
    return directory


def cli(*args):
    assert (ROOT / "src/drawform/service/maintenance.py").is_file(), "缺少可执行的本地维护命令"
    result = subprocess.run([sys.executable, "-m", "drawform.service.maintenance", *map(str, args)],
                            cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
                            text=True, capture_output=True)
    assert not result.stderr, result.stderr
    return result.returncode, json.loads(result.stdout)


def content(directory):
    return {str(p.relative_to(directory)): p.read_bytes() for p in directory.rglob("*")
            if p.is_file() and p.name != ".service.lock"}


def test_inspect_reports_index_growth_and_disk_without_changing_files(work):
    before = content(work)
    code, report = cli("inspect", "--work", work)
    assert code == 0
    assert report["index"] == {"rows": 3, "jobs": 2, "live_jobs": 1, "stale_jobs": 1, "damaged_lines": 0}
    assert report["disk"]["free"] > 0 and report["files"]["bytes"] > 0
    assert content(work) == before


def test_compaction_keeps_latest_live_row_and_archives_exact_original(work, tmp_path):
    before = content(work)
    archive = tmp_path / "original-index.jsonl"
    code, result = cli("compact", "--work", work, "--archive", archive)
    assert code == 0 and result["rows_before"] == 3 and result["rows_after"] == 1
    assert archive.read_bytes() == before["jobs.jsonl"]
    rows = [json.loads(line) for line in (work / "jobs.jsonl").read_text().splitlines()]
    assert len(rows) == 1 and rows[0]["job"] == JOB and rows[0]["state"] == "drawn"
    assert rows[0]["files"][-1] == ["amended-1", "amended-1.dxf"]
    assert {k: v for k, v in content(work).items() if k != "jobs.jsonl"} == {
        k: v for k, v in before.items() if k != "jobs.jsonl"}


def test_compaction_never_overwrites_an_existing_archive(work, tmp_path):
    archive = tmp_path / "archive.jsonl"
    archive.write_text("preserve me")
    before = content(work)
    code, result = cli("compact", "--work", work, "--archive", archive)
    assert code != 0 and "error" in result
    assert archive.read_text() == "preserve me" and content(work) == before


@pytest.mark.parametrize("bad", ["{broken", json.dumps({"job": "../outside", "state": "drawn"})])
def test_a_damaged_index_is_not_silently_compacted(work, tmp_path, bad):
    index = work / "jobs.jsonl"
    index.write_text(index.read_text() + bad + "\n")
    before = index.read_bytes()
    archive = tmp_path / "archive.jsonl"
    code, result = cli("compact", "--work", work, "--archive", archive)
    assert code != 0 and "error" in result
    assert index.read_bytes() == before and not archive.exists()


@pytest.mark.parametrize("command", ["compact", "backup"])
def test_maintenance_refuses_a_work_directory_owned_by_the_server(work, tmp_path, command):
    flag = "--archive" if command == "compact" else "--output"
    target = tmp_path / "target"
    with lock_work_directory(work):
        code, result = cli(command, "--work", work, flag, target)
    assert code != 0 and "工作目录" in result["error"]
    assert not target.exists()


def test_backup_and_restore_preserve_every_byte_and_reopen_versions_in_a_fresh_process(work, tmp_path):
    before = content(work)
    backup, restored = tmp_path / "backup", tmp_path / "restored"
    code, result = cli("backup", "--work", work, "--output", backup)
    assert code == 0 and result["files"] == len(before)
    assert all(p.stat().st_mode & 0o077 == 0 for p in backup.rglob("*") if p.is_file())
    code, _result = cli("restore", "--backup", backup, "--work", restored)
    assert code == 0 and content(restored) == before and content(work) == before
    command = "from drawform.service.jobs import JobQueue,head_of; import sys; q=JobQueue(sys.argv[1]); j=q.get(sys.argv[2]); assert j.state=='drawn'; assert head_of(j).read_bytes()==b'effective drawing bytes'"
    check = subprocess.run([sys.executable, "-c", command, str(restored), JOB], cwd=ROOT,
                           env={**os.environ, "PYTHONPATH": str(ROOT / "src")}, capture_output=True)
    assert check.returncode == 0, check.stderr


@pytest.mark.parametrize("damage", ["changed_file", "extra_file", "escape_manifest"])
def test_restore_rejects_tampered_or_escaping_backup_before_creating_the_target(work, tmp_path, damage):
    backup, restored = tmp_path / "backup", tmp_path / "restored"
    assert cli("backup", "--work", work, "--output", backup)[0] == 0
    if damage == "changed_file":
        (backup / "data" / JOB / "drawing.dxf").write_bytes(b"changed")
    elif damage == "extra_file":
        (backup / "data" / "extra.txt").write_text("not in manifest")
    else:
        path = backup / "manifest.json"
        manifest = json.loads(path.read_text())
        manifest["files"][0]["path"] = "../outside"
        path.write_text(json.dumps(manifest))
    code, result = cli("restore", "--backup", backup, "--work", restored)
    assert code != 0 and "error" in result
    assert not restored.exists()


def test_restore_never_overwrites_an_existing_work_directory(work, tmp_path):
    backup = tmp_path / "backup"
    assert cli("backup", "--work", work, "--output", backup)[0] == 0
    before = content(work)
    code, result = cli("restore", "--backup", backup, "--work", work)
    assert code != 0 and "error" in result and content(work) == before


@pytest.mark.parametrize("command", ["inspect", "backup"])
def test_inspection_and_backup_never_follow_symlinks_out_of_the_work_directory(work, tmp_path, command):
    outside = tmp_path / "outside"
    outside.write_text("private outside content")
    (work / "escape").symlink_to(outside)
    args = ["--output", tmp_path / "backup"] if command == "backup" else []
    code, result = cli(command, "--work", work, *args)
    assert code != 0 and "error" in result
    assert outside.read_text() == "private outside content"
    assert not (tmp_path / "backup").exists()


def test_backup_destination_cannot_be_inside_its_source(work):
    code, result = cli("backup", "--work", work, "--output", work / "recursive-backup")
    assert code != 0 and "error" in result
    assert not (work / "recursive-backup").exists()


def test_offline_compaction_does_not_claim_an_interrupted_task_was_drawn(work, tmp_path):
    row = json.loads((work / "jobs.jsonl").read_text().splitlines()[-1])
    row["state"] = "running"
    with (work / "jobs.jsonl").open("a") as handle:
        handle.write(json.dumps(row) + "\n")
    assert cli("compact", "--work", work, "--archive", tmp_path / "archive.jsonl")[0] == 0
    assert json.loads((work / "jobs.jsonl").read_text())["state"] == "running"

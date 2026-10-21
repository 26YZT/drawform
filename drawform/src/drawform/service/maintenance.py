"""本地磁盘检查、停服索引整理和校验式备份恢复；不启动服务、不删除审计。"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile

from drawform.service.production import INCOMPLETE_RESTORE, lock_work_directory
from drawform.review import DECISIONS

_JOB = re.compile(r"[0-9a-f]{12}")
_STATES = {"queued", "running", "drawn", "refused", "crashed", "waiting_reference"}
PILOT_LEDGER = ".drawform-pilot-ledger.json"


def _root(path):
    path = Path(path)
    if path.is_symlink() or not path.is_dir():
        raise ValueError("工作或备份目录必须是现有的真实目录，不能是符号链接")
    return path.resolve()


def _name(value):
    if (not isinstance(value, str) or not value or "\\" in value or "\0" in value
            or Path(value).is_absolute() or any(part in ("", ".", "..") for part in value.split("/"))):
        raise ValueError("文件路径必须是目录内的相对路径，不能越界")
    return value


def _files(root):
    files = []
    for path in sorted(root.rglob("*")):
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode) or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise ValueError(f"目录含符号链接或特殊文件，不能读取：{path.relative_to(root)}")
        if stat.S_ISREG(mode):
            _name(path.relative_to(root).as_posix())
            files.append(path)
    return files


def _read(path):
    return path.read_bytes() if path.is_file() else b""


def _index_rows(raw):
    rows, damaged = [], []
    for number, line in enumerate(raw.splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            if (not isinstance(row, dict) or not isinstance(row.get("job"), str)
                    or not _JOB.fullmatch(row["job"]) or row.get("state") not in _STATES
                    or not isinstance(row.get("files"), list)):
                raise ValueError
            _name(row.get("source"))
            if row.get('sheet', 'A3') not in ('A3', 'A2'):
                raise ValueError
            runs = row.get("answer_runs", [])
            if not isinstance(runs, list):
                raise ValueError
            references = row.get('reference_runs',[])
            if not isinstance(references,list) or any(not isinstance(r,dict)
                    or r.get('state') not in _STATES or not isinstance(r.get('key'),str)
                    or not isinstance(r.get('submission'),dict) for r in references):
                raise ValueError
            if row['state']=='waiting_reference' and (not isinstance(row.get('reference_snapshot'),dict)
                    or not isinstance(row['reference_snapshot'].get('questions'),list)
                    or not row['reference_snapshot']['questions']):
                raise ValueError
            for record in [row, *runs]:
                if (not isinstance(record, dict) or record.get("state") not in _STATES
                        or not isinstance(record.get("files"), list)):
                    raise ValueError
                for entry in record["files"]:
                    if (not isinstance(entry, list) or len(entry) != 2
                            or not isinstance(entry[0], str) or not entry[0]):
                        raise ValueError
                    _name(entry[1])
            rows.append((line, row))
        except (ValueError, TypeError, UnicodeError):
            damaged.append(number)
    return rows, damaged


def _audit_damage(raw):
    damaged = []
    for number, line in enumerate(raw.splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            if not isinstance(row, dict) or not isinstance(row.get("job"), str) or not _JOB.fullmatch(row["job"]):
                raise ValueError
            # 与 ReviewLog 的读取语义一致：答案操作不是复核结论。
            if row.get("event") == "answers":
                if not row.get("request_key") or row.get("state") not in ("queued", "drawn", "refused", "crashed"):
                    raise ValueError
            elif row.get("decision") not in DECISIONS:
                raise ValueError
        except (ValueError, TypeError, UnicodeError):
            damaged.append(number)
    return damaged


def _missing_files(root, rows):
    latest = {row["job"]: row for _line, row in rows}
    missing = set()
    for job, row in latest.items():
        directory = root / job
        if not directory.is_dir():
            continue  # 已按保留策略清理的任务目录，不作为可运行任务恢复。
        names = {row["source"]}
        for record in [row, *row.get("answer_runs", [])]:
            names.update(entry[1] for entry in record["files"])
        missing.update(f"{job}/{name}" for name in names if not (directory / name).is_file())
    return sorted(missing)


def _destination(path, source):
    path = Path(path)
    if path.exists() or path.is_symlink():
        raise ValueError("目标已存在，不能覆盖；请指定新的本地路径")
    if not path.parent.is_dir():
        raise ValueError("目标的上级目录不存在，请先创建上级目录")
    path = path.resolve()
    if path.is_relative_to(source):
        raise ValueError("目标不能位于来源目录内部")
    return path


def inspect(work):
    work = _root(work)
    files = _files(work)
    rows, damaged = _index_rows(_read(work / "jobs.jsonl"))
    latest = {row["job"]: row for _line, row in rows}
    live = {job: row for job, row in latest.items() if (work / job).is_dir()}
    return {"work": str(work), "observed_at": datetime.now(timezone.utc).isoformat(),
            "restore_incomplete": (work / INCOMPLETE_RESTORE).exists(),
            "index": {"rows": len(rows), "jobs": len(latest), "live_jobs": len(live),
                      "stale_jobs": len(latest) - len(live), "damaged_lines": len(damaged)},
            "index_damaged_lines": damaged,
            "audit_damaged_lines": _audit_damage(_read(work / "reviews.jsonl")),
            "missing_files": _missing_files(work, rows),
            "files": {"count": len(files), "bytes": sum(path.stat().st_size for path in files)},
            "disk": dict(shutil.disk_usage(work)._asdict())}


def compact(work, archive):
    work = _root(work)
    _files(work)  # 先拒绝链接，不能让工作锁跟随到目录外。
    archive = _destination(archive, work)
    with lock_work_directory(work):
        index = work / "jobs.jsonl"
        original = index.read_bytes()
        rows, damaged = _index_rows(original)
        if damaged:
            raise ValueError(f"任务索引存在损坏或非法行 {damaged}，未整理，请先核对原始索引")
        latest = {row["job"]: (line, row) for line, row in rows}
        retained = [line for job, (line, _row) in latest.items() if (work / job).is_dir()]
        replacement = b"".join(line + b"\n" for line in retained)
        with archive.open("xb") as handle:
            archive.chmod(0o600)
            handle.write(original)
            handle.flush()
            os.fsync(handle.fileno())
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=work, prefix=".jobs-", delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(replacement)
                handle.flush()
                os.fsync(handle.fileno())
            if index.read_bytes() != original:
                raise ValueError("整理期间任务索引被其他程序改动，未替换；请停止全部服务后重试")
            os.replace(temporary, index)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()  # 只清理本次创建的临时文件。
    return {"work": str(work), "archive": str(archive), "rows_before": len(rows),
            "rows_after": len(retained), "bytes_before": len(original), "bytes_after": len(replacement)}


def _digest(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _sync_tree(root):
    directories = [path for path in root.rglob("*") if path.is_dir()]
    for path in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        _sync_directory(path)
    _sync_directory(root)


def _validate_workbench(work):
    from drawform.workbench.recovery import validate_workspace
    return validate_workspace(work)


def _snapshot(work):
    return [{"path": path.relative_to(work).as_posix(), "bytes": path.stat().st_size,
             "sha256": _digest(path)} for path in _files(work) if path != work / ".service.lock"]


def _copy(source, target, entries):
    for entry in entries:
        path = target / entry["path"]
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        shutil.copyfile(source / entry["path"], path, follow_symlinks=False)
        if path.is_symlink():
            raise ValueError("复制期间来源变为符号链接，未修改链接目标")
        path.chmod(0o600)
        if _digest(path) != entry["sha256"]:
            raise ValueError("复制期间来源文件发生变化，副本校验未通过")
        with path.open("rb") as handle:
            os.fsync(handle.fileno())


def _validate_ledger(book_path, work):
    from drawform.eval.pilot import _book, _file, _jobs, _sha
    book, jobs = _book(book_path), _jobs(work)
    for case in book["cases"]:
        job = jobs.get(case["job_id"])
        if job is None:
            raise ValueError("首批台账登记的任务在备份索引中缺失")
        source = _file(work, job["job"], job["source"])
        if not source.is_file() or _sha(source) != case["step_sha256"]:
            raise ValueError("台账登记的 STEP 证据缺失或指纹不符")


def backup(work, output, *, book=None):
    work = _root(work)
    _files(work)
    output = _destination(output, work)
    with lock_work_directory(work):
        workbench = _validate_workbench(work)
        entries = _snapshot(work)
        ledger_bytes = None
        if book is not None:
            book = Path(book)
            if book.is_symlink():
                raise ValueError("台账不能是符号链接")
            ledger_bytes = book.read_bytes()
            _validate_ledger(book, work)
            existing = work / PILOT_LEDGER
            if existing.exists() and existing.read_bytes() != ledger_bytes:
                raise ValueError("工作目录中已有另一份归档台账，不能覆盖")
        output.mkdir(mode=0o700)
        data = output / "data"
        data.mkdir(mode=0o700)
        _copy(work, data, entries)
        if _snapshot(work) != entries:
            raise ValueError("备份期间来源发生变化；本目录无有效清单，不能用于恢复")
        if ledger_bytes is not None:
            if book.read_bytes() != ledger_bytes:
                raise ValueError("备份期间台账发生变化，本目录不能作为完整备份")
            ledger = data / PILOT_LEDGER
            if not ledger.exists():
                with ledger.open('xb') as handle:
                    ledger.chmod(0o600)
                    handle.write(ledger_bytes)
                    handle.flush()
                    os.fsync(handle.fileno())
                entries.append({"path": PILOT_LEDGER, "bytes": len(ledger_bytes), "sha256": _digest(ledger)})
            _validate_ledger(ledger, data)
        manifest = {"format": 1, "created_at": datetime.now(timezone.utc).isoformat(),
                    "source": str(work), "files": entries, "workbench": workbench}
        if ledger_bytes is not None:
            manifest["pilot_ledger"] = PILOT_LEDGER
        with (output / "manifest.json").open("x", encoding="utf-8") as handle:
            (output / "manifest.json").chmod(0o600)
            json.dump(manifest, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        _sync_tree(output)
        _sync_directory(output.parent)
    return {"backup": str(output), "files": len(entries), "bytes": sum(e["bytes"] for e in entries)}


def _verified_backup(backup_path):
    backup_path = _root(backup_path)
    _files(backup_path)
    manifest = json.loads((backup_path / "manifest.json").read_text(encoding="utf-8"))
    if (not isinstance(manifest, dict) or type(manifest.get("format")) is not int
            or manifest["format"] != 1 or not isinstance(manifest.get("files"), list)):
        raise ValueError("备份清单无效，不能恢复")
    entries = manifest["files"]
    names = []
    for entry in entries:
        if (not isinstance(entry, dict) or type(entry.get("bytes")) is not int or entry["bytes"] < 0
                or not isinstance(entry.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])):
            raise ValueError("备份文件清单字段无效")
        names.append(_name(entry.get("path")))
    if len(names) != len(set(names)) or ".service.lock" in names:
        raise ValueError("备份清单含重复路径或运行锁")
    data = _root(backup_path / "data")
    actual = {path.relative_to(data).as_posix(): path for path in _files(data)}
    if set(actual) != set(names):
        raise ValueError("备份文件与清单不一致，不能恢复")
    for entry in entries:
        path = actual[entry["path"]]
        if path.stat().st_size != entry["bytes"] or _digest(path) != entry["sha256"]:
            raise ValueError(f"备份文件校验失败：{entry['path']}")
    _rows, damaged = _index_rows(_read(data / "jobs.jsonl"))
    if damaged or _audit_damage(_read(data / "reviews.jsonl")):
        raise ValueError("备份内的任务索引或审计有损坏，不能作为可运行副本恢复，请先人工核对")
    missing = _missing_files(data, _rows)
    if missing:
        raise ValueError(f"备份内的任务来源或图纸文件缺失，不能作为可运行副本恢复：{missing}")
    if "pilot_ledger" in manifest:
        if manifest["pilot_ledger"] != PILOT_LEDGER or PILOT_LEDGER not in names:
            raise ValueError("备份台账清单不完整")
        _validate_ledger(data / PILOT_LEDGER, data)
    _validate_workbench(data)
    return backup_path, data, entries


def verify_backup(backup_path):
    backup_path, _data, entries = _verified_backup(backup_path)
    return {"backup": str(backup_path), "verified": True, "files": len(entries)}


def restore(backup_path, work):
    backup_path, data, entries = _verified_backup(backup_path)
    work = _destination(work, backup_path)
    work.mkdir(mode=0o700)
    with lock_work_directory(work):
        marker = work / INCOMPLETE_RESTORE
        with marker.open("x", encoding="utf-8") as handle:
            marker.chmod(0o600)
            json.dump({"backup": str(backup_path), "started_at": datetime.now(timezone.utc).isoformat()}, handle)
            handle.flush()
            os.fsync(handle.fileno())
        _sync_directory(work)
        _sync_directory(work.parent)
        _copy(data, work, entries)
        _validate_workbench(work)
        _sync_tree(work)
        marker.unlink()  # 只在所有文件核验成功后移除本次恢复标记。
        _sync_directory(work)
        _sync_directory(work.parent)
    return {"work": str(work), "files": len(entries), "bytes": sum(e["bytes"] for e in entries)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("inspect", "compact", "backup", "restore"):
        command = commands.add_parser(name)
        command.add_argument("--work", required=True, type=Path)
        if name == "compact":
            command.add_argument("--archive", required=True, type=Path)
        elif name == "backup":
            command.add_argument("--output", required=True, type=Path)
        elif name == "restore":
            command.add_argument("--backup", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "inspect":
            result = inspect(args.work)
        elif args.command == "compact":
            result = compact(args.work, args.archive)
        elif args.command == "backup":
            result = backup(args.work, args.output)
        else:
            result = restore(args.backup, args.work)
    except (OSError, ValueError, RuntimeError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

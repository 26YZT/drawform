"""试点留存清单：只标记人工复核时间，不删除、移动或截断任何数据。"""

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from drawform.eval.pilot import _book, _save, _text, _jobs
from drawform.service.policy import EVIDENCE_DAYS, LOG_DAYS, DAILY_BACKUPS, WEEKLY_BACKUPS


def instant(value):
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError("时间须包含时区")
    return result.astimezone(timezone.utc)


def close_trial(book_path, ended_at, operator, *, now=None):
    book = _book(book_path)
    end = instant(ended_at)
    now = now or datetime.now(timezone.utc)
    if not instant(book["created_at"]) <= end <= now:
        raise ValueError("结束时间须在建账后且不晚于当前时间")
    if "closed_at" in book:
        raise ValueError("试点结束记录已存在，不能覆盖")
    book.update(closed_at=end.isoformat(), closed_by=_text(operator, "结束登记者"))
    _save(book_path, book)
    return {"closed_at": book["closed_at"], "review_after": (end + timedelta(days=EVIDENCE_DAYS)).isoformat(),
            "meaning": "试点结束登记，不是制图合格签字；到期仍须人工确认"}


def evidence_plan(book_path, work, *, now=None):
    book = _book(book_path)
    work = Path(work).resolve()
    jobs = _jobs(work)
    now = now or datetime.now(timezone.utc)
    deadline = instant(book["closed_at"]) + timedelta(days=EVIDENCE_DAYS) if book.get("closed_at") else None
    protected = {case["job_id"] for case in book["cases"]}
    missing = sorted(job for job in protected if job not in jobs or not (work / job).is_dir())
    return {"action": "report_only", "evidence_days_after_trial": EVIDENCE_DAYS,
            "first_cohort": [case["job_id"] for case in book["cases"][:30]],
            "protected_jobs": sorted(protected), "missing_evidence": missing,
            "review_after": deadline.isoformat() if deadline else None,
            "requires_manual_review": sorted(protected) if deadline and now >= deadline else [],
            "unregistered_jobs_held": sorted(set(jobs) - protected),
            "shared_records_held": ["jobs.jsonl", "reviews.jsonl", str(Path(book_path).resolve())],
            "deletion_authorized": False}


def backup_plan(root):
    """目录约定 daily/weekly/before-change；完整试点归档不进入轮转清单。"""
    root = Path(root).resolve()
    result = {"action": "report_only", "keep": [], "requires_manual_review": [], "invalid": []}
    for kind, limit in (("daily", DAILY_BACKUPS), ("weekly", WEEKLY_BACKUPS)):
        records = []
        for path in sorted((root / kind).glob("*")):
            try:
                if path.is_symlink() or not path.is_dir():
                    raise ValueError("非真实备份目录")
                manifest = json.loads((path / "manifest.json").read_text())
                records.append((instant(manifest["created_at"]), str(path)))
            except (OSError, ValueError, KeyError, TypeError) as error:
                result["invalid"].append({"path": str(path), "error": str(error)})
        ordered = [path for _at, path in sorted(records, reverse=True)]
        result["keep"].extend(ordered[:limit])
        result["requires_manual_review"].extend(ordered[limit:])
    result["held_outside_rotation"] = [str(root / name) for name in ("before-change", "archive")]
    return result


def log_plan(folder, work, *, now=None):
    folder, work = Path(folder).resolve(), Path(work).resolve()
    if folder.is_relative_to(work) or work.is_relative_to(folder):
        raise ValueError("普通日志目录必须与业务工作目录分开，不能清理业务审计")
    now = now or datetime.now(timezone.utc)
    cutoff = now.timestamp() - LOG_DAYS * 86400
    files = sorted(str(p) for p in folder.glob("*.log")
                   if p.is_file() and not p.is_symlink() and p.stat().st_mtime <= cutoff)
    return {"action": "report_only", "log_days": LOG_DAYS, "requires_manual_review": files}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    end = commands.add_parser("close")
    end.add_argument("--book", required=True)
    end.add_argument("--ended-at", required=True)
    end.add_argument("--operator", required=True)
    plan = commands.add_parser("evidence")
    plan.add_argument("--book", required=True)
    plan.add_argument("--work", required=True)
    copies = commands.add_parser("backups")
    copies.add_argument("--root", required=True)
    logs = commands.add_parser("logs")
    logs.add_argument("--folder", required=True)
    logs.add_argument("--work", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "close":
            result = close_trial(args.book, args.ended_at, args.operator)
        elif args.command == "evidence":
            result = evidence_plan(args.book, args.work)
        elif args.command == "backups":
            result = backup_plan(args.root)
        else:
            result = log_plan(args.folder, args.work)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

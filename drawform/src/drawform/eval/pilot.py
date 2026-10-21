"""本地试点台账与只读统计；审图结论和候选选择引用既有服务审计。"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile

from drawform.review import DECISIONS, REASONS
from drawform.service.policy import MIN_OBSERVATIONS

# 2026-10-14 用户确认：项目统计入口以 5 秒观察秒选首项，包含边界。
DEFAULT_RAPID_SECONDS = 5.0


class PilotError(ValueError):
    pass


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise PilotError(f"{name}不能为空")
    return value.strip()


def _seconds(value, name, minimum=0):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < minimum:
        raise PilotError(f"{name}须为不小于 {minimum} 的有限数")
    return value


def create_book(path, *, title, source, retention_reference=""):
    if source not in ("rehearsal", "real"):
        raise PilotError("source 只能为 rehearsal 或 real")
    if source == "real":
        retention_reference = _text(retention_reference, "真实件留存确认依据")
    book = {"title": _text(title, "批次名称"), "source": source,
            "retention_reference": retention_reference, "created_at": _now(), "cases": []}
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(book, handle, ensure_ascii=False, indent=2)
    return book


def _book(path):
    try:
        book = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise PilotError(f"台账读不了：{error}") from error
    if not isinstance(book, dict) or book.get("source") not in ("rehearsal", "real") or not isinstance(book.get("cases"), list):
        raise PilotError("台账缺少合法 source 或 cases")
    if book["source"] == "real" and not book.get("retention_reference"):
        raise PilotError("真实件台账缺少留存确认依据")
    for key in ("job_id", "part", "step_sha256"):
        values = [case.get(key) for case in book["cases"]]
        if any(not isinstance(value, str) or not value for value in values) or len(set(values)) != len(values):
            raise PilotError(f"台账 {key} 缺失或重复，不能据此计数")
    return book


def _save(path, book):
    path = Path(path)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(book, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    Path(handle.name).replace(path)


def _rows(path):
    path = Path(path)
    if not path.exists():
        return []
    result = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError as error:
            raise PilotError(f"{path.name} 第 {number} 行损坏，停止统计") from error
        if not isinstance(row, dict):
            raise PilotError(f"{path.name} 第 {number} 行不是记录对象")
        result.append(row)
    return result


def _jobs(work):
    result = {}
    for row in _rows(Path(work) / "jobs.jsonl"):
        if not isinstance(row.get("job"), str) or not re.fullmatch(r"[0-9a-f]{12}", row["job"]):
            raise PilotError("jobs.jsonl 中出现非法任务 ID")
        result[row["job"]] = row
    return result


def _job(jobs, job_id):
    if job_id not in jobs:
        raise PilotError(f"任务 {job_id} 不在当前 jobs.jsonl 中；不能静默漏算")
    return jobs[job_id]


def _file(work, job_id, name):
    root = Path(work).resolve()
    folder = (root / job_id).resolve()
    path = (folder / name).resolve()
    if folder.parent != root or not path.is_relative_to(folder) or path == folder:
        raise PilotError(f"任务文件路径越界：{job_id}")
    return path


def _questions(job):
    catalog = {}
    groups = [run.get("questions", []) for run in job.get("answer_runs", [])] + [job.get("questions", [])]
    for group in groups:
        for question in group:
            qid = question.get("id")
            options = question.get("options")
            if not qid or not options or len({o["id"] for o in options}) != len(options):
                raise PilotError("问题清单缺少 ID 或封闭候选，不能统计")
            catalog[qid] = question
    return catalog


def register_case(path, work, job_id, part):
    book, jobs = _book(path), _jobs(work)
    job = _job(jobs, job_id)
    part = _text(part, "零件标识")
    source = _file(work, job_id, job["source"])
    if not source.is_file():
        raise PilotError("STEP 源文件不存在，不能登记")
    digest = _sha(source)
    for case in book["cases"]:
        if case["job_id"] == job_id and case["part"] == part and case["step_sha256"] == digest:
            return case
        if case["part"] == part or case["job_id"] == job_id:
            raise PilotError("零件标识或任务已登记；后续操作不能另算一件")
        if case["step_sha256"] == digest:
            raise PilotError("相同 STEP 已登记，不能把重试当成另一件")
    case = {"part": part, "job_id": job_id, "step_sha256": digest, "registered_at": _now(), "observations": []}
    book["cases"].append(case)
    _save(path, book)
    return case


def record_observation(path, work, job_id, question_id, *, elapsed_seconds, first_option_id, observer):
    elapsed_seconds = _seconds(elapsed_seconds, "实测耗时")
    observer = _text(observer, "观察人")
    book = _book(path)
    case = next((case for case in book["cases"] if case["job_id"] == job_id), None)
    if case is None:
        raise PilotError("请先登记零件")
    question = _questions(_job(_jobs(work), job_id)).get(question_id)
    if question is None or first_option_id not in {option["id"] for option in question["options"]}:
        raise PilotError("观察记录引用的问题或首项不在引擎候选中")
    if any(row["question_id"] == question_id for row in case["observations"]):
        raise PilotError("该问题的耗时已记录，不能静默覆盖")
    observation = {"question_id": question_id, "elapsed_seconds": elapsed_seconds,
                   "first_option_id": first_option_id, "observer": observer, "recorded_at": _now()}
    case["observations"].append(observation)
    _save(path, book)
    return observation


def _answers(rows, catalog):
    answers, seen = {}, {}
    for row in rows:
        if row.get("event") != "answers" or row.get("state") != "drawn":
            continue
        key = row.get("request_key")
        if not key or not isinstance(row.get("answers"), list):
            raise PilotError("答案审计缺少 request_key 或 answers")
        if key in seen:
            if seen[key] != row["answers"]:
                raise PilotError("同一请求出现矛盾的答案审计")
            continue
        seen[key] = row["answers"]
        for answer in row["answers"]:
            qid = answer.get("question_id")
            if qid not in catalog or "chosen_option_id" not in answer:
                raise PilotError("答案审计与问题清单不匹配")
            chosen = answer["chosen_option_id"]
            if chosen is not None and chosen not in {option["id"] for option in catalog[qid]["options"]}:
                raise PilotError("答案审计引用了候选之外的选项")
            if qid in answers and answers[qid] != chosen:
                raise PilotError("同一问题有相互矛盾的选择")
            answers[qid] = chosen
    return answers


def _changes(work, job):
    from drawform.eval.amend_diff import compare
    if job.get("state") != "drawn":
        return None, None, [], [f"任务状态 {job.get('state')}，没有可比图纸"]
    files = dict(job.get("files", []))
    if "dxf" not in files:
        return None, None, [], ["缺少机器底稿 DXF"]
    numbered = sorted((int(kind[8:]), name) for kind, name in files.items() if re.fullmatch(r"amended-\d+", kind))
    if [n for n, name in numbered] != list(range(1, len(numbered) + 1)):
        return None, None, [], ["生效版本链有缺口"]
    paths = [_file(work, job["job"], name) for name in [files["dxf"], *(name for n, name in numbered)]]
    missing = [f"缺少图纸 {path.name}" for path in paths if not path.is_file()]
    if missing:
        return None, None, [], missing
    details = []
    try:
        final = sum(compare(paths[0], paths[-1]).as_dict().values())
        for version, (parent, current) in enumerate(zip(paths, paths[1:]), 1):
            diff = compare(parent, current)
            details.append({"from_version": version - 1, "to_version": version,
                            "count": sum(diff.as_dict().values()), "actions": diff.as_dict(), "items": list(diff.items)})
    except Exception as error:
        return None, None, [], [f"DXF 差异无法核验：{error}"]
    return final, sum(row["count"] for row in details), details, []


def _ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def _head(work, job):
    files = dict(job.get("files", []))
    versions = sorted((int(k[8:]), v) for k, v in files.items() if re.fullmatch(r"amended-\d+", k))
    name = versions[-1][1] if versions else files.get("dxf")
    if not name or not _file(work, job["job"], name).is_file():
        raise PilotError("没有可核验的当前图纸，不能登记人工纠错")
    return _sha(_file(work, job["job"], name)), {n for n, _name in versions}


def record_corrections(path, work, job_id, record):
    """只保存人的审图计数依据；文字/标识均不回写几何或制造数值。"""
    book = _book(path)
    case = next((c for c in book["cases"] if c["job_id"] == job_id), None)
    if case is None:
        raise PilotError("请先登记零件")
    if (not isinstance(record, dict) or set(record) != {"reviewer", "complete", "answer_changes_excluded", "items"}
            or record["complete"] is not True or record["answer_changes_excluded"] is not True
            or not isinstance(record["items"], list)):
        raise PilotError("须明确确认记录完整、已排除正常答题变更，并提供 items 数组")
    reviewer = _text(record["reviewer"], "纠错核对人")
    job = _job(_jobs(work), job_id)
    head, versions = _head(work, job)
    engine_versions = {run.get("version") for run in job.get("answer_runs", []) if run.get("state") == "drawn"}
    objects = set()
    for item in record["items"]:
        if not isinstance(item, dict) or set(item) != {"object_id", "net_changed", "versions", "reason", "evidence"}:
            raise PilotError("每处须有 object_id、net_changed、versions、reason、evidence")
        identity = _text(item["object_id"], "改动对象")
        if identity in objects or type(item["net_changed"]) is not bool:
            raise PilotError("同一对象只能一条记录，net_changed 须为布尔值")
        objects.add(identity)
        steps = item["versions"]
        if (not isinstance(steps, list) or not steps or any(type(v) is not int for v in steps)
                or len(set(steps)) != len(steps) or not set(steps) <= versions - engine_versions):
            raise PilotError("修改轮次须引用存在的人工回传版本，不能把答案版本计为人工纠错")
        _text(item["reason"], "修改原因")
        _text(item["evidence"], "核对依据")
    saved = {**record, "reviewer": reviewer, "head_sha256": head, "recorded_at": _now()}
    case.setdefault("correction_reviews", []).append(saved)
    _save(path, book)
    return saved


def report(path, work, *, rapid_seconds=None):
    if rapid_seconds is not None:
        _seconds(rapid_seconds, "秒选阈值", minimum=0.000001)
    book, work = _book(path), Path(work)
    jobs, audit = _jobs(work), _rows(work / "reviews.jsonl")
    if any(not isinstance(row.get("job"), str) or not re.fullmatch(r"[0-9a-f]{12}", row["job"]) for row in audit):
        raise PilotError("审计中出现缺失或非法任务 ID，不能静默漏算")
    decisions, not_reviewed, reasons = Counter(), Counter(), Counter()
    details, final_counts, adjacent_counts = [], [], []
    manual_counts, manual_cumulative, missing_manual = [], [], []
    groups = {}
    total = selected = unknown = unanswered = measured = rapid_count = 0
    for case in book["cases"]:
        job = _job(jobs, case["job_id"])
        source = _file(work, job["job"], job["source"])
        if not source.is_file() or _sha(source) != case["step_sha256"]:
            raise PilotError(f"任务 {job['job']} 的 STEP 指纹无法核验或已变化")
        rows = [row for row in audit if row.get("job") == job["job"]]
        reviews = [row for row in rows if row.get("event") != "answers"]
        for row in reviews:
            if row.get("decision") not in DECISIONS or not row.get("reviewer") or set(row.get("reasons", [])) - REASONS.keys():
                raise PilotError("审图记录缺少合法结论、审核人或理由")
        # 带 effective_sha 的 amended 是回传操作；明确采用另由 /review 留痕。
        conclusions = [row for row in reviews if not (row["decision"] == "amended" and row.get("effective_sha"))]
        first = conclusions[0] if conclusions else None
        counted = None
        if first:
            counted = first["decision"]
            if counted == "accepted" and any(row.get("effective_sha") and row.get("draft_sha") != row["effective_sha"]
                                              for row in reviews[:reviews.index(first)]):
                counted = "amended"
            decisions[counted] += 1
            if first["decision"] == "rejected":
                reasons.update(set(first.get("reasons", [])))
        else:
            not_reviewed[job.get("state", "unknown")] += 1
        catalog = _questions(job)
        answers = _answers(rows, catalog)
        authors = {a["question_id"]: row["reviewer"] for row in rows
                   if row.get("event") == "answers" and row.get("state") == "drawn"
                   for a in row["answers"]}
        observations = {row["question_id"]: row for row in case["observations"]}
        if len(observations) != len(case["observations"]) or observations.keys() - catalog.keys():
            raise PilotError("观察记录重复或引用了不存在的问题")
        total += len(catalog)
        for qid in catalog:
            if qid not in answers:
                unanswered += 1
                continue
            chosen = answers[qid]
            group = groups.setdefault(authors[qid], {"answered": 0, "unknown": 0, "eligible_measured": 0, "rapid": 0})
            group["answered"] += 1
            group["unknown"] += chosen is None
            if chosen is None:
                unknown += 1
            else:
                selected += 1
            observation = observations.get(qid)
            if observation:
                _seconds(observation["elapsed_seconds"], "实测耗时")
                if observation["first_option_id"] not in {option["id"] for option in catalog[qid]["options"]}:
                    raise PilotError("观察记录的首项不在引擎候选中")
                measured += 1
                if len(catalog[qid]["options"]) >= 2 and rapid_seconds is not None and chosen == observation["first_option_id"] and observation["elapsed_seconds"] <= rapid_seconds:
                    rapid_count += 1
                if len(catalog[qid]["options"]) >= 2:
                    group["eligible_measured"] += 1
                    group["rapid"] += (rapid_seconds is not None and chosen == observation["first_option_id"]
                                       and observation["elapsed_seconds"] <= rapid_seconds)
        final, cumulative, versions, missing = _changes(work, job)
        if final is not None:
            final_counts.append(final)
            adjacent_counts.append(cumulative)
        correction = case.get("correction_reviews", [])[-1:]
        if correction and not missing and first and correction[0]["head_sha256"] == _head(work, job)[0]:
            manual_counts.append(sum(item["net_changed"] for item in correction[0]["items"]))
            manual_cumulative.append(sum(len(item["versions"]) for item in correction[0]["items"]))
        else:
            missing_manual.append(case["part"])
        details.append({"part": case["part"], "job_id": job["job"], "state": job["state"],
                        "first_review": first, "counted_decision": counted, "review_events": reviews, "questions": len(catalog),
                        "answers": answers, "observations": case["observations"],
                        "version_changes": versions, "missing_evidence": missing})
    reviewed, answered = sum(decisions.values()), selected + unknown
    alerts = []
    for group in groups.values():
        group["alerts"] = []
        if group["answered"] >= MIN_OBSERVATIONS and group["unknown"] == group["answered"]:
            group["alerts"].append("all_unknown")
        if group["eligible_measured"] >= MIN_OBSERVATIONS and group["rapid"] == group["eligible_measured"]:
            group["alerts"].append("all_rapid_first")
        alerts.extend(group["alerts"])
    assessment = "threshold_not_set" if rapid_seconds is None else (
        "missing_observations" if measured < answered else "measured" if answered else "no_answers")
    source = book["source"]
    return {
        "title": book["title"], "source": source, "registered": len(details),
        "qualification": ("⚠️ 本节全部指标基于机器生成语料，存在同源偏差，不代表真实件表现。\n"
                          "⚠️ 真实件表现以「一次通过率」为准，见 §三。"
                          if source == "rehearsal" else "仅统计本台账登记的真实件，来源与审核人由登记者声明；未审核任务单列。"),
        "baseline": "not_a_real_parts_baseline" if source == "rehearsal" else "awaiting_human_signoff",
        "counting_basis": "2026-10-14 A方案：每件首次明确审图结论；回传不等于采用；未审/未出图单列。",
        "first_pass": {**{decision: decisions[decision] for decision in DECISIONS}, "denominator": reviewed,
                       "rate": _ratio(decisions["accepted"], reviewed)},
        "end_to_end_direct": {"numerator": decisions["accepted"], "denominator": len(details),
                              "rate": _ratio(decisions["accepted"], len(details))},
        "not_reviewed": dict(not_reviewed), "rejection_reasons": dict(reasons),
        "rejection_reason_rates": {key: _ratio(value, decisions["rejected"]) for key, value in reasons.items()},
        "reason_labels": {reason: REASONS[reason] for reason in reasons},
        "changes": {"selected_basis": "human_verified_net", "scope": "主指标为人工核对净处数；from_draft/adjacent_sum 为机器图面差异诊断，包含答案重出图。",
                    "manual_net": {"total": sum(manual_counts), "denominator": len(manual_counts), "rate": _ratio(sum(manual_counts), len(manual_counts))},
                    "manual_adjacent_sum": {"total": sum(manual_cumulative), "denominator": len(manual_cumulative), "rate": _ratio(sum(manual_cumulative), len(manual_cumulative))},
                    "missing_manual_evidence": missing_manual,
                    "from_draft": {"total": sum(final_counts), "denominator": len(final_counts), "rate": _ratio(sum(final_counts), len(final_counts))},
                    "adjacent_sum": {"total": sum(adjacent_counts), "denominator": len(adjacent_counts), "rate": _ratio(sum(adjacent_counts), len(adjacent_counts))}},
        "questions": {"total": total, "selected": selected, "unknown": unknown, "unanswered": unanswered,
                      "selected_rate": _ratio(selected, total), "unknown_rate": _ratio(unknown, total),
                      "unanswered_rate": _ratio(unanswered, total)},
        "rapid_first": {"threshold_seconds": rapid_seconds, "measured": measured, "missing": answered - measured,
                        "count": rapid_count if rapid_seconds is not None else None, "assessment": assessment},
        "alerts": sorted(set(alerts)), "observation_groups": groups, "minimum_observations": MIN_OBSERVATIONS, "cases": details,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    initialize = commands.add_parser("init")
    initialize.add_argument("--book", required=True, type=Path)
    initialize.add_argument("--title", required=True)
    initialize.add_argument("--source", choices=("rehearsal", "real"), required=True)
    initialize.add_argument("--retention-reference", default="")
    for name in ("register", "observe", "corrections", "report"):
        child = commands.add_parser(name)
        child.add_argument("--book", required=True, type=Path)
        child.add_argument("--work", required=True, type=Path)
        if name != "report":
            child.add_argument("--job", required=True)
        if name == "register":
            child.add_argument("--part", required=True)
        elif name == "corrections":
            child.add_argument("--record", required=True, type=Path)
        elif name == "observe":
            child.add_argument("--question", required=True)
            child.add_argument("--first-option", required=True)
            child.add_argument("--elapsed-seconds", type=float, required=True)
            child.add_argument("--observer", required=True)
        else:
            child.add_argument("--rapid-seconds", type=float, default=DEFAULT_RAPID_SECONDS,
                               help=f"秒选首项观察阈值，默认 {DEFAULT_RAPID_SECONDS:g} 秒（含边界）")
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            result = create_book(args.book, title=args.title, source=args.source, retention_reference=args.retention_reference)
        elif args.command == "register":
            result = register_case(args.book, args.work, args.job, args.part)
        elif args.command == "observe":
            result = record_observation(args.book, args.work, args.job, args.question, elapsed_seconds=args.elapsed_seconds,
                                        first_option_id=args.first_option, observer=args.observer)
        elif args.command == "corrections":
            result = record_corrections(args.book, args.work, args.job, json.loads(args.record.read_text()))
        else:
            result = report(args.book, args.work, rapid_seconds=args.rapid_seconds)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"试点记录未写入或统计中止：{error}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""T-27：同一进程串行重复真实出图，记录内容一致性、耗时和 RSS。"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import faulthandler
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import re
import shutil
import statistics
import subprocess
import sys
import threading
import time
import traceback

FORMATS = ("dxf", "svg", "pdf", "json")
DXF_HEADERS = {b"$TDCREATE": 40, b"$TDUPDATE": 40, b"$FINGERPRINTGUID": 2, b"$VERSIONGUID": 2}
DXF_MARKERS = (b"CREATED_BY_EZDXF", b"WRITTEN_BY_EZDXF")


def _dxf_bytes(data):
    lines = data.splitlines(keepends=True)
    if len(lines) % 2:
        raise ValueError("DXF 组码不完整")
    tags = [(int(lines[i].strip()), lines[i + 1].strip()) for i in range(0, len(lines), 2)]
    starts = [i for i, (code, value) in enumerate(tags) if code == 0] + [len(tags)]
    records = [range(a, b) for a, b in zip(starts, starts[1:])]

    def values(record, code):
        return [tags[i][1] for i in record if tags[i][0] == code]

    def entry(record, name):
        for i in record:
            if tags[i] == (3, name) and i + 1 < record.stop and tags[i + 1][0] in (350, 360):
                return tags[i + 1][1]
        return None

    metadata = None
    for record in records:
        if tags[record.start] == (0, b"DICTIONARY") and b"0" in values(record, 330):
            metadata = entry(record, b"EZDXF_META")
            if metadata:
                break
    if metadata is None:
        raise ValueError("DXF 缺少可识别的 EZDXF_META 字典")
    marker_handles = set()
    for record in records:
        if tags[record.start] == (0, b"DICTIONARY") and metadata in values(record, 5):
            marker_handles = {entry(record, name) for name in DXF_MARKERS}
    if not marker_handles or None in marker_handles:
        raise ValueError("DXF 创建/写入元数据不完整")
    section = None
    for i, (code, value) in enumerate(tags):
        if (code, value) == (0, b"SECTION") and i + 1 < len(tags):
            section = tags[i + 1][1]
        elif (code, value) == (0, b"ENDSEC"):
            section = None
        elif section == b"HEADER" and code == 9 and value in DXF_HEADERS:
            if i + 1 >= len(tags) or tags[i + 1][0] != DXF_HEADERS[value]:
                raise ValueError("DXF 已知元数据的组码发生变化")
            lines[(i + 1) * 2 + 1] = b"<creation-metadata>\n"
    for record in records:
        if tags[record.start] != (0, b"DICTIONARYVAR") or not marker_handles.intersection(values(record, 5)):
            continue
        for i in record:
            if tags[i][0] == 1:
                version, stamp = tags[i][1].split(b" @ ", 1)
                datetime.fromisoformat(stamp.decode("ascii"))
                lines[i * 2 + 1] = version + b" @ <creation-time>\n"
    return b"".join(lines)


def _pdf_bytes(data):
    trailer = re.search(rb"trailer\s*<<(.*?)>>\s*startxref\s*\d+\s*%%EOF\s*$", data, re.S)
    info = re.search(rb"/Info\s+(\d+)\s+(\d+)\s+R", trailer[1]) if trailer else None
    if not data.startswith(b"%PDF-") or info is None:
        raise ValueError("PDF 缺少可识别的 Info 元数据引用")
    pattern = rb"(?:^|\n)" + info[1] + rb" " + info[2] + rb" obj\s*(.*?)\s*endobj"
    obj = re.search(pattern, data, re.S)
    if obj is None or not obj[1].startswith(b"<<") or not obj[1].endswith(b">>"):
        raise ValueError("PDF Info 对象不是预期字典")

    def date(match):
        value = match[2]
        if not re.fullmatch(rb"D:\d{14}(?:Z|[+-]\d{2}'\d{2}')?", value):
            raise ValueError("PDF 日期结构发生变化")
        return match[1] + re.sub(rb"\d", b"0", value) + match[3]

    body = re.sub(rb"(/(?:CreationDate|ModDate)\s*\()([^)]*)(\))", date, obj[1])
    # 等长替换保留 xref 偏移；图面 stream 和其他对象一个字节也不改。
    return data[:obj.start(1)] + body + data[obj.end(1):]


def canonical_bytes(kind, data):
    if kind == "dxf":
        return _dxf_bytes(data)
    if kind == "pdf":
        return _pdf_bytes(data)
    if kind not in ("svg", "json"):
        raise ValueError(f"没有核验过的输出格式：{kind}")
    return data


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _code_hashes():
    root = Path(__file__).resolve().parents[1]
    return {str(path.relative_to(root)): _sha(path.read_bytes()) for path in sorted(root.rglob("*.py"))}


def _rss_bytes():
    value = subprocess.check_output(["ps", "-o", "rss=", "-p", str(os.getpid())], text=True, timeout=5)
    return int(value.strip()) * 1024


def _fingerprints(files):
    if set(files) != set(FORMATS):
        raise ValueError(f"预期四种输出，实际为 {sorted(files)}")
    raw, canonical, sizes = {}, {}, {}
    for kind in FORMATS:
        data = Path(files[kind]).read_bytes()
        if not data:
            raise ValueError(f"{kind} 输出为空")
        raw[kind], canonical[kind], sizes[kind] = _sha(data), _sha(canonical_bytes(kind, data)), len(data)
    return raw, canonical, sizes


def _append(handle, row):
    handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    handle.flush()
    os.fsync(handle.fileno())


def worker(output):
    output = Path(output)
    metadata = json.loads((output / "metadata.json").read_text())
    faulthandler.enable(all_threads=True)
    from drawform.pipeline import render_part

    pid, thread = os.getpid(), threading.get_ident()
    baseline_raw = baseline_canonical = None
    started = time.monotonic()
    with (output / "iterations.jsonl").open("x", encoding="utf-8") as journal:
        for number in range(1, metadata["iterations"] + 1):
            common = {"iteration": number, "pid": pid, "thread": thread}
            _append(journal, {**common, "event": "started"})
            folder = output / "iterations" / f"{number:04}"
            folder.mkdir(parents=True)
            try:
                tick = time.perf_counter()
                part = render_part(output / "input.step", folder, stem="stability", with_questions=True)
                elapsed = time.perf_counter() - tick
                raw, canonical, sizes = _fingerprints(dict(part.files))
                if baseline_raw is None:
                    baseline_raw, baseline_canonical = raw, canonical
                raw_diff = [kind for kind in FORMATS if raw[kind] != baseline_raw[kind]]
                canonical_diff = [kind for kind in FORMATS if canonical[kind] != baseline_canonical[kind]]
                row = {**common, "render_seconds": elapsed, "raw_sha256": raw, "canonical_sha256": canonical,
                       "file_sizes": sizes, "raw_differences": raw_diff, "canonical_differences": canonical_diff,
                       "question_count": len(part.questions), "dimension_count": len(part.dimensions)}
                del part
                row["rss_bytes"] = _rss_bytes()
                if canonical_diff:
                    _append(journal, {**row, "event": "failed", "reason": "nonmetadata_difference"})
                    print(json.dumps({"stopped_at": number, "differences": canonical_diff}), flush=True)
                    return 1
                _append(journal, {**row, "event": "completed"})
                if number == 1 or number % 25 == 0 or number == metadata["iterations"]:
                    print(json.dumps({"completed": number, "requested": metadata["iterations"],
                                      "rss_mib": round(row["rss_bytes"] / 1024 ** 2, 2),
                                      "elapsed_seconds": round(time.monotonic() - started, 2)}), flush=True)
            except Exception as error:
                _append(journal, {**common, "event": "failed", "reason": "render_or_check_error", "error": repr(error)})
                traceback.print_exc()
                return 1
    return 0


def _memory(values):
    if not values:
        return None
    window = values[-min(500, len(values)):]
    center = (len(window) - 1) / 2
    denominator = sum((i - center) ** 2 for i in range(len(window)))
    slope = sum((i - center) * value for i, value in enumerate(window)) / denominator if denominator else None
    return {"first_bytes": values[0], "last_bytes": values[-1], "min_bytes": min(values), "sampled_peak_bytes": max(values),
            "first_100_mean_bytes": statistics.mean(values[:100]), "last_100_mean_bytes": statistics.mean(values[-100:]),
            "previous_100_mean_bytes": statistics.mean(values[-200:-100]) if len(values) >= 200 else None,
            "last_window_size": len(window), "last_window_slope_bytes_per_100": slope * 100 if slope is not None else None,
            "assessment": "measured_only_no_automatic_leak_threshold"}


def summarize(rows, *, requested, returncode):
    completed, pending, last_started = [], None, 0
    pids, threads, errors = set(), set(), []
    failure = None
    for row in rows:
        number = row.get("iteration")
        pids.add(row.get("pid"))
        threads.add(row.get("thread"))
        if row.get("event") == "started":
            if pending is not None or number != len(completed) + 1:
                errors.append("轮次没有连续、串行执行")
            pending, last_started = number, number
        elif row.get("event") == "completed":
            if number != pending or number != len(completed) + 1 or row.get("canonical_differences"):
                errors.append("完成记录与开始记录或一致性结果不符")
            completed.append(row)
            pending = None
        elif row.get("event") == "failed":
            failure = row
            if number != pending:
                errors.append("失败轮次没有对应的开始记录")
        else:
            errors.append("未知进度事件")
    if len(pids) > 1 or len(threads) > 1 or None in pids or None in threads or len(completed) > requested:
        errors.append("进程、调用线程或轮次数不符合本次验证范围")
    if errors:
        status = "invalid_evidence"
    elif returncode < 0:
        status = "native_crash"
    elif returncode != 0 or failure:
        status = "failed"
    elif len(completed) != requested or pending is not None:
        status = "incomplete"
    else:
        status = "completed"
    times = [row["render_seconds"] for row in completed]
    return {
        "status": status, "requested": requested, "completed": len(completed), "last_started": last_started,
        "worker_returncode": returncode, "worker_pids": sorted(pids - {None}), "call_threads": sorted(threads - {None}),
        "requested_repetitions_met": status == "completed", "t27_repetitions_met": status == "completed" and requested >= 1000,
        "concurrency_verified": False, "errors": errors, "failure": failure,
        "raw_difference_counts": dict(Counter(kind for row in completed for kind in row["raw_differences"])),
        "memory": _memory([row["rss_bytes"] for row in completed]),
        "timing_seconds": None if not times else {"first": times[0], "mean": statistics.mean(times),
            "p95": sorted(times)[math.ceil(.95 * len(times)) - 1], "max": max(times)},
        "qualification": "⚠️ 本节全部指标基于机器生成语料，存在同源偏差，不代表真实件表现。\n⚠️ 真实件表现以「一次通过率」为准，见 §三。",
        "limits": ["同一合成样件、同一进程内串行调用；不证明 OCCT 并发安全。",
                   "RSS 是每轮出图和文件比对后的进程驻留内存，包含 Python、OCCT、序列化器及检查器；采样峰值不等于瞬时峰值。",
                   "只忽略已核实的创建元数据；JSON/SVG 不归一化、不舍弃候选、不做浮点舍入。"],
    }


def run(step, output, iterations=1000):
    if type(iterations) is not int or iterations < 1:
        raise ValueError("iterations 必须是正整数")
    step, output = Path(step).resolve(strict=True), Path(output).resolve()
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    shutil.copyfile(step, output / "input.step")
    metadata = {"source_step": str(step), "input_sha256": _sha((output / "input.step").read_bytes()),
                "iterations": iterations, "started_at_utc": datetime.now(timezone.utc).isoformat(),
                "platform": platform.platform(), "python": sys.version, "code_sha256": _code_hashes(),
                "dependencies": {name: importlib.metadata.version(name) for name in ("cadquery-ocp", "ezdxf", "matplotlib", "numpy")},
                "ignored_metadata": {"dxf_header": [key.decode() for key in DXF_HEADERS],
                                     "dxf_dictionary_times": [key.decode() for key in DXF_MARKERS],
                                     "pdf_info": ["CreationDate", "ModDate"]}}
    (output / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2))
    command = [sys.executable, "-m", "drawform.eval.stability", "_worker", str(output)]
    with (output / "worker-stderr.log").open("w") as errors:
        process = subprocess.run(command, stderr=errors)
    rows, damaged = [], []
    journal = output / "iterations.jsonl"
    if journal.exists():
        for number, line in enumerate(journal.read_text().splitlines(), 1):
            try:
                rows.append(json.loads(line))
            except ValueError:
                damaged.append(f"进度日志第 {number} 行损坏")
    summary = summarize(rows, requested=iterations, returncode=process.returncode)
    summary["input_unchanged"] = _sha((output / "input.step").read_bytes()) == metadata["input_sha256"]
    summary["source_unchanged"] = _code_hashes() == metadata["code_sha256"]
    if damaged or not summary["input_unchanged"] or not summary["source_unchanged"]:
        summary["status"] = "invalid_evidence"
        summary["requested_repetitions_met"] = summary["t27_repetitions_met"] = False
        summary["errors"] += damaged + ["需核对输入及源码指纹"]
    summary["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def main(argv=None):
    import argparse
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) == 2 and args[0] == "_worker":
        return worker(args[1])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--step", type=Path, required=True, help="合成 STEP 样件")
    parser.add_argument("--output", type=Path, required=True, help="尚不存在的输出目录，保留每轮四件套")
    parser.add_argument("--iterations", type=int, default=1000)
    options = parser.parse_args(args)
    try:
        summary = run(options.step, options.output, options.iterations)
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0 if summary["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

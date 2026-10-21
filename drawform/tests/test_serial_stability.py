"""稳定性检查器必须保留真实差异和中断，不能把未跑满当通过。"""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


def dxf(stamp="2026-10-13T10:00:00.000000+00:00", guid="old", coordinate="12.5"):
    tags = [(0, "SECTION"), (2, "HEADER"), (9, "$TDCREATE"), (40, stamp),
            (9, "$TDUPDATE"), (40, stamp), (9, "$FINGERPRINTGUID"), (2, guid),
            (9, "$VERSIONGUID"), (2, guid), (9, "$INSUNITS"), (70, "4"), (0, "ENDSEC"),
            (0, "SECTION"), (2, "ENTITIES"), (0, "LINE"), (5, "10"), (10, coordinate),
            (20, "0"), (0, "TEXT"), (5, "11"), (1, "must keep this annotation"), (0, "ENDSEC"),
            (0, "SECTION"), (2, "OBJECTS"), (0, "DICTIONARY"), (5, "A"), (330, "0"),
            (3, "EZDXF_META"), (350, "B"), (0, "DICTIONARY"), (5, "B"), (330, "A"),
            (3, "CREATED_BY_EZDXF"), (350, "C"), (3, "WRITTEN_BY_EZDXF"), (350, "D"),
            (0, "DICTIONARYVAR"), (5, "C"), (330, "B"), (1, "1.4.4 @ " + stamp),
            (0, "DICTIONARYVAR"), (5, "D"), (330, "B"), (1, "1.4.4 @ " + stamp),
            (0, "ENDSEC"), (0, "EOF")]
    return "".join(f"{code:3}\n{value}\n" for code, value in tags).encode()


def pdf(date=b"20261013100000", drawing=b"same drawing"):
    return (b"%PDF-1.4\n1 0 obj\n<< /Length 12 >>\nstream\n" + drawing + b"\nendstream\nendobj\n"
            b"2 0 obj\n<< /Creator (matplotlib) /CreationDate (D:" + date + b"+08'00') >>\nendobj\n"
            b"trailer\n<< /Info 2 0 R >>\nstartxref\n0\n%%EOF\n")


def test_dxf_normalization_only_ignores_identified_creation_metadata():
    from drawform.eval.stability import canonical_bytes
    a, b = dxf(), dxf(stamp="2026-10-13T11:00:00.000000+00:00", guid="new")
    assert a != b
    assert canonical_bytes("dxf", a) == canonical_bytes("dxf", b)
    assert canonical_bytes("dxf", a) != canonical_bytes("dxf", dxf(coordinate="99"))
    assert canonical_bytes("dxf", a) != canonical_bytes("dxf", a.replace(b"must keep this annotation", b"changed dimension"))


def test_ezdxf_version_and_unrelated_header_values_are_still_compared():
    from drawform.eval.stability import canonical_bytes
    original = dxf()
    assert canonical_bytes("dxf", original) != canonical_bytes("dxf", original.replace(b"1.4.4 @", b"1.4.5 @"))
    assert canonical_bytes("dxf", original) != canonical_bytes("dxf", original.replace(b"$INSUNITS\n 70\n4", b"$INSUNITS\n 70\n1"))


def test_pdf_only_removes_dates_in_the_referenced_info_object():
    from drawform.eval.stability import canonical_bytes
    a, b = pdf(), pdf(date=b"20261013110000")
    assert a != b
    assert canonical_bytes("pdf", a) == canonical_bytes("pdf", b)
    assert canonical_bytes("pdf", a) != canonical_bytes("pdf", pdf(drawing=b"changed dimension"))
    assert canonical_bytes("pdf", pdf(drawing=b"/CreationDate (D:20261013100000Z)")) != canonical_bytes(
        "pdf", pdf(drawing=b"/CreationDate (D:20261013110000Z)"))


@pytest.mark.parametrize("kind", ["json", "svg"])
def test_geometry_outputs_are_compared_without_numeric_rounding_or_filtering(kind):
    from drawform.eval.stability import canonical_bytes
    a, b = b"value=1.0000001", b"value=1.0000002"
    assert canonical_bytes(kind, a) == a
    assert canonical_bytes(kind, a) != canonical_bytes(kind, b)


@pytest.mark.parametrize("kind,data", [("dxf", b"not DXF"), ("pdf", b"%PDF-1.4\ntruncated")])
def test_unreadable_metadata_is_not_silently_normalized(kind, data):
    from drawform.eval.stability import canonical_bytes
    with pytest.raises(ValueError):
        canonical_bytes(kind, data)


def complete(number, pid=123, thread=7):
    return [{"event": "started", "iteration": number, "pid": pid, "thread": thread},
            {"event": "completed", "iteration": number, "pid": pid, "thread": thread,
             "render_seconds": .25, "rss_bytes": 1024 * 1024 * (100 + number),
             "raw_differences": ["dxf"], "canonical_differences": []}]


def test_a_native_crash_keeps_the_last_started_iteration_and_partial_count():
    from drawform.eval.stability import summarize
    rows = complete(1) + [{"event": "started", "iteration": 2, "pid": 123, "thread": 7}]
    result = summarize(rows, requested=1000, returncode=-11)
    assert result["status"] == "native_crash"
    assert result["completed"] == 1 and result["last_started"] == 2
    assert result["requested_repetitions_met"] is False


def test_a_zero_exit_with_missing_iterations_is_not_success():
    from drawform.eval.stability import summarize
    result = summarize(complete(1), requested=1000, returncode=0)
    assert result["status"] == "incomplete"
    assert result["requested_repetitions_met"] is False


def test_changed_process_or_skipped_iteration_invalidates_the_run():
    from drawform.eval.stability import summarize
    for rows in (complete(1) + complete(2, pid=456), complete(1) + complete(3)):
        result = summarize(rows, requested=2, returncode=0)
        assert result["status"] == "invalid_evidence"


def test_completed_repetitions_keep_raw_differences_and_measured_memory():
    from drawform.eval.stability import summarize
    result = summarize(complete(1) + complete(2), requested=2, returncode=0)
    assert result["status"] == "completed"
    assert result["raw_difference_counts"] == {"dxf": 2}
    assert result["memory"]["last_bytes"] - result["memory"]["first_bytes"] == 1024 * 1024
    assert result["t27_repetitions_met"] is False
    assert result["concurrency_verified"] is False


def test_cli_runs_real_rendering_in_one_process_and_preserves_all_outputs(plate_step, tmp_path):
    from drawform.eval import stability
    root = Path(__file__).parents[1]
    output = tmp_path / "serial"
    env = {**os.environ, "PYTHONPATH": str(root / "src")}
    run = subprocess.run([sys.executable, "-m", stability.__name__, "--step", str(plate_step),
                          "--output", str(output), "--iterations", "3"],
                         env=env, capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stdout + run.stderr
    result = json.loads((output / "summary.json").read_text())
    assert result["completed"] == 3 and result["status"] == "completed"
    assert result["t27_repetitions_met"] is False
    assert len(result["worker_pids"]) == len(result["call_threads"]) == 1
    for iteration in range(1, 4):
        assert {p.suffix for p in (output / "iterations" / f"{iteration:04}").iterdir()} == {".dxf", ".svg", ".pdf", ".json"}

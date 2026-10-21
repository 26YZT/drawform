"""试点记录工具用合成审计验证口径，不把这些夹具当真实件成绩。"""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import ezdxf
import pytest

DRAFT = Path(__file__).parents[2] / "tools/dwg_verify/samples/part_0004.dxf"


def append(path, row):
    with path.open("a") as handle:
        handle.write(json.dumps(row) + "\n")


@pytest.fixture
def pilot(tmp_path):
    from drawform.eval.pilot import create_book
    book, work = tmp_path / "book.json", tmp_path / "work"
    work.mkdir()
    create_book(book, title="合成夹具", source="rehearsal")
    return book, work


def add_job(work, job="a" * 12, state="drawn", questions=()):
    root = work / job
    root.mkdir()
    (root / "model.step").write_text("synthetic fixture " + job)
    shutil.copy(DRAFT, root / "sheet.dxf")
    row = {"job": job, "state": state, "source": "model.step", "files": [["dxf", "sheet.dxf"]],
           "questions": list(questions), "answer_runs": []}
    append(work / "jobs.jsonl", row)
    return row


def review(work, job, decision, reasons=()):
    append(work / "reviews.jsonl", {"job": job, "decision": decision, "reviewer": "合成审计",
                                    "at": "2026-10-13T00:00:00+00:00", "reasons": list(reasons)})


def question(qid):
    return {"id": qid, "kind": "special_tolerance", "target": "合成问题",
            "options": [{"id": "z-option"}, {"id": "a-option"}]}


def answer(work, job, answers, state="drawn", key="request-1"):
    append(work / "reviews.jsonl", {"job": job, "event": "answers", "request_key": key,
                                    "state": state, "answers": answers, "reviewer": "合成审计"})


def test_empty_rehearsal_has_no_invented_rates_or_real_baseline(pilot):
    from drawform.eval.pilot import report
    result = report(*pilot)
    assert result["registered"] == 0
    assert result["first_pass"]["rate"] is None
    assert result["questions"]["selected_rate"] is None
    assert result["source"] == "rehearsal" and result["baseline"] == "not_a_real_parts_baseline"
    assert "存在同源偏差" in result["qualification"]


def test_real_book_requires_a_retention_reference(tmp_path):
    from drawform.eval.pilot import create_book, PilotError
    with pytest.raises(PilotError, match="留存"):
        create_book(tmp_path / "real.json", title="真实件", source="real")
    assert not (tmp_path / "real.json").exists()


def test_register_is_idempotent_and_prevents_duplicate_parts_and_step_files(pilot):
    from drawform.eval.pilot import register_case, PilotError
    book, work = pilot
    first = add_job(work)
    register_case(book, work, first["job"], "part-1")
    register_case(book, work, first["job"], "part-1")
    assert len(json.loads(book.read_text())["cases"]) == 1
    second = add_job(work, "b" * 12)
    with pytest.raises(PilotError, match="零件标识"):
        register_case(book, work, second["job"], "part-1")
    (work / second["job"] / "model.step").write_bytes((work / first["job"] / "model.step").read_bytes())
    with pytest.raises(PilotError, match="STEP"):
        register_case(book, work, second["job"], "part-2")


def test_first_decision_is_per_part_and_later_acceptance_does_not_erase_rejection(pilot):
    from drawform.eval.pilot import register_case, report
    book, work = pilot
    for n, decision in enumerate(("accepted", "amended", "rejected", None)):
        job = f"{n:012x}"
        add_job(work, job, state="drawn" if decision else "refused")
        register_case(book, work, job, f"part-{n}")
        if decision:
            review(work, job, decision, ["view_choice", "datum_choice"] if decision == "rejected" else [])
            review(work, job, "accepted")
    result = report(book, work)
    assert result["first_pass"] == {"accepted": 1, "amended": 1, "rejected": 1, "denominator": 3, "rate": 1 / 3}
    assert result["not_reviewed"] == {"refused": 1}
    assert result["rejection_reasons"] == {"view_choice": 1, "datum_choice": 1}


def test_answers_unknown_and_unanswered_stay_distinct_and_retries_count_once(pilot):
    from drawform.eval.pilot import register_case, report
    book, work = pilot
    row = add_job(work, questions=[question(f"q-{i}") for i in range(3)])
    register_case(book, work, row["job"], "part-1")
    answers = [{"question_id": "q-0", "chosen_option_id": "a-option"},
               {"question_id": "q-1", "chosen_option_id": None}]
    for state in ("queued", "drawn", "drawn"):
        answer(work, row["job"], answers, state=state)
    result = report(book, work)["questions"]
    assert (result["total"], result["selected"], result["unknown"], result["unanswered"]) == (3, 1, 1, 1)
    assert result["selected_rate"] == result["unknown_rate"] == 1 / 3


def test_failed_answers_do_not_become_confirmed_choices(pilot):
    from drawform.eval.pilot import register_case, report
    book, work = pilot
    row = add_job(work, questions=[question("q")])
    register_case(book, work, row["job"], "part-1")
    answer(work, row["job"], [{"question_id": "q", "chosen_option_id": None}], state="crashed")
    assert report(book, work)["questions"]["unanswered"] == 1


def test_missing_timing_or_threshold_is_not_a_clean_bill_of_health(pilot):
    from drawform.eval.pilot import register_case, report, record_observation
    book, work = pilot
    row = add_job(work, questions=[question("q")])
    register_case(book, work, row["job"], "part-1")
    answer(work, row["job"], [{"question_id": "q", "chosen_option_id": "a-option"}])
    assert report(book, work, rapid_seconds=3)["rapid_first"]["assessment"] == "missing_observations"
    record_observation(book, work, row["job"], "q", elapsed_seconds=1.5,
                       first_option_id="a-option", observer="观察人")
    assert report(book, work)["rapid_first"]["assessment"] == "threshold_not_set"
    result = report(book, work, rapid_seconds=3)
    assert result["rapid_first"]["count"] == 1
    assert "all_rapid_first" not in result["alerts"], "单题只记观察数，不触发五题提醒"


def test_single_unknown_is_counted_without_five_sample_warning(pilot):
    from drawform.eval.pilot import register_case, report
    book, work = pilot
    row = add_job(work, questions=[question("q")])
    register_case(book, work, row["job"], "part-1")
    answer(work, row["job"], [{"question_id": "q", "chosen_option_id": None}])
    assert "all_unknown" not in report(book, work)["alerts"], "不足五题不触发全未知提醒"


@pytest.mark.parametrize("seconds", [-1, float("nan"), float("inf"), True])
def test_invalid_observed_times_are_rejected(pilot, seconds):
    from drawform.eval.pilot import register_case, record_observation, PilotError
    book, work = pilot
    row = add_job(work, questions=[question("q")])
    register_case(book, work, row["job"], "part-1")
    with pytest.raises(PilotError, match="耗时"):
        record_observation(book, work, row["job"], "q", elapsed_seconds=seconds,
                           first_option_id="z-option", observer="观察人")


def test_observation_must_reference_a_closed_candidate_and_cannot_silently_change(pilot):
    from drawform.eval.pilot import register_case, record_observation, PilotError
    book, work = pilot
    row = add_job(work, questions=[question("q")])
    register_case(book, work, row["job"], "part-1")
    with pytest.raises(PilotError, match="候选"):
        record_observation(book, work, row["job"], "q", elapsed_seconds=1,
                           first_option_id="invented", observer="观察人")
    record_observation(book, work, row["job"], "q", elapsed_seconds=1,
                       first_option_id="z-option", observer="观察人")
    with pytest.raises(PilotError, match="已记录"):
        record_observation(book, work, row["job"], "q", elapsed_seconds=2,
                           first_option_id="z-option", observer="观察人")


def test_final_and_cumulative_differences_are_reported_separately(pilot):
    from drawform.eval.pilot import register_case, report
    book, work = pilot
    row = add_job(work)
    register_case(book, work, row["job"], "part-1")
    folder = work / row["job"]
    doc = ezdxf.readfile(folder / "sheet.dxf")
    dimension = next(e for e in doc.modelspace() if e.dxftype() == "DIMENSION")
    letter = next(e for e in dimension.get_geometry_block() if e.dxftype() in ("TEXT", "MTEXT"))
    letter.dxf.text = "999"
    doc.saveas(folder / "amended-1.dxf")
    shutil.copy(folder / "sheet.dxf", folder / "amended-2.dxf")
    row["files"] += [["amended-1", "amended-1.dxf"], ["amended-2", "amended-2.dxf"]]
    append(work / "jobs.jsonl", row)
    result = report(book, work)
    assert result["changes"]["from_draft"]["total"] == 0
    assert result["changes"]["adjacent_sum"]["total"] == 2
    assert result["changes"]["selected_basis"] == "human_verified_net"
    assert result["changes"]["manual_net"]["rate"] is None
    assert result["cases"][0]["version_changes"][0]["items"]


def test_missing_artifacts_and_changed_source_are_not_counted_as_zero_changes(pilot):
    from drawform.eval.pilot import register_case, report, PilotError
    book, work = pilot
    row = add_job(work)
    register_case(book, work, row["job"], "part-1")
    draft = work / row["job"] / "sheet.dxf"
    draft.rename(draft.with_suffix(".saved"))
    result = report(book, work)
    assert result["changes"]["from_draft"]["rate"] is None
    assert result["cases"][0]["missing_evidence"]
    (work / row["job"] / "model.step").write_text("changed source")
    with pytest.raises(PilotError, match="指纹"):
        report(book, work)


def test_corrupt_audit_is_reported_instead_of_silently_improving_the_rate(pilot):
    from drawform.eval.pilot import register_case, report, PilotError
    book, work = pilot
    row = add_job(work)
    register_case(book, work, row["job"], "part-1")
    (work / "reviews.jsonl").write_text('{"partial":')
    with pytest.raises(PilotError, match="reviews.jsonl.*1"):
        report(book, work)


@pytest.mark.parametrize("row", [{}, {"event": "answers", "state": "drawn", "answers": []}])
def test_audit_records_without_a_job_are_not_silently_discarded(pilot, row):
    from drawform.eval.pilot import report, PilotError
    book, work = pilot
    append(work / "reviews.jsonl", row)
    with pytest.raises(PilotError, match="审计.*任务"):
        report(book, work)


def test_job_paths_cannot_escape_the_registered_work_directory(pilot, tmp_path):
    from drawform.eval.pilot import register_case, PilotError
    book, work = pilot
    row = add_job(work)
    row["source"] = "../../outside.step"
    append(work / "jobs.jsonl", row)
    with pytest.raises(PilotError, match="路径"):
        register_case(book, work, row["job"], "part-1")


def test_report_is_read_only_and_cli_reads_the_book_in_a_fresh_process(pilot):
    from drawform.eval.pilot import register_case
    book, work = pilot
    row = add_job(work)
    register_case(book, work, row["job"], "part-1")
    review(work, row["job"], "accepted")
    paths = [book, work / "jobs.jsonl", work / "reviews.jsonl"]
    before = [hashlib.sha256(p.read_bytes()).hexdigest() for p in paths]
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")}
    process = subprocess.run([sys.executable, "-m", "drawform.eval.pilot", "report", "--book", str(book),
                              "--work", str(work)], env=env, capture_output=True, text=True)
    assert process.returncode == 0, process.stderr
    assert json.loads(process.stdout)["first_pass"]["accepted"] == 1
    assert before == [hashlib.sha256(p.read_bytes()).hexdigest() for p in paths]

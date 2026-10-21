"""用户于 2026-10-14 确认的 5 秒口径，通过实际统计命令验证。"""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from drawform.eval.pilot import record_observation, register_case
from tests.test_pilot_records import add_job, answer, pilot, question


def cli_report(book, work):
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")}
    result = subprocess.run([sys.executable, "-m", "drawform.eval.pilot", "report",
                             "--book", str(book), "--work", str(work)],
                            env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize("elapsed,chosen,rapid_count,unknown_count", [
    (4.999, "z-option", 1, 0),
    (5.0, "z-option", 1, 0),
    (5.001, "z-option", 0, 0),
    (1.0, "a-option", 0, 0),
    (1.0, None, 0, 1),
])
def test_cli_uses_confirmed_five_second_inclusive_boundary(pilot, elapsed, chosen, rapid_count, unknown_count):
    book, work = pilot
    job = add_job(work, questions=[question("q")])["job"]
    register_case(book, work, job, "synthetic-part")
    answer(work, job, [{"question_id": "q", "chosen_option_id": chosen}])
    record_observation(book, work, job, "q", elapsed_seconds=elapsed,
                       first_option_id="z-option", observer="合成观察记录")
    result = cli_report(book, work)
    assert result["rapid_first"]["threshold_seconds"] == 5
    assert result["rapid_first"]["count"] == rapid_count
    assert result["rapid_first"]["assessment"] == "measured"
    assert result["questions"]["unknown"] == unknown_count
    assert "all_rapid_first" not in result["alerts"], "5秒计数保留；单题不构成五题观察提醒"


def test_configured_threshold_does_not_turn_missing_time_into_a_normal_result(pilot):
    book, work = pilot
    job = add_job(work, questions=[question("q")])["job"]
    register_case(book, work, job, "synthetic-part")
    answer(work, job, [{"question_id": "q", "chosen_option_id": "z-option"}])
    result = cli_report(book, work)
    assert result["rapid_first"]["threshold_seconds"] == 5
    assert result["rapid_first"]["assessment"] == "missing_observations"
    assert result["rapid_first"]["missing"] == 1
    assert "all_rapid_first" not in result["alerts"]

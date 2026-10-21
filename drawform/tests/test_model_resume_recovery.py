"""Offline recovery gates for isolated model attempts; no CAD or workers.

The persisted model bytes below are deliberately synthetic. Native interruption
and model/drawing acceptance belongs to the separate process-level loop.
"""
from __future__ import annotations

import json
import shutil
import sqlite3

import pytest

from drawform.service import maintenance
from drawform.workbench.attempts import bind_intent, intent
from drawform.workbench.clarifications import basis
from drawform.workbench.revisions import canonical_sha
from drawform.workbench.recovery import (
    _validate_model, _validate_sessions, validate_workspace,
)
from tests.test_project_recovery import (
    BUNDLE, OWNER, SID, complete_storage_model, contents, session_data, sha,
    store_session, workspace, write_json,
)


ATTEMPT = "a" * 32
ANALYSIS = "e" * 32


def attempted_model(work):
    data = complete_storage_model(work)
    directory = work / "workbench" / "sessions" / SID
    attempt = directory / "attempts" / ATTEMPT
    shutil.copytree(directory / "artifacts", attempt / "artifacts")
    shutil.copyfile(directory / "manifest.json", attempt / "manifest.json")
    data["model_attempt_id"] = ATTEMPT
    data["current_attempt_id"] = ATTEMPT
    data["state"] = "adopted"
    data["validation"] = json.loads((attempt / "artifacts/drawform-validation.json").read_text())
    store_session(work, data, "adopted")
    return data


def test_legacy_model_recovery_stays_read_only_without_attempt_pointer(workspace):
    complete_storage_model(workspace)
    before = contents(workspace)
    assert validate_workspace(workspace)["verified"]
    assert contents(workspace) == before


def test_attempted_model_uses_committed_pointer_and_preserves_legacy_bytes(workspace):
    data = attempted_model(workspace)
    legacy = workspace / "workbench" / "sessions" / SID / "artifacts/model.step"
    legacy.write_bytes(b"older interrupted attempt, preserved but never selected")
    before = contents(workspace)
    _validate_model(workspace / "workbench", data, OWNER)
    assert contents(workspace) == before


@pytest.mark.parametrize("damage", ["missing_step", "changed_manifest", "wrong_validation", "symlink"])
def test_committed_attempt_must_have_its_own_complete_consistent_model(workspace, damage):
    data = attempted_model(workspace)
    attempt = workspace / "workbench" / "sessions" / SID / "attempts" / ATTEMPT
    if damage == "missing_step":
        (attempt / "artifacts/model.step").unlink()
    elif damage == "changed_manifest":
        document = json.loads((attempt / "manifest.json").read_text())
        document["source_sha256"] = "1" * 64
        write_json(attempt / "manifest.json", document)
    elif damage == "wrong_validation":
        path = attempt / "artifacts/drawform-validation.json"
        document = json.loads(path.read_text())
        document["step_sha256"] = "2" * 64
        write_json(path, document)
        data["artifact_hashes"]["drawform_validation"] = sha(path.read_bytes())
    else:
        path = attempt / "artifacts/model.step"
        path.unlink()
        path.symlink_to(workspace / "workbench" / "sessions" / SID / "artifacts/model.step")
    before = contents(workspace)
    with pytest.raises(ValueError):
        _validate_model(workspace / "workbench", data, OWNER)
    assert contents(workspace) == before


@pytest.mark.parametrize("pointer", ["../escape", "/tmp/attempt", "A" * 32, "a" * 31, 4])
def test_attempt_pointer_cannot_escape_saved_session(workspace, pointer):
    data = attempted_model(workspace)
    data["model_attempt_id"] = pointer
    with pytest.raises(ValueError):
        _validate_model(workspace / "workbench", data, OWNER)


def preview_records(work, *, pointer=ANALYSIS):
    preview = b"storage-only source preview"
    data = {"session_id": SID, "candidate_id": SID, "bundle_id": BUNDLE,
            "text": "", "text_sha256": "1" * 64, "source_sha256": "1" * 64,
            "input_kind": "drawing", "filename": "part.png", "sheet": "A3",
            "analysis_attempt_id": pointer, "source_preview_sha256": sha(preview)}
    row = {"id": SID, "owner": OWNER, "state": "analyzing", "request_key": "original",
           "payload_sha": "0" * 64, "decoded": data}
    inp = {"id": BUNDLE, "owner": OWNER, "text": "", "sha": "1" * 64}
    drawing = {"id": BUNDLE, "format": "png", "filename": "part.png"}
    path = work / "workbench" / "sessions" / SID / "attempts" / ANALYSIS / "analysis/preview.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(preview)
    return {SID: row}, {BUNDLE: inp}, {BUNDLE: drawing}


def test_source_preview_is_read_from_saved_analysis_attempt(workspace):
    sessions, inputs, drawings = preview_records(workspace)
    before = contents(workspace)
    _validate_sessions(workspace / "workbench", sessions, inputs, drawings)
    assert contents(workspace) == before


def test_revision_cannot_borrow_another_analysis_attempt_from_same_source(workspace):
    sessions, inputs, drawings = preview_records(workspace)
    child_id = "f" * 32
    child = json.loads(json.dumps(sessions[SID]))
    child["id"] = child_id
    child["decoded"].update(session_id=child_id, candidate_id=child_id,
                             source_preview_session_id=SID, analysis_attempt_id="9" * 32)
    sessions[child_id] = child
    with pytest.raises(ValueError, match="源图预览尝试"):
        _validate_sessions(workspace / "workbench", sessions, inputs, drawings)


def add_attempt_record(work, data, *, identity=ATTEMPT, state="complete", number=1):
    directory = work / "workbench" / "sessions" / SID / "attempts" / identity
    directory.mkdir(parents=True, exist_ok=True)
    frozen = intent(data, "build")
    record = {"number": number, "directory": "attempts/" + identity, "frozen": frozen,
              "runtime_sha256": "3" * 64, "created_at": "2026-10-18T00:00:00+00:00", "reason": ""}
    write_json(directory / "intent.json", record)
    if state in {"complete", "validated"}:
        result = {key: data[key] for key in ("step_sha256", "step_filename", "artifact_hashes", "validation")}
        result["model_attempt_id"] = identity
        record.update(result={"state": "awaiting_adoption", "values": result}, files={path.relative_to(directory).as_posix(): sha(path.read_bytes())
                                           for path in directory.rglob("*") if path.is_file()})
    else:
        (directory / "model.log").write_bytes(b"interrupted worker log preserved")
        record.update(reason="service_restart", retained_files={
            path.relative_to(directory).as_posix(): sha(path.read_bytes())
            for path in directory.rglob("*") if path.is_file()})
    with sqlite3.connect(work / "workbench/workspace.sqlite") as db:
        db.execute("""CREATE TABLE IF NOT EXISTS model_attempts(
id TEXT PRIMARY KEY,session_id TEXT NOT NULL REFERENCES sessions(id),owner TEXT NOT NULL,
kind TEXT NOT NULL,state TEXT NOT NULL,basis_sha TEXT NOT NULL,data TEXT NOT NULL)""")
        db.execute("INSERT INTO model_attempts VALUES(?,?,?,?,?,?,?)", (
            identity, SID, OWNER, "build", state, canonical_sha(frozen), json.dumps(record)))
    return record


def test_complete_attempt_and_history_survive_backup_and_new_directory_restore(workspace, tmp_path):
    data = attempted_model(workspace)
    add_attempt_record(workspace, data, identity="4" * 32, state="interrupted")
    add_attempt_record(workspace, data, number=2)
    data.update(current_attempt_id=ATTEMPT)
    bind_intent(data, "build")
    store_session(workspace, data, "adopted")
    before = contents(workspace)
    result = validate_workspace(workspace)
    assert result["model_attempts"] == {"attempts": 2, "complete": 1}
    backup, restored = tmp_path / "backup", tmp_path / "restored"
    maintenance.backup(workspace, backup)
    maintenance.restore(backup, restored)
    assert validate_workspace(restored)["model_attempts"] == result["model_attempts"]
    assert contents(workspace) == before == contents(restored)


def test_interrupted_attempt_is_validated_without_automatically_resuming_it(workspace):
    data = session_data(workspace)
    bind_intent(data, "build")
    data["current_attempt_id"] = ATTEMPT
    store_session(workspace, data, "interrupted")
    add_attempt_record(workspace, data, state="interrupted")
    before = contents(workspace)
    assert validate_workspace(workspace)["model_attempts"] == {"attempts": 1, "complete": 0}
    assert contents(workspace) == before
    with sqlite3.connect(workspace / "workbench/workspace.sqlite") as db:
        assert db.execute("SELECT state FROM sessions").fetchone()[0] == "interrupted"


def test_attempt_pointer_requires_a_matching_execution_history(workspace):
    attempted_model(workspace)
    with pytest.raises(ValueError, match="执行尝试指针"):
        validate_workspace(workspace)


def test_complete_execution_cannot_lose_its_atomic_session_selection(workspace):
    data = attempted_model(workspace)
    add_attempt_record(workspace, data)
    data.pop("model_attempt_id")
    store_session(workspace, data, "adopted")
    with pytest.raises(ValueError, match="同事务会话指针"):
        validate_workspace(workspace)


@pytest.mark.parametrize("damage", ["owner", "source", "spec", "intent_file", "directory", "number", "result", "incomplete"])
def test_execution_identity_frozen_intent_and_commit_are_checked_independently(workspace, damage):
    data = attempted_model(workspace)
    record = add_attempt_record(workspace, data)
    with sqlite3.connect(workspace / "workbench/workspace.sqlite") as db:
        if damage == "owner":
            db.execute("UPDATE model_attempts SET owner=?", ("5" * 64,))
        elif damage == "incomplete":
            db.execute("UPDATE model_attempts SET state='validated'")
        else:
            if damage == "source":
                record["frozen"]["source_sha256"] = "6" * 64
            elif damage == "spec":
                record["frozen"]["spec"]["parameters"]["length_mm"] = 101.
            elif damage == "directory":
                record["directory"] = "../../outside"
            elif damage == "number":
                record["number"] = 2
            elif damage == "result":
                record["result"]["values"]["step_sha256"] = "7" * 64
            else:
                path = workspace / "workbench/sessions" / SID / "attempts" / ATTEMPT / "intent.json"
                changed = json.loads(path.read_text())
                changed["runtime_sha256"] = "8" * 64
                write_json(path, changed)
            db.execute("UPDATE model_attempts SET data=?,basis_sha=?", (
                json.dumps(record), canonical_sha(record["frozen"])))
    before = contents(workspace)
    with pytest.raises(ValueError):
        validate_workspace(workspace)
    assert contents(workspace) == before


def test_retained_interrupted_attempt_files_cannot_be_silently_changed(workspace):
    data = session_data(workspace)
    data["current_attempt_id"] = ATTEMPT
    bind_intent(data, "build")
    store_session(workspace, data, "interrupted")
    add_attempt_record(workspace, data, state="interrupted")
    (workspace / "workbench/sessions" / SID / "attempts" / ATTEMPT / "model.log").write_bytes(b"changed")
    with pytest.raises(ValueError, match="执行尝试历史文件摘要失配"):
        validate_workspace(workspace)


def test_changed_session_execution_intent_blocks_recovery_even_if_attempt_is_untouched(workspace):
    data = session_data(workspace)
    bind_intent(data, "build")
    data["current_attempt_id"] = ATTEMPT
    add_attempt_record(workspace, data, state="interrupted")
    data["execution_intent"]["sheet"] = "A2"
    data["execution_intent_sha256"] = canonical_sha(data["execution_intent"])
    store_session(workspace, data, "interrupted")
    with pytest.raises(ValueError, match="会话执行意图"):
        validate_workspace(workspace)


def completed_analysis(work):
    root = work / "workbench"
    source = b"storage-only PNG source; parsing is covered separately"
    source_sha = sha(source)
    original = root / "inputs" / BUNDLE
    original.mkdir(parents=True)
    (original / "source.png").write_bytes(source)
    write_json(original / "input.json", {"source_sha256": source_sha, "filename": "part.png", "format": "png"})
    questions = [{"id": "q_thickness_mm", "parameter": "thickness_mm", "type": "number", "unit": "mm"}]
    analysis = {"kind": "plate", "source_sha256": source_sha, "units": "mm", "facts": [],
                "questions": questions, "parameters": {"length_mm": 100., "width_mm": 60., "thickness_mm": None}, "holes": []}
    directory = root / "sessions" / SID / "attempts" / ANALYSIS
    (directory / "analysis").mkdir(parents=True)
    preview = directory / "analysis/preview.png"
    preview.write_bytes(b"storage-only preview of original drawing")
    write_json(directory / "analysis/analysis.json", analysis)
    data = session_data(work)
    data.pop("spec")
    data.update(text="", text_sha256=source_sha, source_sha256=source_sha,
                input_kind="drawing", filename="part.png", parameters=analysis["parameters"])
    values = {"analysis": analysis, "questions": questions, "basis_sha256": basis(analysis, 1),
              "clarification_revision": 1, "source_preview_sha256": sha(preview.read_bytes()),
              "source_preview_url": f"/api/workbench/v1/modeling/sessions/{SID}/source-preview",
              "model_kind": "plate", "parameters": analysis["parameters"], "holes": [],
              "clarifications": [], "analysis_attempt_id": ANALYSIS}
    data.update(values, current_attempt_id=ANALYSIS)
    bind_intent(data, "analysis")
    record = {"number": 1, "directory": "attempts/" + ANALYSIS, "frozen": intent(data, "analysis"),
              "runtime_sha256": "3" * 64, "created_at": "2026-10-18T00:00:00+00:00", "reason": ""}
    write_json(directory / "intent.json", record)
    record.update(result={"state": "needs_clarification", "values": values}, files={
        path.relative_to(directory).as_posix(): sha(path.read_bytes())
        for path in directory.rglob("*") if path.is_file()})
    with sqlite3.connect(root / "workspace.sqlite") as db:
        db.execute("UPDATE inputs SET text='',sha=? WHERE id=?", (source_sha, BUNDLE))
        db.execute("INSERT INTO drawing_inputs VALUES(?,?,?)", (BUNDLE, "png", "part.png"))
        db.execute("""CREATE TABLE model_attempts(
id TEXT PRIMARY KEY,session_id TEXT NOT NULL REFERENCES sessions(id),owner TEXT NOT NULL,
kind TEXT NOT NULL,state TEXT NOT NULL,basis_sha TEXT NOT NULL,data TEXT NOT NULL)""")
        db.execute("INSERT INTO model_attempts VALUES(?,?,?,?,?,?,?)", (
            ANALYSIS, SID, OWNER, "analysis", "complete", canonical_sha(record["frozen"]), json.dumps(record)))
    store_session(work, data, "needs_clarification")
    return data, record


def test_completed_drawing_analysis_restores_its_original_clarification_basis(workspace):
    completed_analysis(workspace)
    before = contents(workspace)
    assert validate_workspace(workspace)["model_attempts"] == {"attempts": 1, "complete": 1}
    assert contents(workspace) == before


@pytest.mark.parametrize("damage", ["preview", "source", "basis", "state", "fields"])
def test_analysis_success_receipt_cannot_mix_source_preview_and_fact_versions(workspace, damage):
    _, record = completed_analysis(workspace)
    values = record["result"]["values"]
    if damage == "preview":
        values["source_preview_sha256"] = "7" * 64
    elif damage == "source":
        values["analysis"]["source_sha256"] = "7" * 64
    elif damage == "basis":
        values["basis_sha256"] = "7" * 64
    elif damage == "state":
        record["result"]["state"] = "awaiting_adoption"
    else:
        values["spec"] = {"kind": "plate"}
    with sqlite3.connect(workspace / "workbench/workspace.sqlite") as db:
        db.execute("UPDATE model_attempts SET data=?", (json.dumps(record),))
    before = contents(workspace)
    with pytest.raises(ValueError):
        validate_workspace(workspace)
    assert contents(workspace) == before

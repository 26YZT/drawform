"""Storage and recovery semantics; CAD geometry is covered by native loop tests.

The completed-model fixture below deliberately contains storage-only bytes. It
does not assert native geometry acceptance or replace the real CAD validation.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from drawform.service import maintenance
from drawform.service.production import INCOMPLETE_RESTORE, lock_work_directory
from drawform.workbench.recovery import validate_workspace


OWNER = hashlib.sha256(b"recovery-owner-seat").hexdigest()
BUNDLE = "b" * 32
SID = "c" * 32
JOB = "d" * 12


def sha(value):
    return hashlib.sha256(value).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False))


def contents(root):
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*")
            if path.is_file() and path.name != ".service.lock"}


@pytest.fixture
def workspace(tmp_path):
    work = tmp_path / "work"
    root = work / "workbench"
    root.mkdir(parents=True)
    db = sqlite3.connect(root / "workspace.sqlite")
    db.executescript('''
CREATE TABLE inputs(id TEXT PRIMARY KEY,owner TEXT NOT NULL,text TEXT NOT NULL,sha TEXT NOT NULL);
CREATE TABLE sessions(id TEXT PRIMARY KEY,owner TEXT NOT NULL,request_key TEXT NOT NULL,payload_sha TEXT NOT NULL,state TEXT NOT NULL,data TEXT NOT NULL,UNIQUE(owner,request_key));
CREATE TABLE drawing_inputs(id TEXT PRIMARY KEY,format TEXT NOT NULL,filename TEXT NOT NULL);
CREATE TABLE clarification_receipts(owner TEXT NOT NULL,session TEXT NOT NULL,key TEXT NOT NULL,payload_sha TEXT NOT NULL,PRIMARY KEY(owner,session,key));
CREATE TABLE model_revisions(child_id TEXT PRIMARY KEY REFERENCES sessions(id),owner TEXT NOT NULL,root_id TEXT NOT NULL REFERENCES sessions(id),parent_id TEXT NOT NULL REFERENCES sessions(id),number INTEGER NOT NULL,request_key TEXT NOT NULL,payload_sha TEXT NOT NULL,parent_step_sha TEXT NOT NULL,parent_spec_sha TEXT NOT NULL,spec_sha TEXT NOT NULL,changes_json TEXT NOT NULL,request_json TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(owner,parent_id,request_key),UNIQUE(root_id,number));
''')
    text = "长100宽60厚8的实心无孔板"
    source_sha = sha(text.encode())
    write_json(root / "inputs" / (BUNDLE + ".json"),
               {"schema_version": 1, "text": text, "text_sha256": source_sha, "units": "mm"})
    spec = {"schema_version": 2, "kind": "plate", "parameters": {
        "length_mm": 100., "width_mm": 60., "thickness_mm": 8.}, "holes": []}
    data = {"session_id": SID, "candidate_id": SID, "bundle_id": BUNDLE, "text": text,
            "text_sha256": source_sha, "source_sha256": source_sha, "input_kind": "text",
            "sheet": "A3", "parameters": spec["parameters"], "holes": [], "spec": spec,
            "model_kind": "plate", "human_review": "not_reviewed", "job_id": None}
    db.execute("INSERT INTO inputs VALUES(?,?,?,?)", (BUNDLE, OWNER, text, source_sha))
    db.execute("INSERT INTO sessions VALUES(?,?,?,?,?,?)", (SID, OWNER, "original", "0" * 64,
                                                         "queued", json.dumps(data)))
    db.commit()
    db.close()
    return work


def session_data(work):
    with sqlite3.connect(work / "workbench/workspace.sqlite") as db:
        return json.loads(db.execute("SELECT data FROM sessions WHERE id=?", (SID,)).fetchone()[0])


def store_session(work, data, state):
    with sqlite3.connect(work / "workbench/workspace.sqlite") as db:
        db.execute("UPDATE sessions SET state=?,data=? WHERE id=?", (state, json.dumps(data), SID))


def complete_storage_model(work):
    root = work / "workbench"
    data = session_data(work)
    artifacts = root / "sessions" / SID / "artifacts"
    artifacts.mkdir(parents=True)
    step = b"storage-only STEP placeholder; native tests separately validate geometry"
    (artifacts / "model.step").write_bytes(step)
    step_sha = sha(step)
    for name in ("preview.json", "validation.json", "drawform-validation.json"):
        write_json(artifacts / name, {"step_sha256": step_sha, "passed": True, "spec": data["spec"]})
    names = {"step": "model.step", "preview": "preview.json", "validation": "validation.json",
             "drawform_validation": "drawform-validation.json"}
    hashes = {kind: sha((artifacts / name).read_bytes()) for kind, name in names.items()}
    data.update(step_sha256=step_sha, step_filename="model.step", artifact_hashes=hashes)
    write_json(root / "sessions" / SID / "manifest.json", {
        "schema_version": 1, "parameters": data["parameters"], "text_sha256": data["text_sha256"],
        "artifacts": hashes, "spec": data["spec"], "input_kind": "text", "source_sha256": data["source_sha256"],
        "revision_provenance": None, "human_review": "not_reviewed"})
    write_json(root / "sessions" / SID / "adoption.json", {
        "candidate_id": SID, "step_sha256": step_sha, "parameters": data["parameters"],
        "adopted_by_seat": OWNER[:8], "human_review": "not_reviewed"})
    store_session(work, data, "adopted")
    return data


def bind_storage_drawing(work, data):
    directory = work / JOB
    directory.mkdir()
    (directory / "source.step").write_bytes((work / "workbench/sessions" / SID / "artifacts/model.step").read_bytes())
    files = []
    for kind in ("dxf", "svg", "pdf", "json"):
        (directory / ("drawing." + kind)).write_bytes(("storage-only drawing " + kind).encode())
        files.append([kind, "drawing." + kind])
    name = "storage-part.step"
    row = {"job": JOB, "state": "drawn", "source": "source.step", "files": files,
           "source_sha256": data["step_sha256"], "display_name": name, "sheet": "A3", "answer_runs": []}
    (work / "jobs.jsonl").write_text(json.dumps(row) + "\n")
    receipt_id = sha(("model:" + OWNER + ":" + SID).encode())
    payload_sha = sha(json.dumps({"sha": data["step_sha256"], "sheet": "A3", "name": name}, sort_keys=True).encode())
    write_json(work / "job-submissions" / (receipt_id + ".json"), {
        "kind": "job_submission", "job_id": JOB, "payload_sha": payload_sha,
        "state": "complete", "source_sha256": data["step_sha256"], "sheet": "A3"})
    write_json(work / "workbench/sessions" / SID / "drawing-binding.json", {
        "job_id": JOB, "session_id": SID, "step_sha256": data["step_sha256"],
        "text_sha256": data["text_sha256"], "parameters": data["parameters"]})
    data["job_id"] = JOB
    store_session(work, data, "adopted")


def test_empty_legacy_workspace_remains_supported_without_creating_a_database(tmp_path):
    work = tmp_path / "legacy"
    work.mkdir()
    assert validate_workspace(work)["present"] is False
    assert not (work / "workbench").exists()


def test_read_only_validation_preserves_inflight_session_and_all_bytes(workspace):
    before = contents(workspace)
    result = validate_workspace(workspace)
    assert result["verified"] and result["sessions"] == 1
    assert contents(workspace) == before
    with sqlite3.connect(workspace / "workbench/workspace.sqlite") as db:
        assert db.execute("SELECT state,owner FROM sessions").fetchone() == ("queued", OWNER)


@pytest.mark.parametrize("damage", ["missing_source", "changed_source", "cross_owner", "missing_db", "bad_db", "path_escape", "json_array", "duplicate_json", "nonfinite_json"])
def test_corrupt_workspace_blocks_backup_before_creating_output(workspace, tmp_path, damage):
    root = workspace / "workbench"
    source = root / "inputs" / (BUNDLE + ".json")
    if damage == "missing_source":
        source.unlink()
    elif damage == "changed_source":
        source.write_text('{"text":"changed"}')
    elif damage == "cross_owner":
        with sqlite3.connect(root / "workspace.sqlite") as db:
            db.execute("UPDATE inputs SET owner=?", ("a" * 64,))
    elif damage == "missing_db":
        (root / "workspace.sqlite").unlink()
    elif damage == "bad_db":
        (root / "workspace.sqlite").write_bytes(b"not sqlite")
    elif damage == "json_array":
        source.write_text('[]')
    elif damage == "duplicate_json":
        source.write_text('{"text":"a","text":"b"}')
    elif damage == "nonfinite_json":
        source.write_text('{"text":1e999}')
    else:
        data = session_data(workspace)
        data["bundle_id"] = "../outside"
        store_session(workspace, data, "queued")
    before = contents(workspace)
    output = tmp_path / "backup"
    with pytest.raises(ValueError, match="恢复校验"):
        maintenance.backup(workspace, output)
    assert not output.exists() and contents(workspace) == before


@pytest.mark.parametrize("damage", ["missing_model", "changed_preview", "manifest", "adoption_owner"])
def test_model_relationship_damage_is_rejected(workspace, damage):
    complete_storage_model(workspace)
    root = workspace / "workbench/sessions" / SID
    if damage == "missing_model":
        (root / "artifacts/model.step").unlink()
    elif damage == "changed_preview":
        (root / "artifacts/preview.json").write_text('{}')
    elif damage == "manifest":
        record = json.loads((root / "manifest.json").read_text())
        record["parameters"]["length_mm"] = 200
        write_json(root / "manifest.json", record)
    else:
        record = json.loads((root / "adoption.json").read_text())
        record["adopted_by_seat"] = "other"
        write_json(root / "adoption.json", record)
    with pytest.raises(ValueError, match="恢复校验"):
        validate_workspace(workspace)


def add_storage_revision(work):
    from drawform.workbench.revisions import apply_changes, canonical_sha
    parent = complete_storage_model(work)
    child_id = "e" * 32
    request = {"expected_step_sha256": parent["step_sha256"], "expected_spec_sha256": canonical_sha(parent["spec"]),
               "expected_revision": 1, "changes": [{"op": "set_parameter", "parameter": "length_mm", "value_mm": 120}]}
    spec, differences = apply_changes(parent["spec"], request["changes"])
    provenance = {"root_session_id": SID, "parent_session_id": SID, "number": 2,
                  "parent_step_sha256": parent["step_sha256"], "parent_spec_sha256": canonical_sha(parent["spec"]),
                  "spec_sha256": canonical_sha(spec), "changes": differences, "mode": "design_variant", "human_review": "not_reviewed"}
    child = {key: value for key, value in parent.items() if key not in {"step_sha256", "artifact_hashes", "step_filename"}}
    child.update(session_id=child_id, candidate_id=child_id, spec=spec, parameters=spec["parameters"],
                 revision_provenance=provenance)
    write_json(work / "workbench/sessions" / child_id / "revision.json", {
        "request": request, "provenance": provenance, "created_at": "2026-10-18T00:00:00Z"})
    with sqlite3.connect(work / "workbench/workspace.sqlite") as db:
        db.execute("INSERT INTO sessions VALUES(?,?,?,?,?,?)", (child_id, OWNER, "revision:" + child_id,
                   canonical_sha(request), "queued", json.dumps(child)))
        db.execute("INSERT INTO model_revisions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            child_id, OWNER, SID, SID, 2, "revision-request", canonical_sha(request), parent["step_sha256"],
            canonical_sha(parent["spec"]), canonical_sha(spec), json.dumps(differences), json.dumps(request), "2026-10-18T00:00:00Z"))
    return child_id


@pytest.mark.parametrize("damage", [None, "parent_sha", "cross_owner", "missing_revision_file", "missing_link"])
def test_revision_parent_chain_is_preserved_or_rejected_without_guessing(workspace, damage):
    child = add_storage_revision(workspace)
    if damage == "parent_sha":
        with sqlite3.connect(workspace / "workbench/workspace.sqlite") as db:
            db.execute("UPDATE model_revisions SET parent_step_sha=?", ("0" * 64,))
    elif damage == "cross_owner":
        with sqlite3.connect(workspace / "workbench/workspace.sqlite") as db:
            db.execute("UPDATE model_revisions SET owner=?", ("0" * 64,))
    elif damage == "missing_revision_file":
        (workspace / "workbench/sessions" / child / "revision.json").unlink()
    elif damage == "missing_link":
        with sqlite3.connect(workspace / "workbench/workspace.sqlite") as db:
            db.execute("DELETE FROM model_revisions")
    before = contents(workspace)
    if damage:
        with pytest.raises(ValueError, match="恢复校验"):
            validate_workspace(workspace)
    else:
        assert validate_workspace(workspace)["model_revisions"] == 1
    assert contents(workspace) == before


def add_partial_clarification(work):
    from drawform.workbench.clarifications import basis
    data = session_data(work)
    question = {"id": "q_thickness", "parameter": "thickness_mm", "label": "厚度？", "type": "number"}
    request = {"basis_sha256": "1" * 64, "revision": 1, "answers": {"q_thickness": 8.}}
    payload = sha(json.dumps(request, sort_keys=True, ensure_ascii=False, allow_nan=False).encode())
    fact = {"id": "answer_1_q_thickness", "parameter": "thickness_mm", "value": 8., "unit": "mm",
            "evidence": {"type": "user_answer", "text": "厚度？", "question_id": "q_thickness", "clarification_session_id": SID}}
    analysis = {"kind": "plate", "questions": [question], "facts": [fact]}
    data.update(analysis=analysis, questions=[question], clarification_revision=2,
                basis_sha256=basis(analysis, 2), clarifications=[fact])
    write_json(work / "workbench/sessions" / SID / "clarifications" / (payload + ".json"), {
        "request": request, "confirmations": [fact], "source_sha256": data["source_sha256"]})
    store_session(work, data, "needs_clarification")
    with sqlite3.connect(work / "workbench/workspace.sqlite") as db:
        db.execute("INSERT INTO clarification_receipts VALUES(?,?,?,?)", (OWNER, SID, "answer-request", payload))
    return payload


@pytest.mark.parametrize("damage", [None, "missing_receipt", "missing_answer_file", "stale_basis", "answer_owner"])
def test_partial_clarification_and_receipts_are_preserved_or_rejected(workspace, damage):
    payload = add_partial_clarification(workspace)
    if damage == "missing_receipt":
        with sqlite3.connect(workspace / "workbench/workspace.sqlite") as db:
            db.execute("DELETE FROM clarification_receipts")
    elif damage == "missing_answer_file":
        (workspace / "workbench/sessions" / SID / "clarifications" / (payload + ".json")).unlink()
    elif damage == "stale_basis":
        data = session_data(workspace)
        data["basis_sha256"] = "0" * 64
        store_session(workspace, data, "needs_clarification")
    elif damage == "answer_owner":
        with sqlite3.connect(workspace / "workbench/workspace.sqlite") as db:
            db.execute("UPDATE clarification_receipts SET owner=?", ("0" * 64,))
    before = contents(workspace)
    if damage:
        with pytest.raises(ValueError, match="恢复校验"):
            validate_workspace(workspace)
    else:
        assert validate_workspace(workspace)["clarification_receipts"] == 1
    assert contents(workspace) == before


@pytest.mark.parametrize("damage", [None, "source", "preview", "preview_owner"])
def test_original_drawing_and_preview_relationships(workspace, damage):
    root = workspace / "workbench"
    data = session_data(workspace)
    raw = b"storage-only DXF source"
    source_sha = sha(raw)
    directory = root / "inputs" / BUNDLE
    directory.mkdir()
    (directory / "source.dxf").write_bytes(raw)
    write_json(directory / "input.json", {"source_sha256": source_sha, "format": "dxf", "filename": "part.dxf"})
    preview = root / "sessions" / SID / "analysis/preview.png"
    preview.parent.mkdir(parents=True)
    preview.write_bytes(b"storage-only preview bytes")
    data.update(input_kind="drawing", text="", filename="part.dxf", source_sha256=source_sha,
                text_sha256=source_sha, source_preview_sha256=sha(preview.read_bytes()))
    store_session(workspace, data, "queued")
    with sqlite3.connect(root / "workspace.sqlite") as db:
        db.execute("UPDATE inputs SET text='',sha=?", (source_sha,))
        db.execute("INSERT INTO drawing_inputs VALUES(?,?,?)", (BUNDLE, "dxf", "part.dxf"))
    if damage == "source":
        (directory / "source.dxf").write_bytes(b"changed drawing")
    elif damage == "preview":
        preview.write_bytes(b"changed preview")
    elif damage == "preview_owner":
        data["source_preview_session_id"] = "0" * 32
        store_session(workspace, data, "queued")
    before = contents(workspace)
    if damage:
        with pytest.raises(ValueError, match="恢复校验"):
            validate_workspace(workspace)
    else:
        assert validate_workspace(workspace)["verified"]
    assert contents(workspace) == before


def test_unresolved_sqlite_journal_is_rejected_without_opening_or_repairing(workspace):
    journal = workspace / "workbench/workspace.sqlite-journal"
    journal.write_bytes(b"interrupted transaction evidence")
    before = contents(workspace)
    with pytest.raises(ValueError, match="事务日志"):
        validate_workspace(workspace)
    assert contents(workspace) == before


def test_step_to_job_binding_is_semantically_checked_even_with_updated_backup_hash(workspace, tmp_path):
    bind_storage_drawing(workspace, complete_storage_model(workspace))
    backup = tmp_path / "backup"
    maintenance.backup(workspace, backup)
    source = backup / "data" / JOB / "source.step"
    source.write_bytes(b"another STEP from another model")
    manifest = json.loads((backup / "manifest.json").read_text())
    entry = next(item for item in manifest["files"] if item["path"] == JOB + "/source.step")
    entry.update(bytes=source.stat().st_size, sha256=sha(source.read_bytes()))
    write_json(backup / "manifest.json", manifest)
    before = contents(backup)
    with pytest.raises(ValueError, match="出图来源"):
        maintenance.restore(backup, tmp_path / "restored")
    assert not (tmp_path / "restored").exists() and contents(backup) == before


def seal_storage_drawing(work, edition=0, files=None):
    data = session_data(work)
    slots = files or {kind: "drawing." + kind for kind in ("dxf", "svg", "pdf", "json")}
    record = {"schema_version": 1, "job_id": JOB, "drawing_edition": edition,
              "source_step_sha256": data["step_sha256"], "files": {
                  kind: {"path": name, "sha256": sha((work / JOB / name).read_bytes())}
                  for kind, name in slots.items()}}
    write_json(work / JOB / ("drawing-edition-" + str(edition) + ".json"), record)
    return record


@pytest.mark.parametrize("damage", [None, "output", "source_sha", "edition", "job_id", "escape", "slot"])
def test_saved_drawing_edition_seals_bind_all_outputs_to_their_source_and_edition(workspace, damage):
    bind_storage_drawing(workspace, complete_storage_model(workspace))
    seal = seal_storage_drawing(workspace)
    path = workspace / JOB / "drawing-edition-0.json"
    if damage == "output":
        (workspace / JOB / "drawing.pdf").write_bytes(b"a PDF from another version")
    elif damage == "source_sha":
        seal["source_step_sha256"] = "0" * 64
    elif damage == "edition":
        seal["drawing_edition"] = 1
    elif damage == "job_id":
        seal["job_id"] = "0" * 12
    elif damage == "escape":
        seal["files"]["pdf"]["path"] = "../outside.pdf"
    elif damage == "slot":
        (workspace / JOB / "other.pdf").write_bytes((workspace / JOB / "drawing.pdf").read_bytes())
        seal["files"]["pdf"]["path"] = "other.pdf"
    write_json(path, seal)
    before = contents(workspace)
    if damage:
        with pytest.raises(ValueError, match="恢复校验"):
            validate_workspace(workspace)
    else:
        assert validate_workspace(workspace)["verified"]
    assert contents(workspace) == before


def test_recomputed_backup_manifest_cannot_hide_changed_sealed_drawing(workspace, tmp_path):
    bind_storage_drawing(workspace, complete_storage_model(workspace))
    seal_storage_drawing(workspace)
    backup = tmp_path / "backup"
    maintenance.backup(workspace, backup)
    output = backup / "data" / JOB / "drawing.dxf"
    output.write_bytes(b"modified after the drawing suite was sealed")
    manifest = json.loads((backup / "manifest.json").read_text())
    entry = next(item for item in manifest["files"] if item["path"] == JOB + "/drawing.dxf")
    entry.update(bytes=output.stat().st_size, sha256=sha(output.read_bytes()))
    write_json(backup / "manifest.json", manifest)
    before = contents(backup)
    with pytest.raises(ValueError, match="图纸套件封存"):
        maintenance.restore(backup, tmp_path / "restored")
    assert not (tmp_path / "restored").exists() and contents(backup) == before


def test_uncommitted_answer_seal_is_verified_as_evidence_without_promoting_job_history(workspace):
    bind_storage_drawing(workspace, complete_storage_model(workspace))
    seal_storage_drawing(workspace)
    slots = {}
    for kind in ("dxf", "svg", "pdf", "json"):
        name = "uncommitted-1." + kind
        (workspace / JOB / name).write_bytes(("pending answer " + kind).encode())
        slots[kind] = name
    seal_storage_drawing(workspace, 1, slots)
    before = contents(workspace)
    assert validate_workspace(workspace)["verified"]
    row = json.loads((workspace / "jobs.jsonl").read_text())
    assert row["answer_runs"] == [] and row["state"] == "drawn"
    assert contents(workspace) == before


def test_new_directory_and_fresh_process_restore_preserve_ownership_and_completed_records(workspace, tmp_path):
    bind_storage_drawing(workspace, complete_storage_model(workspace))
    before = contents(workspace)
    backup, restored = tmp_path / "backup", tmp_path / "restored"
    maintenance.backup(workspace, backup)
    maintenance.verify_backup(backup)
    maintenance.restore(backup, restored)
    code = '''import json,sqlite3,sys
from pathlib import Path
from drawform.workbench.recovery import validate_workspace
p=Path(sys.argv[1]); r=validate_workspace(p); assert r['sessions']==1
db=sqlite3.connect(p/'workbench/workspace.sqlite')
row=db.execute('SELECT state,owner,data FROM sessions').fetchone()
assert row[0]=='adopted' and row[1]==sys.argv[2] and json.loads(row[2])['job_id']==sys.argv[3]
print(json.dumps(r))
'''
    result = subprocess.run([sys.executable, "-c", code, str(restored), OWNER, JOB],
                            env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert contents(workspace) == before == contents(restored)
    assert not (restored / INCOMPLETE_RESTORE).exists()


def test_restore_syncs_files_and_directories_before_clearing_marker(workspace, tmp_path, monkeypatch):
    backup, restored = tmp_path / "backup", tmp_path / "restored"
    maintenance.backup(workspace, backup)
    real_sync = maintenance._sync_tree
    observed = []
    def observe(root):
        if Path(root) == restored:
            observed.append((restored / INCOMPLETE_RESTORE).exists())
        return real_sync(root)
    monkeypatch.setattr(maintenance, "_sync_tree", observe)
    maintenance.restore(backup, restored)
    assert observed == [True] and not (restored / INCOMPLETE_RESTORE).exists()


def test_failed_restore_retains_marker_and_all_original_data(workspace, tmp_path, monkeypatch):
    backup, restored = tmp_path / "backup", tmp_path / "restored"
    maintenance.backup(workspace, backup)
    original = contents(workspace)
    saved = contents(backup)
    real_validate = maintenance._validate_workbench
    def fail_restored(root):
        if Path(root) == restored:
            raise ValueError("injected restored semantic validation failure")
        return real_validate(root)
    monkeypatch.setattr(maintenance, "_validate_workbench", fail_restored)
    with pytest.raises(ValueError, match="injected"):
        maintenance.restore(backup, restored)
    assert (restored / INCOMPLETE_RESTORE).exists()
    with pytest.raises(RuntimeError, match="恢复"):
        lock_work_directory(restored)
    assert contents(workspace) == original and contents(backup) == saved


def test_local_entrypoint_refuses_owned_workspace_before_constructing_services(workspace, monkeypatch):
    from drawform.service import __main__ as entrypoint
    constructed = []
    monkeypatch.setattr(entrypoint, "create_app", lambda path: constructed.append(path))
    with lock_work_directory(workspace):
        with pytest.raises(RuntimeError, match="占用"):
            entrypoint.main(["--work", str(workspace)])
    assert constructed == []


def test_local_entrypoint_refuses_restore_marker_before_constructing_services(workspace, monkeypatch):
    from drawform.service import __main__ as entrypoint
    (workspace / INCOMPLETE_RESTORE).write_text("interrupted")
    constructed = []
    monkeypatch.setattr(entrypoint, "create_app", lambda path: constructed.append(path))
    with pytest.raises(RuntimeError, match="恢复"):
        entrypoint.main(["--work", str(workspace)])
    assert constructed == []


def test_all_app_construction_refuses_restore_marker_before_worker_start(workspace):
    from drawform.service.app import create_app
    (workspace / INCOMPLETE_RESTORE).write_text("interrupted")
    before = contents(workspace)
    with pytest.raises(RuntimeError, match="恢复"):
        create_app(workspace, token="marker-test-token")
    assert contents(workspace) == before


def test_local_entrypoint_keeps_lease_until_every_writer_stops(workspace, monkeypatch):
    from types import SimpleNamespace
    from drawform.service import __main__ as entrypoint
    observed = []
    def assert_lease(stage):
        with pytest.raises(RuntimeError, match="占用"):
            lock_work_directory(workspace)
        observed.append(stage)
    def jobs_stop(*, timeout):
        assert timeout is None
        assert_lease("jobs_stopped")
    modeling = SimpleNamespace(stop=lambda: assert_lease("model_stopped"),
        worker=SimpleNamespace(join=lambda: assert_lease("model_joined")), jobs=SimpleNamespace(stop=jobs_stop))
    app = SimpleNamespace(extensions={"drawform_modeling": modeling},
                          run=lambda **kwargs: assert_lease("app_running"))
    monkeypatch.setattr(entrypoint, "create_app", lambda path: app)
    monkeypatch.setattr(entrypoint.signal, "signal", lambda *args: None)
    assert entrypoint.main(["--work", str(workspace)]) == 0
    assert observed == ["app_running", "model_stopped", "model_joined", "jobs_stopped"]
    with lock_work_directory(workspace):
        pass

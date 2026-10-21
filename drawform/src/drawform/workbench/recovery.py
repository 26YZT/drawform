"""Read-only validation of a stopped workspace before backup or recovery.

This module never constructs a modeling service, repairs records, changes HEAD,
or starts workers. A byte-perfect copy must also preserve the saved relations.
"""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import stat


_ID = re.compile(r"[0-9a-f]{32}")
_SHA = re.compile(r"[0-9a-f]{64}")
_JOB = re.compile(r"[0-9a-f]{12}")
_STATES = {"queued", "building", "analyzing", "cancel_requested", "interrupted",
           "failed", "needs_clarification", "awaiting_adoption", "adopted"}


def _fail(message):
    raise ValueError("工作区恢复校验失败：" + message)


def _json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                _fail("JSON含重复字段")
            result[key] = value
        return result
    def invalid(_value):
        _fail("JSON含非有限数值")
    def finite_float(value):
        parsed = float(value)
        if not math.isfinite(parsed):
            invalid(value)
        return parsed
    try:
        return json.loads(raw, object_pairs_hook=unique, parse_constant=invalid, parse_float=finite_float)
    except (TypeError, UnicodeError, json.JSONDecodeError, RecursionError) as error:
        _fail("JSON记录损坏：" + str(error))


def _file(root, name):
    if (not isinstance(name, str) or not name or "\\" in name or "\0" in name
            or Path(name).is_absolute() or any(p in ("", ".", "..") for p in name.split("/"))):
        _fail("文件路径越界或无效")
    path = root / name
    current = root
    for part in name.split("/"):
        current = current / part
        if current.is_symlink():
            _fail("文件或父目录是符号链接：" + name)
    if not path.is_file() or not stat.S_ISREG(path.stat().st_mode):
        _fail("必要文件缺失：" + name)
    return path


def _read(root, name):
    result = _json(_file(root, name).read_bytes())
    if not isinstance(result, dict):
        _fail("必要JSON文件不是对象：" + name)
    return result


def _sha(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _identifier(value, label, pattern=_ID):
    if not isinstance(value, str) or not pattern.fullmatch(value):
        _fail(label + "无效")
    return value


def _canonical(value):
    from .revisions import canonical_sha
    return canonical_sha(value)


def _job_rows(work):
    path = work / "jobs.jsonl"
    if not path.exists():
        return {}
    result = {}
    for line in _file(work, "jobs.jsonl").read_bytes().splitlines():
        if not line.strip():
            continue
        row = _json(line)
        if not isinstance(row, dict):
            _fail("任务索引记录无效")
        identity = _identifier(row.get("job"), "任务ID", _JOB)
        result[identity] = row
    return result


def _validate_input(root, row, drawing):
    identity = _identifier(row["id"], "输入ID")
    _identifier(row["owner"], "输入所有权", _SHA)
    _identifier(row["sha"], "输入摘要", _SHA)
    if not isinstance(row["text"], str):
        _fail("输入文字无效")
    if drawing:
        if drawing["format"] not in {"dxf", "pdf", "png", "jpeg"} or row["text"] != "":
            _fail("原图格式或输入类型不一致")
        source = _file(root, "inputs/" + identity + "/source." + drawing["format"])
        meta = _read(root, "inputs/" + identity + "/input.json")
        if (meta.get("source_sha256") != row["sha"] or meta.get("filename") != drawing["filename"]
                or meta.get("format") != drawing["format"] or _sha(source) != row["sha"]):
            _fail("原始图纸与输入索引不一致")
    else:
        saved = _read(root, "inputs/" + identity + ".json")
        sha = hashlib.sha256(row["text"].encode()).hexdigest()
        if (saved.get("text") != row["text"] or saved.get("text_sha256") != sha
                or saved.get("units") != "mm" or row["sha"] != sha):
            _fail("原始文字与输入索引不一致")


def _validate_model(root, data, owner):
    from .revisions import normalized_spec
    from .paths import model_relative, manifest_relative
    sid = data["session_id"]
    normalized_spec(data)
    hashes = data.get("artifact_hashes")
    if not isinstance(hashes, dict) or set(hashes) != {"step", "preview", "validation", "drawform_validation"}:
        _fail("模型产物摘要清单无效")
    if hashes["step"] != data.get("step_sha256"):
        _fail("模型STEP摘要记录不一致")
    filename = data.get("step_filename", "plate.step")
    if filename not in ("plate.step", "model.step"):
        _fail("模型STEP文件名无效")
    names = {"step": filename, "preview": "preview.json", "validation": "validation.json",
             "drawform_validation": "drawform-validation.json"}
    for kind, name in names.items():
        _identifier(hashes[kind], "模型产物摘要", _SHA)
        relative = "sessions/" + sid + "/" + model_relative(data) + "/" + name
        if _sha(_file(root, relative)) != hashes[kind]:
            _fail("模型产物摘要失配：" + relative)
        if kind != "step":
            record = _read(root, relative)
            if record.get("step_sha256") != data["step_sha256"]:
                _fail("模型检查或预览指向了另一份STEP")
            if kind != "preview" and record.get("passed") is not True:
                _fail("模型检查未通过")
            if kind != "preview" and data.get("spec") is not None and record.get("spec") != data["spec"]:
                _fail("模型检查规格与保存规格不一致")
    manifest = _read(root, "sessions/" + sid + "/" + manifest_relative(data))
    expected = {"parameters": data["parameters"], "text_sha256": data["text_sha256"],
                "artifacts": hashes, "spec": data.get("spec"),
                "input_kind": data.get("input_kind", "text"),
                "source_sha256": data.get("source_sha256"),
                "revision_provenance": data.get("revision_provenance"),
                "human_review": data.get("human_review", "not_reviewed")}
    if any(manifest.get(key) != value for key, value in expected.items()):
        _fail("模型manifest与会话记录不一致")
    if data["state"] == "adopted":
        adoption = _read(root, "sessions/" + sid + "/adoption.json")
        if (adoption.get("candidate_id") != sid or adoption.get("step_sha256") != data["step_sha256"]
                or adoption.get("parameters") != data["parameters"] or adoption.get("adopted_by_seat") != owner[:8]):
            _fail("模型采用回执与会话或所有权不一致")


def _validate_sessions(root, sessions, inputs, drawings):
    for sid, row in sessions.items():
        _identifier(sid, "会话ID")
        owner = _identifier(row["owner"], "会话所有权", _SHA)
        data = row["decoded"]
        if (row["state"] not in _STATES or not isinstance(data, dict) or data.get("session_id") != sid
                or data.get("candidate_id") != sid or data.get("sheet") not in {"A3", "A2"}
                or not isinstance(row["request_key"], str) or not row["request_key"]):
            _fail("会话身份或状态无效")
        _identifier(row["payload_sha"], "会话请求摘要", _SHA)
        data["state"] = row["state"]
        inp = inputs.get(data.get("bundle_id"))
        if (inp is None or inp["owner"] != owner or data.get("text") != inp["text"]
                or data.get("text_sha256") != inp["sha"] or data.get("source_sha256") != inp["sha"]):
            _fail("会话来源或所有权不一致")
        is_drawing = inp["id"] in drawings
        if (data.get("input_kind", "text") == "drawing") != is_drawing:
            _fail("会话输入类型与来源不一致")
        if is_drawing and data.get("filename") != drawings[inp["id"]]["filename"]:
            _fail("会话原图名称不一致")
        if data.get("source_preview_sha256"):
            from .paths import analysis_relative
            preview_sid = data.get("source_preview_session_id", sid)
            source = sessions.get(preview_sid)
            if source is None or source["owner"] != owner or source["decoded"].get("bundle_id") != inp["id"]:
                _fail("源图预览所属会话不一致")
            if data.get("analysis_attempt_id") != source["decoded"].get("analysis_attempt_id"):
                _fail("源图预览尝试与来源会话不一致")
            preview = _file(root, "sessions/" + _identifier(preview_sid, "预览会话ID")
                            + "/" + analysis_relative(data) + "/preview.png")
            if _sha(preview) != data["source_preview_sha256"]:
                _fail("源图预览摘要失配")
        if "basis_sha256" in data:
            from .clarifications import basis
            analysis = data.get("analysis")
            revision = data.get("clarification_revision")
            if (not isinstance(analysis, dict) or type(revision) is not int or revision < 1
                    or basis(analysis, revision) != data["basis_sha256"]
                    or data.get("questions") != analysis.get("questions")):
                _fail("澄清依据或问题版本不一致")
        if row["state"] == "needs_clarification" and (not data.get("questions") or "basis_sha256" not in data):
            _fail("澄清态缺少问题或依据")
        if data.get("step_sha256") or row["state"] in {"awaiting_adoption", "adopted"}:
            _validate_model(root, data, owner)


def _validate_attempt_files(root, sid, identity, files):
    if not isinstance(files, dict) or not files:
        _fail("执行尝试缺少已封存文件清单")
    base = "sessions/" + sid + "/attempts/" + identity + "/"
    for name, expected in files.items():
        if not isinstance(name, str):
            _fail("执行尝试文件路径无效")
        _identifier(expected, "执行尝试文件摘要", _SHA)
        if _sha(_file(root, base + name)) != expected:
            _fail("执行尝试历史文件摘要失配：" + name)


def _validate_attempts(root, rows, sessions):
    """Check execution history without resuming it or changing selected versions."""
    from .attempts import intent
    attempts = {}
    numbers = {}
    allowed = {"prepared", "running", "validated", "complete", "interrupted", "failed", "blocked"}
    for row in rows:
        identity = _identifier(row["id"], "执行尝试ID")
        sid = _identifier(row["session_id"], "执行尝试会话ID")
        session = sessions.get(sid)
        if identity in attempts or session is None or row["owner"] != session["owner"]:
            _fail("执行尝试身份、所属会话或所有权不一致")
        if row["kind"] not in {"build", "analysis"} or row["state"] not in allowed:
            _fail("执行尝试类型或状态无效")
        if row["kind"] == "analysis" and session["decoded"].get("input_kind") != "drawing":
            _fail("图纸分析尝试对应了非图纸输入")
        record = _json(row["data"])
        if not isinstance(record, dict):
            _fail("执行尝试记录不是对象")
        frozen = record.get("frozen")
        expected_intent = intent(session["decoded"], row["kind"])
        if frozen != expected_intent or _canonical(frozen) != row["basis_sha"]:
            _fail("执行尝试冻结来源、规格或父版本依据不一致")
        _identifier(row["basis_sha"], "执行尝试依据摘要", _SHA)
        _identifier(record.get("runtime_sha256"), "执行尝试运行协议摘要", _SHA)
        number = record.get("number")
        if type(number) is not int or number < 1:
            _fail("执行尝试序号无效")
        family = (sid, row["kind"])
        if number in numbers.setdefault(family, set()):
            _fail("执行尝试序号重复")
        numbers[family].add(number)
        if record.get("directory") != "attempts/" + identity:
            _fail("执行尝试目录与身份不一致")
        saved = _read(root, "sessions/" + sid + "/attempts/" + identity + "/intent.json")
        fields = ("number", "directory", "frozen", "runtime_sha256", "created_at")
        if (set(saved) != set(fields) | {"reason"} or saved.get("reason") != ""
                or any(saved.get(key) != record.get(key) for key in fields)):
            _fail("执行尝试意图文件与持久化依据不一致")
        if not isinstance(record.get("created_at"), str) or not record["created_at"]:
            _fail("执行尝试创建记录缺失")
        if "files" in record:
            _validate_attempt_files(root, sid, identity, record["files"])
        if "retained_files" in record:
            _validate_attempt_files(root, sid, identity, record["retained_files"])
        if row["state"] in {"validated", "complete"}:
            if "intent.json" not in record.get("files", {}):
                _fail("已校验执行尝试未封存输入意图")
            receipt = record.get("result")
            if (not isinstance(receipt, dict) or set(receipt) != {"state", "values"}
                    or not isinstance(receipt["values"], dict)):
                _fail("已校验执行尝试缺少成功回执")
            result = receipt["values"]
            if row["kind"] == "build":
                if set(result) != {"step_sha256", "step_filename", "artifact_hashes", "validation", "model_attempt_id"}:
                    _fail("建模成功回执字段无效")
                if receipt["state"] != "awaiting_adoption" or result.get("model_attempt_id") != identity:
                    _fail("建模成功回执指向了另一执行尝试")
                _validate_model(root, {**session["decoded"], **result, "state": "awaiting_adoption"}, row["owner"])
                checked = _read(root, "sessions/" + sid + "/attempts/" + identity + "/artifacts/drawform-validation.json")
                if result["validation"] != checked:
                    _fail("建模成功回执与独立验证记录不一致")
            else:
                fields = {"analysis", "questions", "basis_sha256", "clarification_revision", "source_preview_sha256",
                          "source_preview_url", "model_kind", "parameters", "holes", "clarifications", "analysis_attempt_id"}
                if receipt["state"] == "queued":
                    fields.add("spec")
                if set(result) != fields:
                    _fail("图纸分析成功回执字段无效")
                if receipt["state"] not in {"queued", "needs_clarification"} or result.get("analysis_attempt_id") != identity:
                    _fail("图纸分析成功回执指向了另一执行尝试")
                preview = _file(root, "sessions/" + sid + "/attempts/" + identity + "/analysis/preview.png")
                if _sha(preview) != result.get("source_preview_sha256"):
                    _fail("图纸分析成功回执与预览不一致")
                analysis = result.get("analysis")
                if not isinstance(analysis, dict) or analysis.get("source_sha256") != frozen["source_sha256"]:
                    _fail("图纸分析成功回执与冻结原图不一致")
                if not isinstance(result["questions"], list) or not isinstance(result["parameters"], dict) or not isinstance(result["holes"], list):
                    _fail("图纸分析问题、尺寸或孔结构无效")
                from .clarifications import basis
                if (type(result["clarification_revision"]) is not int or result["clarification_revision"] != 1
                        or result["basis_sha256"] != basis(analysis, 1) or result["questions"] != analysis.get("questions")
                        or result["clarifications"] != [] or result["model_kind"] != analysis.get("kind")
                        or result["source_preview_url"] != f"/api/workbench/v1/modeling/sessions/{sid}/source-preview"):
                    _fail("图纸分析成功回执与尺寸澄清依据不一致")
                if receipt["state"] == "queued":
                    from .simple_spec import spec_from_analysis
                    spec = spec_from_analysis(analysis)
                    if result["questions"] or spec != result["spec"] or result["parameters"] != spec["parameters"] or result["holes"] != spec["holes"]:
                        _fail("图纸分析可建模规格与冻结事实不一致")
                elif not result["questions"] or result["parameters"] != analysis.get("parameters") or result["holes"] != analysis.get("holes", []):
                    _fail("图纸分析待澄清事实与回执不一致")
            selected = "model_attempt_id" if row["kind"] == "build" else "analysis_attempt_id"
            if row["state"] == "complete" and session["decoded"].get(selected) != identity:
                _fail("已提交执行尝试缺少同事务会话指针")
        attempts[identity] = {**row, "decoded": record}
    for family in numbers.values():
        if sorted(family) != list(range(1, len(family) + 1)):
            _fail("执行尝试历史缺号")
    for sid, session in sessions.items():
        data = session["decoded"]
        if "execution_intent" in data or "execution_intent_sha256" in data:
            frozen = data.get("execution_intent")
            if (not isinstance(frozen, dict) or frozen.get("kind") not in {"build", "analysis"}
                    or frozen != intent(data, frozen["kind"])
                    or data.get("execution_intent_sha256") != _canonical(frozen)):
                _fail("会话执行意图与冻结来源或规格不一致")
        for key, kind in (("current_attempt_id", None), ("model_attempt_id", "build"), ("analysis_attempt_id", "analysis")):
            if key not in data:
                continue
            identity = _identifier(data[key], "会话执行尝试指针")
            attempt = attempts.get(identity)
            source_sid = data.get("source_preview_session_id", sid) if key == "analysis_attempt_id" else sid
            if (attempt is None or attempt["session_id"] != source_sid or attempt["owner"] != session["owner"]
                    or (kind is not None and attempt["kind"] != kind)):
                _fail("会话执行尝试指针与所属操作不一致")
            if key != "current_attempt_id" and attempt["state"] != "complete":
                _fail("会话采用了尚未完成的执行尝试")
            if key == "model_attempt_id":
                result = attempt["decoded"]["result"]["values"]
                if any(result.get(k) != data.get(k) for k in ("step_sha256", "step_filename", "artifact_hashes", "validation")):
                    _fail("会话模型与已提交执行尝试成功回执不一致")
                if data.get("current_attempt_id") != identity:
                    _fail("当前模型执行尝试与已提交模型指针不一致")
            if key == "analysis_attempt_id" and data.get("source_preview_sha256") != attempt["decoded"]["result"]["values"].get("source_preview_sha256"):
                _fail("会话预览与已提交分析尝试不一致")
    return {"attempts": len(attempts), "complete": sum(r["state"] == "complete" for r in attempts.values())}


def _validate_revisions(root, rows, sessions):
    from .revisions import normalized_spec, apply_changes
    links = {row["child_id"]: row for row in rows}
    if len(links) != len(rows):
        _fail("重复模型子版本")
    numbers = set()
    for child, link in links.items():
        parent = sessions.get(link["parent_id"])
        current = sessions.get(child)
        original = sessions.get(link["root_id"])
        if (parent is None or current is None or original is None or link["root_id"] in links
                or any(row["owner"] != link["owner"] for row in (parent, current, original))):
            _fail("模型父子、根版本或所有权不一致")
        number = link["number"]
        if type(number) is not int or number < 2 or (link["root_id"], number) in numbers:
            _fail("模型版本号无效或重复")
        numbers.add((link["root_id"], number))
        p, c = parent["decoded"], current["decoded"]
        if (p.get("step_sha256") != link["parent_step_sha"]
                or _canonical(normalized_spec(p)) != link["parent_spec_sha"]
                or _canonical(normalized_spec(c)) != link["spec_sha"]):
            _fail("模型版本父STEP或规格摘要不一致")
        request = _json(link["request_json"])
        changes = _json(link["changes_json"])
        if (not isinstance(request, dict) or set(request) != {"expected_step_sha256", "expected_spec_sha256", "expected_revision", "changes"}
                or request.get("expected_step_sha256") != link["parent_step_sha"]
                or request.get("expected_spec_sha256") != link["parent_spec_sha"]
                or request.get("expected_revision") != number - 1
                or _canonical(request) != link["payload_sha"]):
            _fail("模型修改请求摘要不一致")
        expected, differences = apply_changes(normalized_spec(p), request.get("changes"))
        if expected != normalized_spec(c) or changes != differences:
            _fail("模型修改请求、差异与子规格不一致")
        provenance = {"root_session_id": link["root_id"], "parent_session_id": link["parent_id"],
                      "number": number, "parent_step_sha256": link["parent_step_sha"],
                      "parent_spec_sha256": link["parent_spec_sha"], "spec_sha256": link["spec_sha"],
                      "changes": changes, "mode": "design_variant", "human_review": "not_reviewed"}
        record = _read(root, "sessions/" + child + "/revision.json")
        if record.get("request") != request or record.get("provenance") != provenance or c.get("revision_provenance") != provenance:
            _fail("模型修改回执与版本索引不一致")
        seen = set()
        identity = child
        previous = number + 1
        for _ in range(65):
            node = links.get(identity)
            if node is None:
                if identity != link["root_id"]:
                    _fail("模型父链没有抵达登记根版本")
                break
            if identity in seen or node["root_id"] != link["root_id"] or node["number"] >= previous:
                _fail("模型父链出现循环或版本顺序错误")
            seen.add(identity)
            previous = node["number"]
            identity = node["parent_id"]
        else:
            _fail("模型父链超过64层")
    for sid, row in sessions.items():
        if bool(row["decoded"].get("revision_provenance")) != (sid in links):
            _fail("模型修改来源缺少对应版本记录")


def _validate_clarifications(root, receipts, sessions):
    recorded = {}
    for receipt in receipts:
        sid = receipt["session"]
        row = sessions.get(sid)
        if row is None or row["owner"] != receipt["owner"]:
            _fail("澄清回执所属会话或所有权不一致")
        sha = _identifier(receipt["payload_sha"], "澄清请求摘要", _SHA)
        record = _read(root, "sessions/" + sid + "/clarifications/" + sha + ".json")
        request = record.get("request")
        if (not isinstance(request, dict) or set(request) != {"basis_sha256", "revision", "answers"}
                or type(request.get("revision")) is not int or request["revision"] < 1
                or request["revision"] >= row["decoded"].get("clarification_revision", 0)
                or not isinstance(request.get("answers"), dict) or not request["answers"]
                or not isinstance(receipt.get("key"), str) or not receipt["key"]):
            _fail("澄清请求字段或版本无效")
        actual = hashlib.sha256(json.dumps(request, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
        if actual != sha or record.get("source_sha256") != row["decoded"]["source_sha256"]:
            _fail("澄清请求或原始输入摘要不一致")
        confirmations = record.get("confirmations")
        saved = row["decoded"].get("clarifications", [])
        if not isinstance(confirmations, list) or not confirmations or any(fact not in saved for fact in confirmations):
            _fail("澄清回执没有对应的持久化回答")
        for fact in confirmations:
            evidence = fact.get("evidence", {})
            question = evidence.get("question_id")
            if (question not in request["answers"] or fact.get("value") != request["answers"][question]
                    or evidence.get("type") != "user_answer" or evidence.get("clarification_session_id") != sid
                    or fact.get("id") != "answer_" + str(request["revision"]) + "_" + question):
                _fail("澄清确认事实与原始请求不一致")
        recorded.setdefault(sid, []).extend(confirmations)
    for sid, row in sessions.items():
        saved = row["decoded"].get("clarifications", [])
        received = recorded.get(sid, [])
        if (not isinstance(saved, list) or len(saved) != len(received)
                or any(fact not in received for fact in saved)):
            _fail("持久化澄清回答缺少幂等回执")


def _validate_bindings(work, root, sessions, jobs):
    for sid, row in sessions.items():
        data = row["decoded"]
        relative = "sessions/" + sid + "/drawing-binding.json"
        path = root / relative
        job_id = data.get("job_id")
        if job_id is None and not path.exists():
            continue
        binding = _read(root, relative)
        bound_job = _identifier(binding.get("job_id"), "图纸任务ID", _JOB)
        if job_id is not None and job_id != bound_job:
            _fail("模型会话与出图绑定任务不一致")
        if (binding.get("session_id") != sid or binding.get("step_sha256") != data.get("step_sha256")
                or binding.get("text_sha256") != data.get("text_sha256")
                or binding.get("parameters") != data.get("parameters")):
            _fail("出图绑定与模型规格或STEP不一致")
        job = jobs.get(bound_job)
        if job is None:
            _fail("出图绑定对应任务缺失")
        source = _file(work, bound_job + "/" + job.get("source", ""))
        if _sha(source) != data["step_sha256"] or job.get("source_sha256") != data["step_sha256"] or job.get("sheet", "A3") != data["sheet"]:
            _fail("模型STEP与实际出图来源不一致")
        receipt_name = hashlib.sha256(("model:" + row["owner"] + ":" + sid).encode()).hexdigest()
        receipt = _read(work, "job-submissions/" + receipt_name + ".json")
        payload_sha = hashlib.sha256(json.dumps({"sha": data["step_sha256"], "sheet": data["sheet"],
            "name": job.get("display_name", "")}, sort_keys=True).encode()).hexdigest()
        if (receipt.get("kind") != "job_submission" or receipt.get("job_id") != bound_job
                or receipt.get("source_sha256") != data["step_sha256"] or receipt.get("state") != "complete"
                or receipt.get("sheet") != data["sheet"] or receipt.get("payload_sha") != payload_sha):
            _fail("出图受理回执与绑定不一致")
        _validate_drawing_seals(work, job, data["step_sha256"])


def _validate_drawing_seals(work, job, source_sha):
    """Verify saved drawing editions without promoting an interrupted edition.

    Old jobs have no seals and remain compatible. A crash may leave a seal
    before the job index commit; its bytes are checked but it is never adopted.
    """
    identity = job["job"]
    directory = work / identity
    for path in directory.glob("drawing-edition-*.json"):
        match = re.fullmatch(r"drawing-edition-(0|[1-9][0-9]*)\.json", path.name)
        if not match:
            _fail("图纸套件封存版次名称无效")
        edition = int(match[1])
        record = _read(work, identity + "/" + path.name)
        if (record.get("schema_version") != 1 or record.get("job_id") != identity
                or type(record.get("drawing_edition")) is not int or record["drawing_edition"] != edition
                or record.get("source_step_sha256") != source_sha):
            _fail("图纸套件封存来源或版次不一致")
        files = record.get("files")
        if not isinstance(files, dict) or set(files) != {"dxf", "svg", "pdf", "json"}:
            _fail("图纸套件封存缺少完整四出口")
        names = set()
        for kind, entry in files.items():
            if not isinstance(entry, dict) or set(entry) != {"path", "sha256"}:
                _fail("图纸套件封存文件字段无效")
            _identifier(entry["sha256"], "图纸套件产物摘要", _SHA)
            file = _file(directory, entry.get("path"))
            if entry["path"] in names or _sha(file) != entry["sha256"]:
                _fail("图纸套件封存文件重复或摘要失配")
            names.add(entry["path"])
        if edition == 0:
            slots = dict(job.get("files", []))
        else:
            runs = [run for run in job.get("answer_runs", [])
                    if run.get("state") == "drawn" and run.get("version") == edition]
            if len(runs) > 1:
                _fail("同版图纸套件对应重复答案版本")
            slots = dict(runs[0].get("files", [])) if runs else {}
        if all(kind in slots for kind in files):
            if any(files[kind]["path"] != slots[kind] for kind in files):
                _fail("图纸套件封存与任务版次文件槽不一致")


def _validate_submissions(work, sessions, jobs):
    directory = work / "job-submissions"
    if not directory.exists():
        return 0
    if directory.is_symlink() or not directory.is_dir():
        _fail("出图受理回执目录无效")
    model_receipts = {hashlib.sha256(("model:" + row["owner"] + ":" + sid).encode()).hexdigest(): row
                      for sid, row in sessions.items()}
    count = 0
    for path in directory.iterdir():
        # Atomic-write remnants remain preserved forensic evidence; only a final
        # .json receipt is an authority that may identify an accepted operation.
        if path.suffix != ".json":
            continue
        _identifier(path.stem, "出图受理回执名称", _SHA)
        receipt = _read(work, "job-submissions/" + path.name)
        identity = _identifier(receipt.get("job_id"), "受理任务ID", _JOB)
        source_sha = _identifier(receipt.get("source_sha256"), "受理STEP摘要", _SHA)
        _identifier(receipt.get("payload_sha"), "受理请求摘要", _SHA)
        if receipt.get("kind") != "job_submission" or receipt.get("state") not in {"prepared", "complete"} or receipt.get("sheet") not in {"A3", "A2"}:
            _fail("出图受理回执字段无效")
        job = jobs.get(identity)
        if receipt["state"] == "complete" and job is None:
            _fail("已受理任务索引缺失")
        if job is not None:
            source = _file(work, identity + "/" + job.get("source", ""))
            payload = hashlib.sha256(json.dumps({"sha": source_sha, "sheet": receipt["sheet"],
                "name": job.get("display_name", "")}, sort_keys=True).encode()).hexdigest()
            if (_sha(source) != source_sha or job.get("source_sha256") != source_sha
                    or job.get("sheet", "A3") != receipt["sheet"] or receipt["payload_sha"] != payload):
                _fail("受理回执与实际任务来源不一致")
        model = model_receipts.get(path.stem)
        if model is not None and (model["decoded"].get("step_sha256") != source_sha
                or model["decoded"].get("sheet") != receipt["sheet"]):
            _fail("受理回执与原始建模请求不一致")
        count += 1
    return count


def validate_workspace(work):
    """Return a service-free semantic summary without mutating any saved record."""
    work = Path(work)
    if work.is_symlink() or not work.is_dir():
        _fail("工作目录必须是真实目录")
    from drawform.service.journal import validate as validate_publications
    publication_summary = validate_publications(work)
    root = work / "workbench"
    db_path = root / "workspace.sqlite"
    if not root.exists():
        return {"present": False, "verified": True, "inputs": 0, "sessions": 0,
                "model_revisions": 0, "legacy_publications": publication_summary}
    if root.is_symlink():
        _fail("workbench目录是符号链接")
    if not db_path.exists():
        if any(root.iterdir()):
            _fail("workbench存在数据但SQLite索引缺失")
        return {"present": False, "verified": True, "inputs": 0, "sessions": 0,
                "model_revisions": 0, "legacy_publications": publication_summary}
    _file(root, "workspace.sqlite")
    # A stopped normal DELETE-journal workspace has no live journals. Refusing
    # unresolved journals avoids opening SQLite in a mode that writes recovery
    # files or silently ignores newer WAL data.
    for suffix in ("-wal", "-journal"):
        journal = Path(str(db_path) + suffix)
        if journal.exists() and journal.stat().st_size:
            _fail("SQLite存在未收口事务日志，请先核对停服状态")
    try:
        with closing(sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA query_only=ON")
            if [row[0] for row in db.execute("PRAGMA integrity_check")] != ["ok"] or list(db.execute("PRAGMA foreign_key_check")):
                _fail("SQLite完整性或外键校验失败")
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {"inputs", "sessions"}.issubset(tables):
                _fail("SQLite核心表缺失")
            def rows(name):
                return [dict(row) for row in db.execute("SELECT * FROM " + name)] if name in tables else []
            input_rows, session_rows, drawing_rows = rows("inputs"), rows("sessions"), rows("drawing_inputs")
            inputs = {row["id"]: row for row in input_rows}
            sessions = {row["id"]: row for row in session_rows}
            drawings = {row["id"]: row for row in drawing_rows}
            if (len(inputs) != len(input_rows) or len(sessions) != len(session_rows)
                    or len(drawings) != len(drawing_rows)
                    or len({(row["owner"], row["request_key"]) for row in session_rows}) != len(session_rows)):
                _fail("输入、会话身份或受理请求重复")
            for row in sessions.values():
                row["decoded"] = _json(row["data"])
                if not isinstance(row["decoded"], dict):
                    _fail("会话JSON不是对象")
            if set(drawings) - set(inputs):
                _fail("原图索引缺少输入记录")
            for row in inputs.values():
                _validate_input(root, row, drawings.get(row["id"]))
            _validate_sessions(root, sessions, inputs, drawings)
            attempt_summary = _validate_attempts(root, rows("model_attempts"), sessions)
            revisions = rows("model_revisions")
            _validate_revisions(root, revisions, sessions)
            receipts = rows("clarification_receipts")
            _validate_clarifications(root, receipts, sessions)
            jobs = _job_rows(work)
            _validate_bindings(work, root, sessions, jobs)
            submission_count = _validate_submissions(work, sessions, jobs)
            project_summary = {}
            if "projects" in tables:
                from .projects import validate_project_store
                project_summary = validate_project_store(work)
    except (sqlite3.Error, KeyError, TypeError, IndexError, AttributeError) as error:
        _fail("索引字段或SQLite记录损坏：" + str(error))
    return {"present": True, "verified": True, "inputs": len(inputs), "sessions": len(sessions),
            "model_revisions": len(revisions), "clarification_receipts": len(receipts),
            "job_submissions": submission_count, "projects": project_summary,
            "legacy_publications": publication_summary, "model_attempts": attempt_summary}

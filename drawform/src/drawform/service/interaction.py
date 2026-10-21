"""Read-only workspace context and version-bound, retry-safe review submission.

These fields describe the user's task; they do not infer engineering decisions.
Legacy endpoint shapes and review records remain supported.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import re
import unicodedata
import uuid
from dataclasses import asdict

from drawform.review import ACCEPTED, ReviewError, is_reviewable
from drawform.service.jobs import head_of, versions_of


def display_name(value):
    name = str(value or "").replace("\\", "/").rsplit("/", 1)[-1]
    return "".join(c for c in name if not unicodedata.category(c).startswith("C")).strip()[:180]


def sha_file(path):
    try:
        return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()
    except (OSError, TypeError):
        return ""


def read_source_names(path):
    """An optional operator-supplied SHA catalogue; never derive a storage path from it."""
    if not path:
        return {}
    data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("来源名称目录必须是 SHA256 到显示名称的对象")
    return {key: display_name(value) for key, value in data.items()
            if re.fullmatch(r"[0-9a-f]{64}", key) and isinstance(value, str)}


def metadata(job, source_names=None):
    name, origin = job.display_name, "upload" if job.display_name else "unknown"
    if not name and source_names:
        # Old rows did not store filenames. Only exact source bytes justify a recovered name.
        name = (source_names or {}).get(sha_file(job.source), "")
        if name:
            origin = "verified_catalog"
    return {"display_name": name, "name_origin": origin, "created_at": job.created_at}


def task_context(job):
    source_sha = sha_file(job.source)
    expected_source = job.source_sha256 or (job.reference_snapshot or {}).get("source_sha256", "")
    effective = head_of(job) or dict(job.files).get("dxf")
    # The persisted question catalogue is the same input used by submit_answers.
    questions = json.dumps([asdict(q) for q in job.questions], sort_keys=True,
                           ensure_ascii=False, separators=(",", ":"))
    return {"source_sha256": source_sha, "sheet": job.sheet,
            "source_valid": bool(source_sha) and (not expected_source or source_sha == expected_source),
            "question_basis": hashlib.sha256(questions.encode()).hexdigest(),
            "reference_basis": (job.reference_snapshot or {}).get("basis_sha", ""),
            "effective_sha": sha_file(effective), "head_version": len(versions_of(job))}


def reference_receipt(job):
    return [{"state": run["state"], "reviewer": run.get("reviewer", ""),
             "created_at": run.get("created_at", ""),
             "count": len(run["submission"].get("answers", [])),
             "unknown_count": sum(a.get("option_id") is None for a in run["submission"].get("answers", []))}
            for run in job.reference_runs]


class ReviewConflict(ValueError):
    def __init__(self, message, **context):
        super().__init__(message)
        self.context = context


def submit_review(queue, reviews, job_id, body, token_hint="", token_user="", token_scope=""):
    """Validate and append while both version publication and freeze changes are locked.

    submission_id is scoped to a job and authenticated seat. Replays return the original
    row even after restart; changing the payload under an existing key is a conflict.
    reviewed_* intentionally differs from effective_* (which denotes a CAD upload).
    """
    if not isinstance(body, dict):
        raise ReviewError("复核内容必须是对象")
    bound = "reviewed_sha" in body or "reviewed_version" in body
    key = body.get("submission_id", "")
    if "submission_id" in body and (not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,100}", key)):
        raise ReviewError("复核提交编号无效")
    if bound and (not re.fullmatch(r"[0-9a-f]{64}", str(body.get("reviewed_sha", "")))
                  or type(body.get("reviewed_version")) is not int or body["reviewed_version"] < 0):
        raise ReviewError("复核需要完整的图纸版本与 SHA256")
    reasons = body.get("reasons")
    if reasons is not None and (not isinstance(reasons, list) or any(not isinstance(r, str) for r in reasons)):
        raise ReviewError("reasons 必须是原因编号的字符串数组")
    payload = {"decision": str(body.get("decision", "")),
               "reviewer": str(body.get("reviewer", "")).strip(),
               "reasons": body.get("reasons") or [], "note": str(body.get("note", ""))}
    if bound:
        payload.update(reviewed_sha=body["reviewed_sha"], reviewed_version=body["reviewed_version"])
    fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                             separators=(",", ":")).encode()).hexdigest()
    scope = hashlib.sha256((token_scope + "\0" + token_user).encode()).hexdigest() if token_scope else token_hint + ":" + token_user
    with queue.review_guard():
        job = queue.get(job_id)
        if key:
            previous = next((r for r in reviews.for_job(job_id)
                             if r.get("submission_id") == key and r.get("submission_scope", "") == scope), None)
            if previous:
                if previous.get("submission_fingerprint") != fingerprint:
                    raise ReviewConflict("提交编号已用于其他内容，请重新确认复核摘要", reason="submission_conflict")
                return previous
        if not is_reviewable(job.state):
            raise ReviewConflict("这张图没有出图成功，没有可复核的对象",
                                 state=job.state, failed=list(job.failed))
        if bound:
            context = task_context(job)
            if (body["reviewed_sha"] != context["effective_sha"]
                    or body["reviewed_version"] != context["head_version"]):
                raise ReviewConflict("图纸版本已变化，请读取最新图纸并重新确认复核摘要",
                                     reason="stale_base", head=context["head_version"])
        if payload["decision"] == ACCEPTED and job.id in reviews.frozen():
            raise ReviewConflict("这张图已有数值级错误记录，不能标「直接用」。请改好后回传生效版本，或驳回说明", frozen=True)
        row = reviews.record(job_id=job_id, **payload, token_hint=token_hint, token_user=token_user,
                              client_bound=bound, submission_id=key, submission_scope=scope,
                              submission_fingerprint=fingerprint if key else "", _persist=False)
        try:
            return queue.publish_review(job_id, row,
                        key=job_id+':'+scope+':'+(key or uuid.uuid4().hex))
        except ValueError as error:
            raise ReviewConflict(str(error),reason='submission_conflict') from error

"""复核结论与审计日志：把"这张图最后被怎么处理的"记下来。

《接入与推广方案》第三节里那条责任链有两个缺口，这一层补的是第一个：
**接受 / 改后采用 / 驳回** 得有地方落，否则试点期只能靠人工登记，
而人工登记的数据一定收不齐 —— 那意味着我们永远拿不到"机器该学什么"的信号。

两个刻意的设计决定：

* **日志是 work 目录里一份独立的 JSONL**，不放在任务目录下。任务目录和版本链不按数量自动清理；
  "谁在什么时候把这张图判成了什么"与图纸一起受试点留存规则保护。
* **理由分类是必填项（驳回时）**，且只认下面这张表里的 id。自由文本没法统计，
  而统计才是它存在的唯一理由。分类表是按"机器目前确实没把握的地方"拟的，
  **不是任何标准里的分类**，试点期拿到真实分布后要改。

⚠️ `reviewer` 是自报的。在挂到集团 SSO 之前，它只是"自称是谁"，**不是身份凭证**；
真正的签字生效仍然发生在线下/PDM 流程里，这里记的是使用事实，不是签署事实。
"""

from __future__ import annotations

import json
import os
import pathlib
import threading
from datetime import datetime, timezone
from dataclasses import asdict

ACCEPTED = "accepted"
AMENDED = "amended"
REJECTED = "rejected"
DECISIONS = (ACCEPTED, AMENDED, REJECTED)

#: 理由分类 **DF-RC-0.1（集团试点口径）**：四组十四条，值就是复核人当场看到的判据文案。
#: 由本项目按"机器目前确实没把握的地方"拟定、产品负责人 2026-10-09 确认为试点口径，
#: **待真实驳回分布校准**。保留这行溯源，是为了企标到手时能分辨哪些格是定的、哪些是抄的。
REASONS: dict[str, str] = {
    # A 组 · 数值正确性（阻断级：出现即说明图纸有错，不是风格分歧）
    "wrong_value": "尺寸数值与模型不符，照它加工就废件",
    "wrong_count": "重复特征的数量或并组错了",
    "wrong_symbol": "⌀/⌴/↧/R 等符号或用词用错",
    # B 组 · 标注取舍
    "missing_dimension": "漏标：该标的尺寸没标",
    "extra_dimension": "多标：冗余或不该标",
    "datum_choice": "基准选错或该从别的面量起",
    "tolerance_missing": "该给专门公差的尺寸没给",
    "chain_open": "尺寸链留空的那一环不对",
    # C 组 · 表达与图面
    "view_choice": "视图数量或剖切位置不对",
    "unreadable_sheet": "图面读不出：字压线、字压字、标注重叠",
    "style_violation": "线型/字高/箭头不合规范",
    # D 组 · 数据与范围
    "title_block_fields": "标题栏字段不对（图号/材料/比例/版本）",
    "not_applicable_part": "这类零件本就不该由工具做",
    "other_needs_text": "以上都不是 —— 必须写清楚是什么",
}

#: 每条理由属于哪一组。A 组是阻断级：出现即说明图纸有错，不是风格分歧。
REASON_GROUPS: dict[str, str] = {
    "wrong_value": "A", "wrong_count": "A", "wrong_symbol": "A",
    "missing_dimension": "B", "extra_dimension": "B", "datum_choice": "B",
    "tolerance_missing": "B", "chain_open": "B",
    "view_choice": "C", "unreadable_sheet": "C", "style_violation": "C",
    "title_block_fields": "D", "not_applicable_part": "D", "other_needs_text": "D",
}

BLOCKING_REASONS = frozenset(k for k, g in REASON_GROUPS.items() if g == "A")

#: 兜底项没有信息量，除非配了说明。
NOTE_REQUIRED_REASONS = frozenset({"other_needs_text"})


class ReviewError(ValueError):
    """输入不合法。调用方把它翻成 400，不要退化成"记一条空的"。"""


def is_reviewable(state: str) -> bool:
    """只有真出了图的才可能被采用。被门拦下与崩掉的任务记"已接受"是假账。"""
    return state == "drawn"


class ReviewLog:
    """append-only 的复核记录。写之前先验，读的时候把坏行数报出来。"""

    def __init__(self, path):
        self._path = pathlib.Path(path)
        # 生效版本提交要在同一把锁内复查冻结并追加审计，不能被迟到的 A 组复核插入。
        self._lock = threading.RLock()
        self.damaged_lines = 0

    def record(self, *, job_id: str, decision: str, reviewer: str,
               reasons=(), note: str = "", draft_sha: str = "",
               effective_sha: str = "", diff=None, token_hint: str = "",
               token_user: str = "", version: int = 0, changes=None,
               reviewed_sha: str = "", reviewed_version: int = 0, client_bound: bool = False,
               submission_id: str = "", submission_fingerprint: str = "", submission_scope: str = "",
               _persist: bool = True) -> dict:
        if not str(job_id).strip():
            raise ReviewError("job 不能为空")
        if decision not in DECISIONS:
            raise ReviewError(f"decision 只认 {DECISIONS}，收到 {decision!r}")
        if not str(reviewer).strip():
            raise ReviewError("reviewer 不能为空（审计链的最小要素是谁）")
        given = tuple(reasons or ())
        unknown = [r for r in given if r not in REASONS]
        if unknown:
            raise ReviewError(f"理由分类里没有 {unknown}，可选：{sorted(REASONS)}")
        if decision == ACCEPTED and given:
            raise ReviewError("直接用不该带理由分类 —— 有意见请写 note，或选「改后采用」")
        if decision == REJECTED and not given:
            raise ReviewError("驳回必须至少给一条 reason —— 不知道为什么驳回，等于没驳回")
        if NOTE_REQUIRED_REASONS & set(given) and not str(note).strip():
            raise ReviewError("选了「以上都不是」就必须写 note，否则这一条没有信息量")

        row = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "job": str(job_id), "decision": decision,
               "reviewer": str(reviewer).strip(), "reasons": list(given),
               "note": str(note or "")}
        # 生效版本要能回答"基于哪一版改的、改出了什么、这是第几版"；没有这些字段时不写空键。
        # `changes` 是逐项明细：事后只查得到"改了几处"而查不到"改了哪一处"，
        # 那份审计就没法复核。
        for key, value in (("draft_sha", draft_sha), ("effective_sha", effective_sha),
                           ("diff", diff), ("changes", changes),
                           ("token_hint", token_hint),
                           ("token_user", token_user), ("version", version)):
            if value:
                row[key] = value
        if client_bound:
            row.update(client_bound=True, reviewed_sha=reviewed_sha, reviewed_version=reviewed_version)
        if submission_id:
            row.update(submission_id=submission_id, submission_fingerprint=submission_fingerprint,
                       submission_scope=submission_scope)
        if _persist: self._append(row)
        return row

    def _append(self, row):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                handle.flush(); os.fsync(handle.fileno())
            fd = os.open(self._path.parent, os.O_RDONLY)
            try: os.fsync(fd)
            finally: os.close(fd)

    def append_once(self, row, operation_id, number):
        """Replay only an exact captured row, with a stable publication identity."""
        captured = {**row, 'publication_id': operation_id, 'publication_entry': number}
        with self._lock:
            rows = self._read(include_answers=True)
            if self.damaged_lines:
                raise ReviewError('审计日志存在损坏行，保留原始字节；核对前不能继续发布或补写')
            existing = [item for item in rows
                        if item.get('publication_id') == operation_id
                        and item.get('publication_entry') == number]
            if existing:
                if len(existing) != 1 or existing[0] != captured:
                    raise ReviewError('发布审计编号对应不同内容，禁止覆盖')
                return existing[0]
            self._append(captured)
        return captured

    def record_answers(self, *, job_id, request_key, reviewer, answers, state,
                       draft_sha, base_version, version=0, effective_sha="", diff=None,
                       token_hint="", token_user="", error="", _persist=True):
        """回答是操作记录，不是接受/改后采用/驳回的审图结论。"""
        if (not job_id or not request_key or not isinstance(reviewer, str) or not reviewer.strip()
                or state not in ("queued", "drawn", "refused", "crashed")):
            raise ReviewError("答案审计缺少任务、请求、复核人或合法状态")
        row = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "job": job_id, "event": "answers", "request_key": request_key,
               "reviewer": reviewer, "answers": [asdict(answer) for answer in answers],
               "state": state, "draft_sha": draft_sha, "base_version": base_version,
               "version": version, "effective_sha": effective_sha, "diff": diff or {},
               "token_hint": token_hint, "token_user": token_user, "error": error}
        if _persist: self._append(row)
        return row

    def _read(self, include_answers=False) -> list[dict]:
        if not self._path.exists():
            return []
        rows: list[dict] = []
        damaged = 0
        with self._lock:
            lines = self._path.read_text(encoding="utf-8").splitlines()
        for line in lines:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                damaged += 1
                continue
            if isinstance(row, dict) and row.get("event") == "answers":
                if not row.get("request_key") or row.get("state") not in ("queued", "drawn", "refused", "crashed"):
                    damaged += 1
                elif include_answers:
                    rows.append(row)
                continue
            if not isinstance(row, dict) or row.get("decision") not in DECISIONS:
                damaged += 1
                continue
            rows.append(row)
        # 手工编辑过这个文件是常态。跳过坏行可以，但悄悄少算比读不出更危险。
        self.damaged_lines = damaged
        return rows

    def for_job(self, job_id: str) -> list[dict]:
        return [row for row in self._read() if row.get("job") == str(job_id)]

    def entries_for_job(self, job_id: str) -> list[dict]:
        """交付包携带审图结论及答案操作，保持原始追加顺序。"""
        return [row for row in self._read(include_answers=True) if row.get("job") == str(job_id)]

    def frozen(self) -> frozenset[str]:
        """被判过 A 组（数值级）错误的任务。这些图不许再标「直接用」。

        「直接用」主张的是"机器底稿可以照它加工"；A 组理由出现的这一刻这句话就已经
        被证伪了，所以冻结只拦 `accepted`，不拦「改后采用」与「驳回」—— 那条通道正是
        修掉错误的地方。冻结**不会因为后来又操作一次而自行解除**：台账里记过的错，
        不该被后续记录洗掉。要解冻得等模型修好、重出一版，而不是在同一张图上再点一下。
        """
        return frozenset(row["job"] for row in self._read()
                         if BLOCKING_REASONS & set(row.get("reasons") or ()))

    def stats(self) -> dict:
        rows = self._read()
        decisions: dict[str, int] = {}
        reasons: dict[str, int] = {}
        diff: dict[str, int] = {}
        blocking = 0
        for row in rows:
            decisions[row["decision"]] = decisions.get(row["decision"], 0) + 1
            for reason in row.get("reasons") or ():
                reasons[reason] = reasons.get(reason, 0) + 1
                if reason in BLOCKING_REASONS:
                    blocking += 1
            for action, count in (row.get("diff") or {}).items():
                diff[action] = diff.get(action, 0) + int(count or 0)
        return {"total": len(rows), "decisions": decisions, "reasons": reasons,
                "blocking": blocking, "diff": diff,
                "damaged_lines": self.damaged_lines}

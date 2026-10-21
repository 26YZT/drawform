"""在副本上验证停服整理和备份恢复，再由新 HTTPS 进程读回版本及冻结状态。"""

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import secrets
import socket
import ssl
import subprocess
import sys
import time
import zipfile
from urllib.error import HTTPError, URLError
from urllib.request import HTTPSHandler, ProxyHandler, Request, build_opener

from drawform.service.maintenance import backup, compact, inspect, restore
from drawform.review import BLOCKING_REASONS
from verify_local import certificate


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def contents(work):
    return {p.relative_to(work).as_posix(): digest(p) for p in work.rglob("*")
            if p.is_file() and p.name != ".service.lock"}


def verify(work, runtime, root):
    work, runtime, root = work.resolve(), runtime.resolve(), root.resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    inspect(work)  # 先拒绝链接/特殊文件，再对指定来源取哈希。
    original = contents(work)
    first_backup, copy = root / "first-backup", root / "copy"
    backup(work, first_backup)
    restore(first_backup, copy)
    assert contents(copy) == original
    before = inspect(copy)
    compacted = compact(copy, root / "original-index.jsonl")
    after = inspect(copy)
    final_backup, restored = root / "final-backup", root / "restored"
    backup(copy, final_backup)
    restore(final_backup, restored)
    expected = contents(copy)
    assert contents(restored) == expected and contents(work) == original
    rows = [json.loads(line) for line in (restored / "jobs.jsonl").read_text().splitlines()]
    if not rows or not any(kind.startswith("amended-") for row in rows for kind, _name in row["files"]):
        raise ValueError("恢复演练需要至少一个带生效版本的合成样件任务")
    review_path = restored / "reviews.jsonl"
    audit = [json.loads(line) for line in review_path.read_text().splitlines() if line.strip()]
    frozen = {row["job"] for row in audit if set(row.get("reasons", ())) & BLOCKING_REASONS}
    cert, key = certificate(root)
    token = secrets.token_urlsafe(32)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    source = Path(__file__).resolve().parents[1] / "src"
    env = {**os.environ, "PYTHONPATH": os.pathsep.join((str(source), str(runtime))), "DRAWFORM_TOKEN": token}
    command = [sys.executable, "-m", "drawform.service.production", "--work", str(restored),
               "--cert", str(cert), "--key", str(key), "--port", str(port)]
    context = ssl.create_default_context(cafile=str(cert))
    client = build_opener(ProxyHandler({}), HTTPSHandler(context=context))
    url = f"https://127.0.0.1:{port}"

    def call(path, body=None, status=200, raw=False, authorized=True):
        headers = {"Authorization": "Bearer " + token} if authorized else {}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        request = Request(url + path, data=data, headers=headers)
        try:
            response = client.open(request, timeout=15)
        except HTTPError as error:
            response = error
        with response:
            content = response.read()
            assert response.status == status, (path, response.status, content[:300])
        return content if raw else json.loads(content)

    process = None
    try:
        with (root / "https-server.log").open("w") as log:
            process = subprocess.Popen(command, env=env, stdout=log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("恢复后的 WSGI 启动失败，见 https-server.log")
            try:
                listing = call("/jobs")
                break
            except (URLError, ConnectionError, TimeoutError):
                time.sleep(.2)
        else:
            raise TimeoutError("恢复后的 WSGI 未在 30 秒内启动")
        children = subprocess.check_output(["pgrep", "-P", str(process.pid)], text=True).split()
        assert len(children) == 1
        call("/jobs", status=401, raw=True, authorized=False)
        assert {r["job_id"] for r in listing["jobs"]} == {r["job"] for r in rows}
        checked_files = 0
        for row in rows:
            job = row["job"]
            status = call(f"/jobs/{job}")
            assert status["state"] == row["state"]
            for kind, name in row["files"]:
                data = call(f"/jobs/{job}/files/{kind}", raw=True)
                assert hashlib.sha256(data).hexdigest() == expected[f"{job}/{name}"]
                checked_files += 1
            history = call(f"/jobs/{job}/review")
            assert history["damaged_lines"] == 0
            assert history["reviews"] == [r for r in audit if r["job"] == job and r.get("event") != "answers"]
            if any(kind.startswith("amended-") for kind, _name in row["files"]):
                with zipfile.ZipFile(io.BytesIO(call(f"/jobs/{job}/package", raw=True))) as package:
                    assert package.testzip() is None
                    bundled = [json.loads(line) for line in package.read(f"{job}/review.jsonl").decode().splitlines()]
                    assert bundled == [r for r in audit if r["job"] == job]
            if job in frozen:
                refused = call(f"/jobs/{job}/review", {"reviewer": "恢复冻结演练（非制图审核）",
                                                       "decision": "accepted"}, status=409)
                assert refused["frozen"] is True
        assert contents(restored) == expected and contents(work) == original
        with socket.create_connection(("127.0.0.1", port), timeout=5) as connection:
            with context.wrap_socket(connection, server_hostname="127.0.0.1") as secure:
                tls_version = secure.version()
        result = {"scope": "local_synthetic_restore_rehearsal", "original_unchanged": True,
                  "index_before": before["index"], "index_after": after["index"],
                  "compaction": compacted, "checked_jobs": len(rows), "checked_files": checked_files,
                  "all_file_hashes_match": True, "audit_unchanged": True, "frozen_jobs_rejected": sorted(frozen),
                  "worker_count": len(children), "tls_version": tls_version,
                  "qualification": "合成样件本机演练，非公司内网验收，非真实件制图审核"}
        (root / "verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
        return result
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()  # 仅本次 Popen 创建的服务。
                process.wait(timeout=5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.work, args.runtime, args.output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

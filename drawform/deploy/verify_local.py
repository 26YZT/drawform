"""用合成 STEP 验证本机 Gunicorn + HTTPS、答案版本及重启恢复；不作真实件验收。"""

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
from urllib.error import HTTPError, URLError
from urllib.request import HTTPSHandler, ProxyHandler, Request, build_opener
import zipfile

from drawform.eval.pilot import create_book, register_case, report


def certificate(root):
    config = root / "openssl.cnf"
    config.write_text("[req]\nprompt=no\ndistinguished_name=dn\nx509_extensions=ext\n"
                      "[dn]\nCN=localhost\n[ext]\nsubjectAltName=DNS:localhost,IP:127.0.0.1\n")
    cert, key = root / "cert.pem", root / "key.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2",
                    "-config", str(config), "-out", str(cert), "-keyout", str(key)],
                   check=True, capture_output=True, umask=0o077)
    return cert, key


def multipart(field, path, extra=None):
    boundary = "drawform-" + secrets.token_hex(16)
    body = bytearray()
    for name, value in (extra or {}).items():
        body += f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
    body += (f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"; filename="sample{path.suffix}"\r\n'
             'Content-Type: application/octet-stream\r\n\r\n').encode()
    body += path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    return bytes(body), {"Content-Type": f"multipart/form-data; boundary={boundary}"}


def verify(step, runtime, root):
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    cert, key = certificate(root)
    token = secrets.token_urlsafe(32)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    source = Path(__file__).resolve().parents[1] / "src"
    env = {**os.environ, "PYTHONPATH": os.pathsep.join((str(source), str(runtime))), "DRAWFORM_TOKEN": token}
    work = root / "jobs"
    command = [sys.executable, "-m", "drawform.service.production", "--work", str(work),
               "--cert", str(cert), "--key", str(key), "--port", str(port)]
    context = ssl.create_default_context(cafile=str(cert))
    client = build_opener(ProxyHandler({}), HTTPSHandler(context=context))
    url = f"https://127.0.0.1:{port}"

    def call(route, data=None, headers=None, status=200, authorized=True, raw=False):
        sent = {"Authorization": f"Bearer {token}"} if authorized else {}
        sent.update(headers or {})
        if isinstance(data, dict):
            data = json.dumps(data).encode()
            sent["Content-Type"] = "application/json"
        request = Request(url + route, data=data, headers=sent)
        try:
            response = client.open(request, timeout=15)
        except HTTPError as error:
            response = error
        with response:
            content = response.read()
            if response.status != status:
                raise AssertionError(f"{route}: HTTP {response.status}, expected {status}: {content[:500]!r}")
        return content if raw else json.loads(content)

    def wait_job(job):
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            current = call(f"/jobs/{job}")
            if current["state"] in ("drawn", "refused", "crashed"):
                if current["state"] != "drawn":
                    raise AssertionError(f"本地演练未出图：{current}")
                return current
            time.sleep(.6)
        raise TimeoutError("本地出图超过 90 秒")

    def start(number):
        log = (root / f"server-{number}.log").open("w")
        process = subprocess.Popen(command, env=env, stdout=log, stderr=subprocess.STDOUT)
        log.close()
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(f"WSGI 启动失败，请查 {root / f'server-{number}.log'}")
                try:
                    call("/jobs")
                    children = subprocess.check_output(["pgrep", "-P", str(process.pid)], text=True).split()
                    if len(children) != 1:
                        raise AssertionError(f"预期一个应用 worker，实际 {len(children)}")
                    return process, children[0]
                except (URLError, ConnectionError, TimeoutError):
                    time.sleep(.2)
            raise TimeoutError("WSGI 未在 30 秒内就绪")
        except BaseException:
            stop(process)
            raise

    def stop(process):
        if process and process.poll() is None:
            process.terminate()
            process.wait(timeout=20)

    process = None
    evidence = {"scope": "local_https_rehearsal", "sample_sha256": hashlib.sha256(step.read_bytes()).hexdigest()}
    try:
        process, first_worker = start(1)
        with context.wrap_socket(socket.socket(), server_hostname="127.0.0.1") as connection:
            connection.settimeout(10)
            connection.connect(("127.0.0.1", port))
            evidence["tls_version"] = connection.version()
        untrusted = build_opener(ProxyHandler({}), HTTPSHandler(context=ssl.create_default_context()))
        try:
            untrusted.open(url + "/", timeout=10)
        except URLError as error:
            if not isinstance(error.reason, ssl.SSLCertVerificationError):
                raise
            evidence["untrusted_certificate_rejected"] = True
        else:
            raise AssertionError("本地证书意外获得了系统信任")
        call("/jobs", status=401, authorized=False, raw=True)
        payload, headers = multipart("step", step)
        job = call("/jobs", payload, headers, status=202)["job_id"]
        wait_job(job)
        original = call(f"/jobs/{job}/files/dxf", raw=True)
        questions = call(f"/jobs/{job}/questions")["questions"]
        if not questions:
            raise AssertionError("请用含问题卡的合成样件演练")
        answers = []
        for question in questions:
            chosen = None
            if question["kind"] == "special_tolerance":
                ids = [option["id"] for option in question["options"]]
                chosen = "H7" if "H7" in ids else ids[0]
            answers.append({"question_id": question["id"], "option_id": chosen,
                            "free_note": "自动演练记录，不是工程师的生产确认"})
        answer_body = {"reviewer": "本地自动演练", "answers": answers}
        call(f"/jobs/{job}/answers", answer_body, status=202)
        first = wait_job(job)
        if first.get("version") != 1:
            raise AssertionError("答案没有形成第 1 版")
        retry = call(f"/jobs/{job}/answers", answer_body, status=202)
        assert retry["version"] == 1
        effective = call(f"/jobs/{job}/effective", raw=True)
        assert call(f"/jobs/{job}/files/dxf", raw=True) == original
        call(f"/jobs/{job}/review", {"reviewer": "本地自动演练", "decision": "accepted", "reasons": [],
                                     "note": "仅测试接口，不构成人工制图审核"}, status=201)
        package = call(f"/jobs/{job}/package", raw=True)
        (root / "delivery.zip").write_bytes(package)
        with zipfile.ZipFile(io.BytesIO(package)) as archive:
            assert archive.testzip() is None
            assert any(name.endswith("review.jsonl") for name in archive.namelist())
        stop(process)
        process, second_worker = start(2)
        restored = call(f"/jobs/{job}")
        assert restored["version"] == 1 and restored["state"] == "drawn"
        assert call(f"/jobs/{job}/effective", raw=True) == effective
        assert call(f"/jobs/{job}/files/dxf", raw=True) == original
        assert call(f"/jobs/{job}/questions")["questions"] == []
        assert call(f"/jobs/{job}/review")["reviews"][0]["decision"] == "accepted"
        book = root / "pilot.json"
        create_book(book, title="本机 HTTPS 自动演练", source="rehearsal")
        register_case(book, work, job, "synthetic-01")
        result = report(book, work)
        (root / "pilot-report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
        assert result["registered"] == 1 and result["first_pass"]["denominator"] == 1
        assert result["rapid_first"]["assessment"] == "threshold_not_set"
        evidence.update(job_id=job, question_count=len(questions), answer_version=restored["version"],
                        worker_pids=[first_worker, second_worker], restart_preserved_files=True,
                        original_unchanged=True, answer_retry_idempotent=True,
                        qualified_pilot_report=True, package_verified=True)
        (root / "verification.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
        return evidence
    finally:
        stop(process)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--step", required=True, type=Path, help="含问题卡的合成 STEP；不以此替代真实件审图")
    parser.add_argument("--runtime", required=True, type=Path, help="安装 Gunicorn 的独立目录")
    parser.add_argument("--output", required=True, type=Path, help="尚不存在的演练输出目录")
    args = parser.parse_args()
    print(json.dumps(verify(args.step.resolve(), args.runtime.resolve(), args.output.resolve()), ensure_ascii=False, indent=2))

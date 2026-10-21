"""本机 HTTPS 演练入口：单 worker、证书必需、工作目录独占。"""

import os
from pathlib import Path
import ssl
import subprocess
import sys

import pytest


@pytest.fixture(scope="module")
def certificate(tmp_path_factory):
    root = tmp_path_factory.mktemp("local-tls")
    config = root / "openssl.cnf"
    config.write_text("[req]\nprompt=no\ndistinguished_name=dn\nx509_extensions=ext\n"
                      "[dn]\nCN=localhost\n[ext]\nsubjectAltName=DNS:localhost,IP:127.0.0.1\n")
    cert, key = root / "cert.pem", root / "key.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                    "-config", str(config), "-out", str(cert), "-keyout", str(key)],
                   check=True, capture_output=True)
    key.chmod(0o600)
    return cert, key


def test_deployment_pins_one_worker_and_loopback_despite_gunicorn_environment(certificate, monkeypatch):
    from drawform.service.production import server_settings
    monkeypatch.setenv("DRAWFORM_TOKEN", "synthetic-test-token")
    monkeypatch.setenv("GUNICORN_CMD_ARGS", "--workers 8 --threads 8 --preload")
    settings = server_settings(*certificate, port=9443)
    assert settings["bind"] == "127.0.0.1:9443"
    assert (settings["workers"], settings["threads"], settings["worker_class"]) == (1, 1, "sync")
    assert settings["preload_app"] is False and settings["reload"] is False
    assert settings["control_socket_disable"] is True


def test_deployment_refuses_a_missing_access_token(certificate, monkeypatch):
    from drawform.service.production import server_settings
    monkeypatch.delenv("DRAWFORM_TOKEN", raising=False)
    with pytest.raises(ValueError, match="DRAWFORM_TOKEN"):
        server_settings(*certificate)


def test_deployment_refuses_missing_or_mismatched_tls_material(certificate, tmp_path, monkeypatch):
    from drawform.service.production import server_settings
    monkeypatch.setenv("DRAWFORM_TOKEN", "synthetic-test-token")
    with pytest.raises((OSError, ValueError)):
        server_settings(tmp_path / "missing.pem", certificate[1])
    bad_key = tmp_path / "bad-key.pem"
    bad_key.write_text("not a key")
    bad_key.chmod(0o600)
    with pytest.raises((ssl.SSLError, ValueError)):
        server_settings(certificate[0], bad_key)


def test_deployment_refuses_a_private_key_readable_by_other_users(certificate, tmp_path, monkeypatch):
    from drawform.service.production import server_settings
    monkeypatch.setenv("DRAWFORM_TOKEN", "synthetic-test-token")
    key = tmp_path / "public-key.pem"
    key.write_bytes(certificate[1].read_bytes())
    key.chmod(0o644)
    with pytest.raises(ValueError, match="私钥"):
        server_settings(certificate[0], key)


@pytest.mark.parametrize("port", [0, 65536, -1, True])
def test_deployment_refuses_invalid_ports(certificate, monkeypatch, port):
    from drawform.service.production import server_settings
    monkeypatch.setenv("DRAWFORM_TOKEN", "synthetic-test-token")
    with pytest.raises(ValueError, match="端口"):
        server_settings(*certificate, port=port)


def test_a_second_process_cannot_own_the_same_job_directory(tmp_path):
    from drawform.service.production import lock_work_directory
    work = tmp_path / "jobs"
    holder = lock_work_directory(work)
    code = ("from drawform.service.production import lock_work_directory; "
            "import sys; lock_work_directory(sys.argv[1])")
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")}
    try:
        second = subprocess.run([sys.executable, "-c", code, str(work)],
                                env=env, capture_output=True, text=True)
        assert second.returncode != 0 and "已被另一个服务占用" in second.stderr
    finally:
        holder.close()
    restarted = subprocess.run([sys.executable, "-c", code, str(work)], env=env, capture_output=True)
    assert restarted.returncode == 0

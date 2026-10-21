"""本机 HTTPS 演练入口：Gunicorn 单应用 worker，OCCT 继续由既有队列串行执行。"""

from __future__ import annotations

import argparse
import fcntl
import os
from pathlib import Path
import ssl
import sys
from contextlib import contextmanager

INCOMPLETE_RESTORE = ".restore-in-progress"


@contextmanager
def automatic_resumption_default():
    """Scope the standard-entry default without changing embedded factory calls."""
    previous=os.environ.get('DRAWFORM_AUTO_RESUME')
    if previous is None:os.environ['DRAWFORM_AUTO_RESUME']='1'
    try:yield
    finally:
        if previous is None:os.environ.pop('DRAWFORM_AUTO_RESUME',None)
        else:os.environ['DRAWFORM_AUTO_RESUME']=previous


def tls_context(config, factory):
    context = factory()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


def worker_exit(server, worker):
    """Gunicorn正常退出/重启时回收本worker拥有的建模子进程。"""
    app=getattr(worker,'wsgi',None)
    model=getattr(app,'extensions',{}).get('drawform_modeling')
    if model is not None:model.stop()


def server_settings(cert, key, *, port=8443):
    if not os.environ.get("DRAWFORM_TOKEN", "").strip():
        raise ValueError("必须先设置 DRAWFORM_TOKEN；令牌不要放进命令行参数")
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("端口必须在 1–65535 内")
    cert, key = Path(cert).resolve(strict=True), Path(key).resolve(strict=True)
    if key.stat().st_mode & 0o077:
        raise ValueError("TLS 私钥只能由当前用户读取，请设置权限为 600")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    return {
        "bind": f"127.0.0.1:{port}", "workers": 1, "threads": 1,
        "worker_class": "sync", "preload_app": False, "reload": False,
        "control_socket_disable": True, "certfile": str(cert), "keyfile": str(key),
        "ssl_context": tls_context, "timeout": 120, "graceful_timeout": 120,
        "accesslog": "-", "errorlog": "-", "umask": 0o077, "worker_exit": worker_exit,
    }


def lock_work_directory(work):
    work = Path(work).resolve()
    work.mkdir(mode=0o700, parents=True, exist_ok=True)
    handle = (work / ".service.lock").open("a")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        raise RuntimeError("工作目录已被另一个服务占用；请先停止该服务，不能并行打开") from None
    marker = work / INCOMPLETE_RESTORE
    if marker.exists() or marker.is_symlink():
        handle.close()
        raise RuntimeError("工作目录的恢复尚未通过校验，不能启动；请从核验过的备份恢复到新目录")
    return handle


def run_server(work, settings):
    from gunicorn.app.base import BaseApplication

    class Server(BaseApplication):
        def load_config(self):
            # 不读取其他任务的 gunicorn.conf.py 或 GUNICORN_CMD_ARGS。
            for name, value in settings.items():
                self.cfg.set(name, value)

        def load(self):
            # 锁和几何库都在 fork 后的应用 worker 中创建，不能预加载再派生。
            lease = lock_work_directory(work)
            try:
                from drawform.service.app import create_app
                with automatic_resumption_default():app = create_app(work)
            except BaseException:
                lease.close()
                raise
            app.extensions["drawform_work_lease"] = lease
            return app

    Server().run()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", required=True, type=Path, help="本次演练的独立任务目录")
    parser.add_argument("--cert", required=True, type=Path)
    parser.add_argument("--key", required=True, type=Path)
    parser.add_argument("--port", type=int, default=8443)
    args = parser.parse_args(argv)
    try:
        settings = server_settings(args.cert, args.key, port=args.port)
        run_server(args.work.resolve(), settings)
    except ModuleNotFoundError as error:
        if error.name != "gunicorn":
            raise
        print("缺少 Gunicorn：请先按 deploy/README.md 安装独立运行依赖", file=sys.stderr)
        return 2
    except (OSError, ValueError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

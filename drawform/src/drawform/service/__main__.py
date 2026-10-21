"""`python -m drawform.service --port 8000` 起服务。

**必须先设 `DRAWFORM_TOKEN` 环境变量**，否则 `create_app` 直接拒绝启动 ——
一个没有任何鉴权、又能让调用方往服务器磁盘写文件的端口，不该是默认状态。
令牌走环境变量而不是命令行参数：命令行会进 shell 历史和进程列表。

用的是 Flask 自带的开发服务器：单进程、无 TLS。正式部署要换成真正的 WSGI 服务器
（gunicorn/waitress）并置于集团已有的反向代理与单点登录之后 —— 那时这个令牌
就成了"代理到服务"这一跳的内部密钥，不是白做。
"""

from __future__ import annotations

import argparse
import pathlib
import signal

from drawform.service.app import create_app
from drawform.service.production import lock_work_directory,automatic_resumption_default


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m drawform.service")
    parser.add_argument("--host", default="127.0.0.1",
                        help="监听地址。默认只听本机，客户内网放开前先想清楚鉴权")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--work", type=pathlib.Path, default=pathlib.Path("out/jobs"),
                        help="任务产物目录（暂无自动清理）")
    args = parser.parse_args(argv)

    # The local entry point owns the same lease as production and maintenance.
    # Hold it before constructing services, including marker and ownership checks.
    with lock_work_directory(args.work) as lease:
        with automatic_resumption_default():app=create_app(args.work)
        app.extensions['drawform_work_lease']=lease
        def terminate(signum,frame):
            raise SystemExit(0)
        signal.signal(signal.SIGTERM,terminate)
        try:app.run(host=args.host, port=args.port)
        finally:
            modeling = app.extensions['drawform_modeling']
            modeling.stop()
            # Native drawing runs in a thread and cannot safely be killed. Keep
            # the workspace lease until it has stopped writing, even on SIGTERM.
            modeling.worker.join()
            modeling.jobs.stop(timeout=None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

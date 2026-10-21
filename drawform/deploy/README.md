# 本机 HTTPS 部署演练

本目录验证 T-22 的 TLS + 生产 WSGI 技术路径。当前只监听 `127.0.0.1`，不对公网或局域网开放。
演练成功不代表公司内网部署，也不代表 T-23 的真实件制图审核。

环境：macOS / Linux、现有 DrawForm Python 环境、OpenSSL、`pgrep`。
生产入口使用 [Gunicorn](https://pypi.org/project/gunicorn/26.2.0/)，配置依据为其[官方设置文档](https://gunicorn.org/reference/settings/)。
本地核验版本固定在 `requirements.txt`；不修改项目的 `pyproject.toml`。

## 1. 安装独立运行依赖

以下命令在源码根 `drawform/` 执行。联网下载前遵守项目的联网授权要求。

```sh
../spike/.venv/bin/python -m pip install --index-url https://pypi.org/simple \
  --target ../spike/p04-wsgi-runtime -r deploy/requirements.txt
```

只安装公开运行依赖，不上传 STEP、图纸、日志或源代码。

## 2. 自动演练

选一个含问题卡的合成 STEP，使用一个尚不存在的输出目录：

```sh
PYTHONPATH=src ../spike/.venv/bin/python deploy/verify_local.py \
  --step /绝对路径/合成样件.step \
  --runtime ../spike/p04-wsgi-runtime \
  --output /绝对路径/新建演练目录
```

脚本会创建短期本机证书、随机访问令牌和独立任务目录，并验证：

- TLS 使用指定证书校验；系统默认信任链拒绝该自签证书。
- 未携带令牌的任务请求返回 401。
- 一个 Gunicorn 应用 worker；既有 OCCT 队列继续串行。
- STEP 上传、问题卡、答案重出图、同答案重试不增版。
- 原始 DXF 不变；交付包可读且包含审计。
- 停止后重新启动，任务、版本、问题状态、复核记录及文件字节仍可读回。
- 生成标为 `rehearsal` 的试点台账和统计结果；缺秒选阈值时不判正常。

结果在 `verification.json`、`pilot-report.json`、`server-1.log`、`server-2.log` 和 `delivery.zip`。
脚本退出时停止它自己启动的服务，不碰其他进程，不安装系统信任证书。
自动脚本的“直接采用”记录仅用于验证接口，不是工程师审核。

## 3. 手动运行

使用演练目录生成的 `cert.pem`、`key.pem`，或自行提供相互匹配的本机证书和私钥。
私钥权限必须为 600。令牌由环境变量提供，不作为命令行参数，也不要写入版本库。

```sh
# 先在当前终端设置 DRAWFORM_TOKEN；不要把真实令牌写进文档。
PYTHONPATH=src:../spike/p04-wsgi-runtime \
  ../spike/.venv/bin/python -m drawform.service.production \
  --work /绝对路径/本机任务目录 \
  --cert /绝对路径/cert.pem --key /绝对路径/key.pem --port 8443
```

此时服务地址为 `https://127.0.0.1:8443`。
自动验收使用显式 CA 文件验证证书，不使用 `curl -k` 或全局忽略证书错误。
浏览器不会自动信任本地证书；正式内网环境应使用 IT 签发、终端已信任的证书。

入口固定一个 sync worker 和一个请求线程，不读取 `GUNICORN_CMD_ARGS` 或其他目录的 Gunicorn 配置。
几何库与任务队列在应用 worker 内载入；不使用 preload。
同一工作目录由进程锁独占，第二个服务会拒绝启动。

## 4. 停止、重启与数据保留

等待队列任务结束后，在启动终端按 Ctrl-C；服务退出后再用同一命令和工作目录启动。
不要使用 HUP 热重载、TTIN 扩 worker 或并行开启两套服务。
若中断正在运行的任务，既有队列会在重启时将其标为 crashed，并要求重提，不冒充出图成功。

保留整个任务目录，包括 `jobs.jsonl`、`reviews.jsonl`、每个任务下的原始 STEP、四类出口和版本文件。
在线索引仍为追加写；已有停服整理、原索引归档、磁盘检查和校验备份恢复命令，见 [MAINTENANCE.md](MAINTENANCE.md)。12 项 A 方案已确定阈值与留存，执行命令见 [PILOT_OPERATIONS.md](PILOT_OPERATIONS.md)。现场只需落实收工时间、维护窗口、独立存储与通知渠道；不再按 200 个结束任务清理。
不要用外部 logrotate 直接截断这份任务索引或覆盖审计。

## 5. 转入真实内网前的缺口

内网地址与端口、IT 证书信任链、访问控制、维护周期/留存和备份恢复仍需按实际环境核验。本机副本整理和 HTTPS 恢复已有验证，不等于公司环境验收。
30 件真实件、留存许可和懂制图的审核人仍由现场提供。
图上继续保留“未经制图审核”；本地 TLS 和接口测试不能替代人的制图结论。

## 6. 冻结提示的浏览器回归

`tests/browser/frozen_notice.mjs` 导出 `verifyFrozenNotice(page, options)`，供 ego-browser 的 Node 脚本调用。
传入已归当前回归任务控制的 Page，以及 `url`、`token`、全新合成样件的 `jobId`、该任务底稿路径 `originalDxf`、
只改一条尺寸显示文字的测试副本路径 `wrongDxf`。只在隔离的本机测试目录执行，不向真实任务回传错误图纸。

该回归通过原生表单分别上传未修改的底稿和错误副本：前者保持可复核，后者必须即时显示冻结并禁用“直接用”，
同时保留回传修正图的入口。测试读取页面状态，不等待再点击一次“直接用”后才出现的 409 提示。

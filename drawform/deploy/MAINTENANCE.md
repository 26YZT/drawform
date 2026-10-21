# 本地工作目录维护

正式试点请优先使用 [PILOT_OPERATIONS.md](PILOT_OPERATIONS.md) 的 `backup --book`，将外置审图台账一起备份。本页通用目录备份只覆盖指定工作目录。

适用于 macOS/Linux，使用现有 Python 环境，不添加服务、数据库或依赖。
下列命令在 `drawform/` 执行；原始 STEP、四件套、全部生效版本和 `reviews.jsonl` 均在工作目录中。

## 检查磁盘与索引

```sh
PYTHONPATH=src ../spike/.venv/bin/python -m drawform.service.maintenance inspect \
  --work /绝对路径/工作目录
```

返回当前磁盘总量/已用/剩余字节、目录文件量、索引行数/任务数、损坏行号、缺失文件，以及恢复是否中断。缺失文件检查包含仍有目录的任务来源、原图、回传图和答案版本；已清理整个目录的过期任务单列为 stale_jobs。
这是读取时的观察，运行中的追加可能造成短暂不一致；操作前停服再复查。
本命令不改文件，也不自行设定报警百分比或周期。报警阈值、周期和留存已按 12 项 A 方案确认；带调度与台账的试点操作见 [PILOT_OPERATIONS.md](PILOT_OPERATIONS.md)。

## 停服后整理索引

**先停止使用该工作目录的全部服务。** 命令复用 Gunicorn 入口的排他工作锁；Flask 开发入口没有该锁，不能靠维护命令替它停服。
不要热重载或另开 worker。命令不会停止任何进程。

```sh
PYTHONPATH=src ../spike/.venv/bin/python -m drawform.service.maintenance compact \
  --work /绝对路径/工作目录 \
  --archive /绝对路径/归档目录/整理前索引.jsonl
```

归档上级目录须存在，文件须不存在，而且必须在工作目录之外。工具先将原始索引逐字节归档并刷盘，再用临时文件原子替换索引。
每个仍有任务目录的任务保留最后一行，按原任务顺序保存；已经按保留策略清掉目录的旧任务不复活。
任务目录、图纸、答案版本与 `reviews.jsonl` 不删不改。queued/running 状态不冒充 drawn，重启由原队列标为 interrupted/crashed。

索引损坏、非法路径、活动工作锁、归档路径已存在、整理期间索引被改动时拒绝操作；不跳过坏行来凑一个成功结果。

## 备份和恢复

备份前也须停服。输出路径和恢复路径的上级目录须存在，目标必须是新路径。

```sh
PYTHONPATH=src ../spike/.venv/bin/python -m drawform.service.maintenance backup \
  --work /绝对路径/工作目录 --output /绝对路径/备份目录

PYTHONPATH=src ../spike/.venv/bin/python -m drawform.service.maintenance restore \
  --backup /绝对路径/备份目录 --work /绝对路径/新工作目录
```

备份包含 `data/` 与 `manifest.json`，逐文件记录大小和 SHA-256；不复制运行锁。目录权限 700，文件权限 600。
工具拒绝符号链接、特殊文件、路径越界、重复路径与不在清单中的额外文件；不会跟随链接读取目录外内容。
恢复先核验完整清单、文件字节、索引和审计，并确认有效任务引用的文件都在，再写入新工作目录；不覆盖现有目录。
原本缺文件的工作目录可原样备份留证，但不能恢复为可运行目录；工具报告缺失路径，不补造图纸或丢弃版本。
这些校验用于检查副本完整性，不能代替备份来源的可信确认。

恢复期间保留 `.restore-in-progress`，只有全部复制和校验成功才移除。恢复中断后，生产入口拒绝启动该目录。
不要手工删除标记来绕过校验；保留失败目录排查，从核验过的备份恢复到另一个新路径。
备份复制中断时不会产生有效清单，不能把部分目录当成可恢复备份。

`compact` 的索引归档用于保留整理前的证据；完整恢复应使用整目录备份，不能只拷一份 jobs.jsonl 而漏掉答案文件或审计。
保留策略见已确认 A 方案；到期只列人工复核清单，本工具不自动删除任何历史备份。
上面的通用目录备份不会自动发现外置台账。完整试点证据必须使用 PILOT_OPERATIONS.md 的 backup --book，将台账和源文件一起校验备份。

## 实际恢复演练

```sh
PYTHONPATH=src ../spike/.venv/bin/python deploy/verify_maintenance.py \
  --work /绝对路径/已停服的合成样件工作目录 \
  --runtime ../spike/p04-wsgi-runtime \
  --output /绝对路径/新的恢复演练目录
```

演练只整理恢复出来的副本，原目录的业务文件不变。副本完成索引整理、再次备份恢复后，启动一个 Gunicorn 应用 worker 的本地 HTTPS 服务，逐文件读回 SHA-256、复核审计和数值冻结状态，随后停止自己启动的进程。
证书仅由本次客户端显式信任，不安装系统证书、不忽略校验、不对外网/局域网开放。
这不代替公司环境的恢复验收、断电演练、30 件真实件或人工制图审核。

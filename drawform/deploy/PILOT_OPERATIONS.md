# 已确认的试点运维命令

依据：[12 项 A 方案](../../docs/pilot/P0-12项A方案-执行口径.md)。以下命令从 `drawform/` 运行。
现有全部数据保留；这些命令没有删除、重启或停止他人进程的动作。

## 启动本地监测

先在终端环境设置 `DRAWFORM_TOKEN`，不把令牌放在命令参数或文档里。

```sh
PYTHONPATH=src ../spike/.venv/bin/python -m drawform.service.operations watch \
  --work /绝对路径/业务目录 \
  --state /绝对路径/运维目录/state.json \
  --url https://127.0.0.1:8443 \
  --ca /绝对路径/客户端信任证书.pem \
  --pid 实际服务进程号
```

`check` 子命令只采样一次，参数相同。`watch` 每秒观察进程和任务异常，HTTP 每 60 秒、磁盘和在线完整性每 300 秒采样；只有新事件或到提醒时间才输出。
PID 须是部署方实际启动的服务 PID；重启服务后更新监测命令里的 PID。未提供 PID 时只能检测 HTTP 和任务状态，不能宣称已覆盖进程退出监测。

状态与按 UTC 日分段的 `state.events.YYYY-MM-DD.log` 放在业务目录之外，权限 600；状态跨运行保留，避免每次执行重复首报。另一个监测实例使用同一状态路径会被文件锁拒绝。状态损坏时退出非零，不静默清零告警。
HTTPS 校验证书并携带鉴权，不忽略 TLS 错误。未接通知平台；事件只写本地文件和标准输出，供现场接入批准的通知渠道。

计划维护时带 `--maintenance-until 2026-10-14T20:30:00+08:00`，换成实际批准的截止时间。它只抑制停服引起的服务告警，不抑制磁盘、数据损坏等告警，也不伪造恢复事件。
普通预警首次通知、4 小时后才重复；严重首次通知、30 分钟后重复；观察到恢复通知一次。

人工处置可留痕：

```sh
PYTHONPATH=src ../spike/.venv/bin/python -m drawform.service.operations acknowledge \
  --state /绝对路径/运维目录/state.json --incident 实际告警标识 \
  --operator 实际处置人 --note 实际处置说明
```

处置登记不改任务、审图或冻结状态，也不会停止仍未恢复问题的周期提醒。

## 每个试点工作日收工后备份

先等待任务结束并按部署规程停服，再执行；工具不会替人停服。
服务仍持工作锁时备份失败并记录告警。台账写入也须停止，复制期间台账变化会中止备份。

```sh
PYTHONPATH=src ../spike/.venv/bin/python -m drawform.service.operations backup \
  --source /绝对路径/业务目录 --book /绝对路径/试点台账.json \
  --target /绝对路径/备份目录/daily/本次唯一标识 \
  --state /绝对路径/运维目录/state.json
```

`--book` 必填：外置台账会被原样复制为备份 data/ 下的 `.drawform-pilot-ledger.json`，一起进入 SHA-256 清单；原台账不移动、不修改。
台账中每件 STEP 的指纹须与副本一致；即使文件清单哈希相符，关联指纹不符也拒绝恢复。
只有没有生产数据的早期通用存储演练才可单独使用 MAINTENANCE.md 的目录备份；它不能冒充带台账的完整试点备份。

每周同样备份到 `weekly/本次唯一标识`。升级或索引整理前备份到 `before-change/本次唯一标识`。
完整试点归档复制到批准的独立存储 `archive/批次标识`，不得只在同一块磁盘换一个文件夹就称为独立备份。
上述目标上级目录先由部署方建立；目标本身必须不存在。

## 校验与每周恢复演练

```sh
PYTHONPATH=src ../spike/.venv/bin/python -m drawform.service.operations verify \
  --source /绝对路径/完整试点备份 --state /绝对路径/运维目录/state.json

PYTHONPATH=src ../spike/.venv/bin/python -m drawform.service.operations restore-check \
  --source /绝对路径/完整试点备份 --target /绝对路径/全新恢复目录 \
  --state /绝对路径/运维目录/state.json
```

验证失败立即写严重事件。恢复只写新目录；返回 `restored_book` 指向恢复后的台账，不覆盖原台账。
命令核对文件、索引、审计和台账关联，不自动启动业务。完整端到端演练还须用新目录启动单 worker HTTPS 服务，核对版本、下载和冻结行为；现有 `verify_maintenance.py` 可用于有回传版本的合成样件演练。

## 留存到期只列清单

```sh
PYTHONPATH=src ../spike/.venv/bin/python -m drawform.service.retention close \
  --book /绝对路径/试点台账.json --ended-at 实际结束时间含时区 --operator 实际登记人

PYTHONPATH=src ../spike/.venv/bin/python -m drawform.service.retention evidence \
  --book /绝对路径/试点台账.json --work /绝对路径/业务目录

PYTHONPATH=src ../spike/.venv/bin/python -m drawform.service.retention backups \
  --root /绝对路径/备份目录

PYTHONPATH=src ../spike/.venv/bin/python -m drawform.service.retention logs \
  --folder /绝对路径/普通运行日志目录 --work /绝对路径/业务目录
```

结束登记不等于制图合格签字，且不能覆盖已登记的结束时间。未登记结束时，证据一直受保护；180 天到期仍只报告需人工复核，不会删除。
备份清单保留最近 7 日份、4 周份，其余列入人工复核；before-change 与 archive 单列保护。
监测事件日志自动按日分文件，不截断旧文件；Gunicorn 的普通 stdout/stderr 日志由现场进程管理器按日滚动，不能把业务索引或审计放进 logrotate。普通 `*.log` 文件最后写入超过 30 天列入人工复核，业务目录及其父子目录不得作为普通日志目录。

现场还需落实：实际收工时间和工作日安排、计划维护窗口、通知接收渠道与责任人、独立备份路径及容量。当前没有替现场猜测这些参数或安装生产定时任务。

## 本机综合演练

只传入已经停服的合成样件工作目录，要求其中包含回传版本以验证冻结行为：

```sh
PYTHONPATH=src ../spike/.venv/bin/python deploy/verify_policy_a.py \
  --work /绝对路径/合成任务目录 --runtime ../spike/p04-wsgi-runtime \
  --output /绝对路径/新的综合演练目录
```

脚本创建 rehearsal 台账，只在副本验证完整备份、台账指纹、HTTPS文件读回、真实进程启停和恢复通知。三次失败采样使用推进的测试时钟，不能将结果报告为实际三分钟停机 SLA。脚本结束会停止自己启动的服务。

## 2026-10-15 工作台显示名配置

本机服务沿用 `drawform.service.production`、1个sync应用worker、1个串行几何worker。启动时额外设置 `DRAWFORM_SOURCE_NAMES` 为 `开发/P0.4真实试点/公开样本文件名索引.json` 的绝对路径；这是已核对SHA256到显示名的映射，不含令牌，不参与任务存储路径。旧任务仅在源文件实际SHA匹配时显示目录名，读取不会迁移索引。新上传自身持久化原显示名、上传时间和来源SHA。

切换代码前等待无queued/running任务，停止明确归属的进程，保存业务目录恢复副本，再启动。重新启动会按V1既有流程核验等待参考任务。启动后核对任务/图件/实际审核数量并更新启动信息中的PID。令牌仍由现有私有文件注入环境，不写进命令参数。正式日备份与独立介质规则保持。

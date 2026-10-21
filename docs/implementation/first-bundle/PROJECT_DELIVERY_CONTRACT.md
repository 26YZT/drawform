# 项目有效版本、统一交付与恢复契约

2026-10-18 用户授权继续首包v2。本段在已有单零件模型族上收口项目生命周期，不扩大建模特征。

## 权威与状态

- 项目ID等于模型族 root_session_id，owner保持原令牌座位SHA。latest_number为最新分配候选，不是有效版本。历史 adopted 只表示参数采用，不代表项目有效或工程人审通过。
- 项目初始 head_revision=0、effective=null。新候选、新出图和恢复均不自动推进HEAD。用户在模型及出图完成后显式“设为项目有效版本”。
- POST /api/workbench/v1/projects/<root>/effective：Idempotency-Key；JSON严格为 session_id、expected_head_revision、expected_step_sha256。仅同族 adopted 模型、drawn且来源STEP一致、完整四出口可发布。CAS旧revision；同键同体重试返回同一回执，不重复推进，异体409。
- SQLite project_publications保留 prepared→captured→complete/failed/interrupted 阶段。先持久化快照全部文件和manifest，再在一个短事务提交HEAD及回执。异常/重启的未提交操作保留证据且不推进HEAD；同键重试可重新核查执行。提交后丢响应重试只返回原回执。
- GET /projects/<root> 返回head_revision、effective（session_id/job_id/STEP SHA/model_version/snapshot_id/manifest_sha256）及快照完整性；GET /projects 返回当前owner模型族。会话GET附project概览。

## 一致交付

- 出图发布只采集同一Job的稳定四出口。模型STEP、Job实际来源STEP及source_sha256必须相同；每文件记录相对路径、SHA256、bytes、role、session/job/模型版与图纸版。
- 快照含原输入、模型族各版本的来源/澄清/修改/模型检查与产物、关联Job全部历史图件、源文件、审计及项目记录。采用当前四出口时明确其drawing edition。手工DXF若没有配套四出口则拒绝设为一致项目有效版本，不能把旧PDF冒充更新图；历史文件保留。
- 历史文件/版本槽缺失、重复、路径越界、链接、SHA失配或来源不一致均阻断发布，不删除历史或重编号。已有legacy入口保持；本段项目发布不会替代工程审图。
- GET /projects/<root>/package?revision=N 下载指定不可变快照zip；省略N为当前HEAD。manifest.json逐文件可验证，工程人审状态不升级。GET /projects/<root>/publications 列阶段/结果。
- python -m drawform.workbench.project_package <zip> 在独立进程校验封包、路径、大小、摘要、STEP绑定及四出口版本。交付包用于交付/核对；完整可运行恢复使用下述工作区备份，保留SQLite/全部历史/幂等回执。

## 完整恢复与故障边界

- 沿用maintenance backup/restore到全新目录，停服工作锁、逐文件摘要、拒绝链接、恢复中标记、不可覆盖。标准本地服务入口也持有工作目录锁；恢复中标记在所有入口启动前检查。
- 新增只读workbench语义校验：SQLite integrity/foreign_key、输入摘要、session字段、父子版本及参数、源预览、模型产物、STEP↔Job来源、HEAD↔快照↔journal、提交回执；缺失不按成功恢复。兼容没有workbench的旧备份。
- 排队/生成等中断记录保留为interrupted，不自动重新建模或采用；出图受理窗口借原submit_once稳定ID恢复，已有completed任务不重复生成。无法确认的阶段保留记录、明确失败，原有效快照保持。
- 验收必须包含真实文字与图纸模型、两个版本切换、旧HEAD冲突、重复请求、丢响应/文件故障/中断、交付独立验证、停服备份到新目录、新进程恢复及继续修改出图。技术采用不代替人工工程验收。

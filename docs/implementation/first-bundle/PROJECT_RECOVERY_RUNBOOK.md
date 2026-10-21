# 项目版本、交付与恢复操作

本段使用现有简单零件模型族作为项目，不增加建模特征。模型“采用”、项目“有效版本”、工程“人工审图”是三个不同状态。

## 日常使用

1. 在工作台生成模型、补齐缺失条件，核对后采用，生成二维工程图。
2. 查看“项目有效版本与交付”：左侧正在查看的模型可能是历史版，不一定是有效版。
3. 勾选确认，再点击“设为项目有效版本”。服务器核对同一STEP、图纸版次和完整四出口，成功后保存不可变快照。
4. 下载按钮绑定显示的项目修订号。之后生成新模型或新图，不会改变这份交付包。
5. 如果提示版本冲突，先刷新比较；需要继续原操作时，明确更新项目依据并重新确认，不自动覆盖。
6. “已保存项目”列表可恢复项目；默认打开有效模型，尚未发布时打开初始模型。具体历史模型仍通过模型版本列表查看。

工程图只有人工修改DXF而没有同版PDF/SVG/JSON时，本段拒绝发布一致项目套件。历史DXF和原底稿继续保留；不能用旧PDF冒充新图。

## 核验下载包

交付包包含原输入、澄清回执、模型修改来源、历史模型及检查、关联图纸、审计和逐文件清单。它是交付快照；完整可运行恢复使用全工作区备份。

在正式根的 `drawform/` 目录运行，路径替换为自己的下载文件：

```sh
PYTHONPATH=src ../spike/.venv/bin/python -m drawform.workbench.project_package /absolute/path/project.zip
```

仅 `verified: true` 表示封包、摘要及版本关系校验通过，不代表工程审图通过。校验不会解压或执行包里的内容。

## 全工作区备份与恢复

先正常停止对应服务；标准本地和生产入口持有工作目录锁，活跃服务不能备份。不要强行移除锁文件或恢复中标记。现有不同端口的旧服务各自使用不同数据目录。

在 `drawform/` 目录运行：

```sh
PYTHONPATH=src ../spike/.venv/bin/python -m drawform.service.maintenance backup --work /absolute/path/stopped-work --output /absolute/path/new-backup
PYTHONPATH=src ../spike/.venv/bin/python -m drawform.service.maintenance restore --backup /absolute/path/new-backup --work /absolute/path/new-restored-work
```

备份和恢复目标都必须是不存在的新路径，且上级目录存在。源目录、原备份和失败的恢复目录均保留；失败后核对原因，改用另一个新目录重试。不会清理或覆盖用户文件。

恢复验证会检查SQLite、源输入、澄清、采用回执、父子版本、模型产物、出图来源和封存记录、项目HEAD及发布回执；不是只比较复制出来的文件大小。

只有文件复制、逐文件摘要和关系校验全部完成后，恢复中标记才移除。所有应用入口都会拒绝启动带该标记的目录。

启动恢复副本时使用原有访问座位令牌，通过环境变量设置 `DRAWFORM_TOKEN`，不要放到命令行、URL或交付文件中。所有权保持原值，不按恢复操作者静默重绑。随后可用标准入口：

```sh
PYTHONPATH=src ../spike/.venv/bin/python -m drawform.service --port 8452 --work /absolute/path/new-restored-work
```

## 中断后的行为

- 未提交的项目发布阶段保留为interrupted，原有效版本保持；明确重试同一请求会重新校验和捕获，不重复提交已完成的请求。
- 已完成模型和图纸保持原版本；待澄清任务继续等待原问题答案。
- 标准入口现在默认启用受托管任务自动续算：同会话新attempt，原尝试和设计版本号保持，最多3次自动恢复。仅服务中断可续算；取消/超时/失败/来源漂移/协议变化/超限阻断。不自动采用、不回答澄清、不切换项目HEAD。旧无冻结意图的中断记录保持，不猜测复活。
- 受理过的出图使用稳定Job ID；构造旧Queue仍按原crashed语义读回，标准入口启动后按新五阶段日志恢复托管操作。图前参考、图后答案、人工DXF及明确复核同门；只有原始明确审图请求可以补写对应结论。
- 发现历史图纸缺件时，保留版本槽并拒绝继续写入；发现未登记的同名图纸文件时同样拒绝，不猜测重编号或覆盖。

本轮验证包含真实进程强制退出、恢复到新目录与新进程继续建模；未做物理断电或实际CAD软件/打印/人工工程验收。

2026-10-18 自动续算实现以AUTO_RESUME_JOURNAL_CONTRACT.md为准。服务能力接口automatic_resume反映真实配置；嵌入使用create_app(..., auto_resume=True)，标准本地/生产入口默认启用，DRAWFORM_AUTO_RESUME=0可明确关闭。日志位于legacy-publications/<operation_id>/intent.json及独占顺序events，模型尝试位于会话attempts目录；不得手工删除以绕过核验。

# 公开 STEP 的本机 HTTPS 技术验证

这是独立技术验证，审核状态只作模拟。真实件、人工制图审核、生产放行和实际一次通过率不从本流程产生。

本批清单：`out/public-step-technical-20261014/corpus/catalog.json`。
来源为 FreeCAD Parts Library 固定提交；资产许可、作者、输入 SHA-256、配套源文件许可属性和排除理由都在清单及同目录 ATTRIBUTION.md 中。
不按出图成功与否换样；许可声明不一致的候选在出图前排除，不猜测哪份声明优先。

## 复现

在 `drawform/` 执行，输出目录必须尚不存在：

```sh
PYTHONPATH=src ../spike/.venv/bin/python deploy/verify_public_step.py \
  --catalog out/public-step-technical-20261014/corpus/catalog.json \
  --runtime ../spike/p04-wsgi-runtime \
  --output /绝对路径/新的公开STEP验证目录
```

运行前不要同时执行其他 OCCT 测试或修改引擎源码。
脚本先检查许可证据、输入指纹、固定提交、署名和选样锁定；不能把 `human_review` 或 `production_release` 设为 true。

随后启动只绑定 127.0.0.1 的 Gunicorn 单应用 worker HTTPS 服务。客户端显式信任本次证书，测试未鉴权拒绝及系统不信任自签证书；不安装系统证书，也不忽略 TLS 校验。

每件串行上传、出图；可出图样件再以独立任务重复上传，核对问题与候选 ID。有效单实体才进入答案与模拟采用流程：

- 每件仅在引擎候选确有 H7 时选择一次 H7，其余明确选择不知道。这是演练策略，不是公差推荐。
- 验证带自由数值/非候选 ID 的回答被拒绝、同答案重试不增版、底稿字节及尺寸约束值不被答案改写。
- 模拟审核人固定为“公开STEP技术验证：模拟审核，无人工签字”；note 明确保存 `simulated_acceptance=true; human_review=false; production_release=false`。
- 导出包内验证相同审计标记；无生效版本的样件按现有接口契约不能导出交付包，不能伪造一个版本。
- 重启新服务进程，从磁盘读回文件与模拟审计。

被引擎拒绝或崩溃的样件不能硬记 accepted，脚本验证复核端点仍拒绝它们。多实体即使引擎能出图，也单列边界结果，不按单零件模拟采用。
发生超时后停止继续提交，保留记录。脚本退出停止的仅是自己创建的服务进程组。

## 结果含义

`simulated_accepted` 只表示上述状态和文件流程走通，不是几何语义正确、图纸可读或制造标准符合性结论。
`engine_refused`、`engine_crashed`、`boundary_drawn_not_accepted`、验证器 `error` 分开报告。`true_human_approved=0`、`real_parts_first_pass_rate=null`、`production_release=false` 固定保留。

本次单件耗时包含重复上传、答案重绘、下载与审计等动作，不是一次 `render_part` 的性能指标。
PDF 的可打开性与只读视觉观察另外记录；不要把接口成功替代图纸质量检查。原始有问题输出保留作复现证据，不修饰成合格图纸。

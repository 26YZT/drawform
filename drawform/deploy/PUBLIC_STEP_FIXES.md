# 公开 STEP 三项修复的复现

范围：多实体拒绝、标注布局与重复/标题栏、未经真实审核的图面标识。数据仍来自上一批锁定的 11 件公开模型，不替代真实件试点或专业制图审核。

## 运行

在 `drawform/` 中执行，`pdftotext` 须位于 PATH，输出目录须不存在：

```sh
PYTHONPATH=src ../spike/.venv/bin/python deploy/regress_public_step.py \
  --catalog out/public-step-technical-20261014/corpus/catalog.json \
  --runtime ../spike/p04-wsgi-runtime \
  --baseline out/public-step-technical-20261014/https-run \
  --output /绝对路径/新的回归目录

PYTHONPATH=src ../spike/.venv/bin/python -m pytest tests/ -q
```

当前 Codex 本机的 `pdftotext` 位于 `/Users/mima1234/.cache/codex-runtimes/codex-primary-runtime/dependencies/native/poppler/poppler/bin/`；可仅在本次命令的 PATH 中加入这个目录。应用依赖文件不变。

回归期间不并行运行另一份 OCCT 测试，不修改引擎源码。脚本只启动并停止自己创建的本机 HTTPS 服务；不清理现有任务或旧证据。鉴权、证书校验、答案重试、导出及重启读回沿用 `verify_public_step.py`。

## 判据

- 两件多实体 STEP 在投影前返回 `multiple_solids`，提示拆成单体逐件导出。一个实体包含多个孔、面或轮廓仍按单体处理。
- 原有 8 件可出图样件继续走通模拟流程；原有型材 `sheet_overflow` 单独保留，不能算出图成功。
- 对照冻结的旧 `original.json/current.json`，逐来源严格比较约束值、数量、尺寸原始值和测量端点。原生 DXF 测量值也逐来源比较，绝对容差为 1e-9 mm，用于浮点坐标运算误差比较，不是制造公差。
- SVG 文字框不堆叠；回答前后基准字母都在方框内。DXF 保留原生 DIMENSION 并通过审计。PDF 实际文本、SVG、DXF 均包含“未经制图审核”。
- 自动模拟 accepted、填写审核人字符串或导出包都不会撤掉未审提示。真实审核通过数固定为 0。
- PDF 另用 `pdftoppm` 渲染作实图检查；协议与字框检查不能代替制图审核。

## 共享标注的数据含义

约束清单不去重、不改值。尺寸只在测量种类、方向、几何、显示文字、数量及公差相符时共享。孔与轴、不同公差、不同定位区间不会因显示数字相同而合并。

共线孔位的匹配指纹保留毫米坐标小数点后 12 位，消除 1e-15 量级的算术尾差。这个步骤只用于分组，不改写原数值或端点。JSON 的每条尺寸除代表 `source/value/p1/p2` 外，按相同索引记录：

- `shared_sources`：共享标注的其他约束来源；
- `shared_values`：各来源原始双精度值；
- `shared_witnesses`：各来源原始测量端点。

三列长度必须相同。交付门按代表来源与共享来源的并集检查漏标；回归将它们展开后与旧结果逐条比较。选择一个来源的专门公差后，如果与其他来源不同，就分开显示，不能把该公差传播到未选择的来源。

## 既有测试的授权例外

本批只调整 `tests/test_place.py::test_hole_positions_are_drawn_as_linear_dimensions`：原断言要求定位标注数量等于约束数量，会强制重复绘制共线孔位；新断言核对全部来源、数值与实际端点覆盖。用户已明确回复“允许，且旧测试不得影响开发”。其余原有测试及 strict xfail 不改。

最终批次记录、逐文件行数及验证日志位于 `out/public-step-fixes-20261014/`，完整解释见 `../docs/validation/P0-公开STEP三项修复-20261014.md`。

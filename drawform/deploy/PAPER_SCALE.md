# 图面比例、斜孔与慢任务核对

本轮代码继续经 `drawform.pipeline.render_part` 出图。比例是引擎选择的纸面表达，不是新的用户数值输入字段。设计依据和当前限制见 [paper-scale.md](../../docs/design/paper-scale.md)，实测证据见[本轮记录](../../docs/validation/P0-图面比例与斜孔落位-20261014.md)。

## 本地回归

在 `drawform/` 下使用既有 Python 环境；不修改 `pyproject.toml` 或全局依赖。

```bash
PYTHONPATH=src ../spike/.venv/bin/python -m pytest tests/test_paper_scale.py tests/test_oblique_bore_placement.py tests/test_slow_polling.py -q
PYTHONPATH=src ../spike/.venv/bin/python -m pytest tests/ -q
```

三个新增测试文件共有 11 个通过项。慢任务测试直接执行页面实际的 `poll`，需要当前已有的 Node.js；没有 Node 时该文件会显式跳过，不能把跳过称为通过。NIST 几何夹具出处和原始 SHA-256 见 [NIST_SOURCE.md](../tests/fixtures/NIST_SOURCE.md)。

## 逐件查看

1. 查看 JSON 的 `drawing_scale` 与 `scale_label`，并核对标题栏。约束值和测量端点仍为真实模型毫米。
2. 查看 SVG/PDF：字高、箭头和基准框不随零件比例缩小，三出口仍写“未经制图审核”。
3. 在 CAD 中打开 DXF 时查看图纸空间的 A3/A4 视口；模型空间实体保持真实毫米。测量原生 DIMENSION，不能拿纸面线段长度直接当公称尺寸。
4. `sheet_overflow` 提示标注空间不够时，保留拒绝结果并安排人工布局/分图；本轮不会自动删尺寸、缩字或生成局部放大。
5. 页面等待 90 秒后继续查状态，显示已等待时间；任务终态自动更新。单个请求仍有 15 秒超时，网络错误应按页面提示处理。

真实 AutoCAD 打开/打印、工程师逐件审核和正式真实件基线仍需现场证据。当前本机 HTTPS 证书的有效期与令牌位置见[试点说明](../../../开发/P0.4真实试点/README.md)；令牌和私钥不得加入审阅包。

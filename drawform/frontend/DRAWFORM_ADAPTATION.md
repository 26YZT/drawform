# DrawForm 前端适配

2026-10-18 后续更新：界面品牌/导航/介绍中文化，四入口改为功能块；标题采用用户截图指定的猫啃网故障黑，正文与数值保持系统易读字体。字体本地嵌入，OFL/版权/源版本与摘要在public/assets/fonts/maoken。原index.css仅替换字体导入与根字体栈，其余原版规则继续按摘要保护。当前演示仍为8455，同一原工作目录；详见仓库docs/validation/DrawForm-中文故障黑与功能块验证-20261018.md。下面为首轮适配时点记录。

此目录是 VEYRA v1.0.0 的 **DrawForm 适配版**，不是原版逐像素复刻声明。未改动的原版另存于 `开发/veyra-reference-v1.0.0`，本机预览端口 5220。

来源：https://github.com/amirmushichge/veyra-interactive-car/tree/v1.0.0

固定提交：`5779ec0762559b84bd5da3b6ffccf76e747f7587`。保留原 MIT LICENSE、THIRD_PARTY_NOTICES.md、18份媒体、HoverVideo.tsx、index.css、锁文件及参考manifest。字体仍沿用原版 Google Fonts 导入；离线时使用本地系统字体。

## 功能接线

| 入口 | 链接 | 实际承载 |
| --- | --- | --- |
| 3D转2D | `/?tool=step` | 原STEP上传、图前确认、审图、版本、回传与下载 |
| 文字建模 | `/workbench/?mode=text` | 原文字输入、尺寸澄清、模型采用、STEP与2D |
| 图纸建模 | `/workbench/?mode=drawing` | 原DXF/PDF/图片输入与建模流程 |
| 项目与交付 | `/workbench/?view=projects` | 原项目列表、有效版本、统一交付包 |

`/?job=...&version=...` 和 `/workbench/?session=...` 保留。任何有效session优先于mode/view。首页四个底部入口和顶部3D转2D链接无需等待媒体准备即可使用。

首页车与剖面仅为交互示意。真实CAD模型、尺寸、版本和文件在工作区查看。全工作区备份/恢复仍使用本地工具，网页不伪造ZIP恢复功能。两处外观菜单已改为业务说明和工作区链接，不再选择车漆或轮毂；原版相关选择功能仍可在5220查看。

## 构建与本地服务

使用Node 22+、pnpm 10.15.1；没有增加应用依赖。

```sh
npx --yes pnpm@10.15.1 install --frozen-lockfile
npx --yes pnpm@10.15.1 test
npx --yes pnpm@10.15.1 verify:adaptation
npx --yes pnpm@10.15.1 build
```

Flask读取 `DRAWFORM_FRONTEND_DIST` 指向本目录的绝对 `dist` 路径；该服务的根首页和两个工作区将启用新界面。继续使用现有 `DRAWFORM_TOKEN` 与 `--work` 配置。没有此环境变量时保留旧界面，便于基线回放。构建目录缺失会拒绝启动，不悄悄展示空页。

Vite单独运行只能预览场景，业务链接需要同源Flask服务。当前完整适配演示为 http://127.0.0.1:8455/ ，使用从既有验证备份恢复的独立工作目录；原8454与其历史数据不动。

## 校验边界

`verify:reference` 原命令与manifest均保留；在适配版上会报告四个刻意修改的展示文件。不能重新生成manifest把适配伪装成原版。新增 `verify:adaptation` 固定manifest摘要，校验另外28项原版参考文件。

`test`保留原HoverVideo检查与4个离线媒体工具mock，使用新workflow检查替代与适配不再相符的色板/轮毂检查。原 `production/test-appearance.cjs` 仍原样保存；原版运行它并通过。适配检查涵盖入口、neutral gate、互斥、关闭锁、异步焦点重放与明确点击重开；实际布局/导航另经浏览器验证。

建模页已提交任务可恢复；尚未提交的澄清/参数输入仍有原先的页面内草稿限制。不要声称本轮新增跨页面草稿恢复。

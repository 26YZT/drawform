# DrawForm / VEYRA 前端适配决策

用户授权先复原所附固定版本提示词，再将功能替换为DrawForm真实能力。范围为前端展示与入口接线，使用独立演示数据；CAD几何、规则、模型状态机、项目发布与恢复协议不重写。

## 参考锁与视觉决策

- 唯一主要视觉来源：VEYRA v1.0.0，提交 `5779ec0762559b84bd5da3b6ffccf76e747f7587`；同时查看其1440×900与390×844截图。
- 原版独立运行5220，先通过原test、32项reference校验和build，再复制适配。
- Refero检索返回NO_SUBSCRIPTION；未订阅或产生费用。采用用户提供的精确源码/截图和Refero本地设计工作流进行对照。
- 保留1672:941共享平面、同一测量公式、完整车与两轮、白3px图框、蓝灰/酸绿tokens、原视频与播放器、原index.css和移动两列页脚。
- 允许变化：DrawForm品牌与中文层级、四个功能文案、业务dock/CTA、工具页共用导航与配色。原始素材和参考manifest不可重写。
- 原版已知外观关闭焦点问题原样保留。适配版单独修复关闭后延迟focus/pointer事件导致菜单重开，并保留正确焦点和显式点击重开。
- 画面明确标作概念交互示意；工程能力由原有真实工作区承载，不拿概念车冒充生成的模型。

## 功能与集成

| 场景ID | 用户入口 | 真实落点 |
| --- | --- | --- |
| drive | 3D转2D | `/?tool=step`；同时置于顶部与页脚首项 |
| battery | 文字建模 | `/workbench/?mode=text` |
| paint | 图纸建模 | `/workbench/?mode=drawing` |
| wheels | 项目与交付 | `/workbench/?view=projects` |

`service/frontend.py`只处理可选dist、首页分派与固定静态资源。`service/workspace_theme.py`仅换肤、导航和旧窄范围入口文案；所有原控件与业务script保留。workbench末尾增加白名单首屏query初始化，不创建任务或变更有效版本。已有job/session深链接优先，不改URL里的版本与session契约。

静态豁免按固定endpoint判断；只读构建目录中的assets/media。解析后的文件必须仍在对应目录内，拒绝穿越和外逃symlink。API、工程图、STEP与项目包仍需Bearer。令牌沿用sessionStorage，不写URL或构建资源。首页CSP只开放本源代码/媒体与原版Google字体。

## 数据与验收

开工先冻结235份已有源/测试/运行资产。新演示8455从现有已验证备份恢复613文件到新目录；旧8454服务不停止、不刷新、不写入。首包功能使用原实现，测试在新目录创建独立技术样本，技术采用/发布不计作人工审图。

验收分开记录：原版reference/test/build；适配版28项保留文件/test/build；137项Python定向回归；六档浏览器布局；真实文字、DXF和已有STEP输入；交付包独立校验；新进程读取；旧源/测试/历史数据摘要保护。最终证据见本批validation报告。

本轮不承诺物理手机/Safari触摸验收、任意图纸理解、任意复杂建模或制造工程适用性。建模页未提交的澄清/修改参数草稿保留原有页面内限制。完整恢复工具仍为本地维护流程。

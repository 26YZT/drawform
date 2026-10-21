# DrawForm VEYRA 前端重构验证

本轮目标：先按用户所附提示词运行VEYRA固定版本，再把同一场景适配为DrawForm四个真实能力入口。未发布公网、推送仓库或调用收费媒体生成服务。

## 交付位置

- 原版：`开发/veyra-reference-v1.0.0`；http://127.0.0.1:5220/ 。tag `v1.0.0`、commit `5779ec0762559b84bd5da3b6ffccf76e747f7587`，分支my-veyra，Git工作树干净。
- 适配源码：`drawform/frontend`；同源完整演示 http://127.0.0.1:8455/ （最终PID93966）。服务记录与证据在 `drawform/out/frontend-veyra-20261018/`。
- 数据：从既有已验证备份恢复613文件到本轮独立demo-work。旧8454 PID79354及原历史目录保留。

## 静态、构建与回归

| 检查 | 结果 |
| --- | --- |
| 原版 pnpm10.15.1 frozen install / test / verify:reference / build | 通过；32项固定参考文件匹配 |
| 适配版 frozen install / test / build | 通过；不增加应用依赖 |
| verify:adaptation | 28项保留文件匹配，原manifest摘要固定 |
| Python相关回归 | 137 passed /75.64s；含鉴权、DOM、异步、文字/图纸真实建模、项目生命周期 |
| 工具换肤最后收口复验 | 12 passed /0.54s |
| 143份已有测试资产摘要 | 全部保持 |
| 既有源/运行/测试冻结235份 | 仅app.py、workbench/api.py、workbench/page.py三份已有文件变化；原几何、规则和pipeline保持 |
| 原8454历史613文件摘要 | 全部保持 |

137项是本轮定向回归，不冒充修改后全量1643项复跑。开工前快照 `before-source.tar.gz` 和 `before-source-manifest.json` 可回查。新增后端集成与纯主题文件分别为 `service/frontend.py`、`service/workspace_theme.py`；新增12项Python检查，旧测试不改。

适配版有意修改四项参考展示文件，因此不能把适配版的原verify:reference视作应当通过的校验；原命令与manifest保留不重写。新增校验单独固定manifest并保护播放器、原CSS、18份媒体等原版文件。原appearance测试保存，适配版使用新业务dock检查；原版仍运行原appearance测试。

## 浏览器视觉与入口

原版与适配版分别检查1440×900、1280×720、390×844、1920×1080、768×1024、360×800：四热点、四页脚入口和完整车构图保持，未发现横向溢出。最终bundle为index-Ck8Bg6Mj.js；截图在本批screenshots目录。

原版实际操作了五个车漆、三个轮毂、两处详情、注释、Enter/Escape、快速hover切换、菜单通道和减少动态效果。实际视频开盖画面有截图证据；其余端点/队列/冻结/cleanup另由原播放器单测核对。截图与用户指定desktop/mobile参考对照，允许字体与浏览器栅格差异，不声明跨平台逐像素一致。

适配版四个功能链接分别进入原STEP工具、文字模式、图纸模式、项目列表。有效session深链接优先于mode/view；入口不自动创建模型、采用或切换项目有效版。旧审图稿保存与所有业务控件保留。新静态豁免不放开API，穿越和逃逸symlink拒绝。

菜单真实焦点事件曾暴露关闭过程的DOM移除blur/leave清掉抑制标志，导致延迟focus重开；已作为单独回归用例收口，最终360/1440浏览器关闭、焦点恢复、Enter重开和Tab导航通过；结果见本批browser-verification.json。

## 真实业务闭环

全部为本轮独立目录的技术样本，不记作人工工程审图。

| 输入/操作 | 实际结果 |
| --- | --- |
| 文字100×60×8、中心通孔Ø20 | session `058f1f33b8fe49f6bbde61c47735ff72`；真实3D/STEP下载、明确采用、Job `0dd18cc21dc5`四出口、PDF下载、项目修订1/统一包 |
| 120×80双孔DXF、缺厚度/通孔条件 | session `2123b6b6b5024ac0977334fbf571fe82`；实际上传、原图事实、补8mm和通孔、真实3D/STEP下载、采用、Job `cdf5f772d42f`四出口、PDF与统一包 |
| 已有STEP上传入口 | 浏览器上传刚下载的STEP，Job `0d5a083a84dd`真实出图，审图画布与PDF下载可用 |
| 既有项目列表恢复 | 选择root `c0023b6ff1c44eb581d1635ff50da3df`；打开原session `bd366254aed243f89f4e781ce6a90be2`、模型第3版、项目修订3；下载原156文件包 |
| 本轮服务新进程 | 本轮8455正常关闭并重新启动，已有文字模型、出图任务、项目记录及原深链接可读；旧8454服务始终保持 |

两个浏览器下载的STEP用原生OCCT独立冷读：均有效单实体；bbox分别100×60×8与120×80×8；体积45486.725877、75493.097456 mm³与解析值匹配。原生DXF DIMENSION包含Ø20及Ø8/Ø12对应测量值。结果在 `native-ui-check.json`。

文字新包49文件、图纸新包57文件、原项目包156文件，均由独立project_package校验通过，含来源、模型、STEP、图件、有效版本关系与摘要。下载文件保存在本批out目录。

## 实际限制

- 物理手机、iOS/Safari触摸和实际CAD/打印验收未做；当前为Chromium浏览器响应式/键盘和本地CAD技术验证。
- 本轮不扩大原有简单零件建模范围。首页概念车/剖面不是DrawForm生成的CAD。
- 完整工作区恢复仍是本地工具，网页只恢复已保存项目/session；未提交建模澄清/修改参数仍有原页面内草稿限制。
- 原版已知外观关闭焦点缺陷保持；适配版修复单独记录，不把适配后的行为说成原版行为。
- Ego截图接口超时，改用内置浏览器完成视觉与业务验收；原版窄屏一次反复reload卡在媒体准备，新tab完整加载后六档截图/操作正常，原版未改源码。
- Google字体保持原版外部导入；离线使用本地字体。Refero在线检索NO_SUBSCRIPTION，使用用户给定精确源码和截图作为参考，未产生订阅费用。

最终源码/构建冻结见 `implementation-freeze.json` 与 `implementation-source.tar.gz`；最终新进程读回见 `final-restart-readback.json`。所有临时视口和减少动态效果仿真均已复位。

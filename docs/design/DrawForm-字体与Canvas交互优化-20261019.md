# 字体与Canvas交互优化

用户要求：小字用抖音美好体，大标题用优设标题圆；通过已连接MCP选择Canvas UI组件；检查首页和功能页交互。补充确认：悬停、点击、选中统一为上汽蓝。

| 决策 | 来源 | 实施 |
| --- | --- | --- |
| 小字/按钮/说明使用抖音美好体 | 用户图二；ByteDance官方bytedance/fonts | Version 1.01，完整字库转WOFF2，保留OFL和版权；该字体原生为单一粗字重，不伪造细字重 |
| 品牌、大标题、功能卡片标题用优设标题圆 | 用户图三；优设官方发布页 | Version 1.005；原字形/名称/版权不改，完整字库转WOFF2，来源与镜像摘要随资源保存 |
| 悬停/点击/选中统一上汽蓝 | 用户补充确认；既有上汽原图 | #0066ff主操作、#0055d6悬停、#0047b3按下；深色面板用白字/浅蓝边保证识别；成功/警告/错误语义色保留 |
| 卡片采用Canvas UI Ripple | shadcn MCP读取官方registry的Ripple/Grid；Canvas UI官方文档及许可证 | 只采用低幅度、无色散/内容折射、无环境循环的鼠标涟漪，保留原HTML链接；不选会扭曲文字的Grid效果 |
| 保留普通DOM与既有业务状态机 | 原页面/用户保留能力与历史要求；Refero craft | forceOverlay固定语义HTML，GPU层不拦截鼠标；界面控件、键盘、鉴权与模型/图件流程保持 |

Refero实时研究返回NO_SUBSCRIPTION，本批使用用户三张精确截图、当前界面、字体/Canvas官方资料与本地craft作为参考，没有认领在线Refero风格参考已取得。

## 来源与集成

抖音美好体官方：https://github.com/bytedance/fonts ，固定提交327565b61501d7a6d4902ed6ce417a7bd2ae54e6。优设标题圆官方声明：https://www.uisdc.com/uisdc-4-free-font ，下载镜像与原文件MD5/版本/版权对照记录在assets/fonts/youshe/SOURCE.json。原字体文件保留完整字符，没有用截图或相似字体替代。

Canvas UI：https://canvasui.dev/docs 。MCP服务当前无@canvas-ui命名空间配置，因此通过官方registry完整URL成功读取ripple-react/grid-react；使用Ripple真实registry源码，不将自写CSS称为Canvas UI。源版本a04a99b235299ee79a123a16ccb2e6cf9da74b6b，registry源码与改造记录在src/components/canvasui/SOURCE.json。

项目保留React18及原依赖。该组件只用现有React18支持的hooks；通过TSC和运行验证，不升级整站。宿主适配包括：普通HTML覆盖模式、初始化异常回退、context loss释放、输出canvas状态、避免卡片内部滚动条。MIT + Commons Clause全文随源码和public/assets/notices分发，不单独销售组件。

内页继续用原生控件，只统一字体、选中/悬停/焦点/禁用反馈，不向CAD画布或工程文件注入GPU效果。模型/图纸尺寸与等宽摘要保持原语义和导出。

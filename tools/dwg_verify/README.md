# DWG 真机验证包（在装了 AutoCAD 的机器上跑一次）

## 这包要回答的唯一问题

**我们出的 DXF 经过 AutoCAD 存成 DWG、再导回 DXF 之后，原生尺寸还在不在、数字有没有变。**

为什么只问这个：DWG 是 Autodesk 的私有二进制格式，第三方库写不了现代版本
（LibreDWG 自己写着 "R2010-R2018 writing leads to CRC errors still"）。
所以 DWG 出口走"借集团已有的 AutoCAD"这条路。而转换器最常见的失败方式恰恰是
**把尺寸炸成普通线条和文字** —— 图看着一样，机器读不出"这是个 Ø40 的直径尺寸"，
那正是我们交付的核心。所以判定不比对像素，比对尺寸实体与显示数字。

## 你要做的（约 5 分钟，一次性的）

1. 找一台**装了 AutoCAD 2018 及以上**的 Windows 机器（集团任意一台 CAD 席位都行）。
2. 把整个 `tools/dwg_verify` 文件夹拷过去。
3. 右键 `run_on_cad_machine.ps1` → 使用 PowerShell 运行
   （或在该目录下执行 `powershell -ExecutionPolicy Bypass -File .\run_on_cad_machine.ps1`）。
4. 跑完把生成的 **`result.zip`** 发回来。里面是：
   - `out/*.dwg` —— DXF 转出来的 DWG
   - `back/*.dxf` —— 那些 DWG 再导回的 DXF（**判定靠这个**）
   - `report/` —— AutoCAD 版本、`/?` 帮助原文、每次调用的退出码与输出、文件清单

**不需要你判断成败**，发回来我们这边跑判定器。

## 三件事要说在前面（不是免责声明，是这包的构造）

- **命令行开关没有官方规范。** 我查到的 Autodesk 官方资料（《AutoCAD 的 101 个小秘密》下篇，第 68 页）
  只说明 AcCoreConsole 可以加载插件、可以执行 script 做批处理，**没有公布 `/i`、`/o` 这类开关**。
  所以脚本第一步就是把 `accoreconsole.exe /?` 的原样输出存进 `report/usage.txt`，
  然后逐个尝试并记录退出码 —— **开关对不对，由那次运行的输出说话，不由我猜**。
- 如果两个尝试都失败，`report/` 里会有完整的失败输出。那也是有用的结论：
  说明这条路要走，得换成"在 AutoCAD 里手工另存"或采购 ODA 转换器，而不是我们假装支持 DWG。
- 这个包**不修改**你机器上的任何东西：只在它自己目录下建 `out/`、`back/`、`report/`。

## 我们这边怎么判

```bash
cd drawform && PYTHONPATH=src ../spike/.venv/bin/python \
    ../research/check_dwg_roundtrip.py tools/dwg_verify/samples <发回来的>/back
```

判定逻辑在 `drawform/eval/dwg_roundtrip.py`（有 5 项测试与对照组：
我们自己出的图跟自己比必须判"通过"）。它报三件事：
尺寸实体条数变少（`dimensions_lost`）、显示数字对不上（`labels_lost`，会点名哪几个数）、
读不出来（`target_unreadable`，多半是直接把 `.dwg` 发回来了 —— 那要的是导回的 DXF）。

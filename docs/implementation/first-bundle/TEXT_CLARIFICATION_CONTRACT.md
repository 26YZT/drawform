# 文字缺项澄清接入合同

2026-10-18；首包v2已授权开发范围内，沿用现有本地CAD运行时、schema2规格、模型版本及唯一出图队列。

- 完整旧文字解析行为和旧文件名保持；两条完整解析失败后才启用严格有限的文字分析。只支持板件、同心垫圈/实心圆盘及明确编号通孔；盲孔、未知特征、冲突及表达式直接拒绝。
- 输入原文不可变：session.text为原始全文，source_sha256/text_sha256是原始UTF-8摘要；analysis.normalized_text及normalized_text_sha256单独保存。text_span证据的span=[start,end]、span_basis=normalized_text，仅指规范化文本，不能当原文坐标。
- 缺项返回needs_clarification，不启动CAD。复用现有answers端点、clarification_receipts、问题版本和basis算法；答案只覆盖当前未确定参数，已知事实不允许覆盖。
- 数值尺寸须有限且大于0；孔XY允许负数或0；hole_mode选择no_holes/center_through，选择中心孔后动态产生孔径问题；hole_origin只确认plate_center。文字单位只接受mm，未声明须确认。
- 原始文字、解析事实、用户补充、模型修改差异分别保留；每次回答记录源摘要，部分回答可恢复。严格拒绝重复JSON键；旧依据、串座位、源变化、同键异内容不得生成模型。
- 问题清空后由spec_from_analysis核验几何/贯穿/原点，进入现有queued→真实STEP→双运行时冷读→awaiting_adoption。采用、STEP下载、2D工程图、模型子版本沿用原有入口。
- 验收：缺T文本长100/宽60/中心通孔D20只询问T；回答8后真实STEP可读，原生DXF尺寸可测；修改孔径D12产生独立版本/Job，旧文件保持；新进程恢复等待态和完成态。
- 不认领任意自然语言、任意图纸、阶梯轴或人工工程审图完成。原84源/运行、127测试资产冻结见out/v2-model-revisions-20261018/implementation-freeze.json。

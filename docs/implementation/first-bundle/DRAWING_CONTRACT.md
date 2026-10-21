# 图纸闭环增量契约（2026-10-18）

用户授权本批图纸输入/关键澄清/简单零件扩展与真实验证。保留1201项首段冻结；不改旧标准、旧审图或旧数据。新验证端口8447/独立工作目录，8446与旧8443/8444保持。

规范Spec（纯数据，后端统一校验）：

- `{schema_version:2,kind:'plate',parameters:{length_mm,width_mm,thickness_mm},holes:[{x_mm,y_mm,diameter_mm}]}`；孔坐标相对板中心，Z方向贯通，0–16孔。边缘/孔间不得相交。
- `{schema_version:2,kind:'washer',parameters:{outer_diameter_mm,inner_diameter_mm,thickness_mm},holes:[]}`；inner可以0（圆盘）。
- 二段阶梯轴暂不开放：现有2D方案存在肩面位置覆盖缺口，不能仅原生建模成功即标工程图完整。

native runner（新增simple.py，不改plate.py）：`python simple.py request.json`，request仅`{schema_version:2,spec:<Spec>,output_dir:<受控绝对新目录>}`。写model.step、preview.json、validation.json、result.json；schema_version2，status=succeeded，spec、runtime、step_sha256、files、validation。preview/validation仍携确切STEP SHA。独立OCP8 checker inspect_simple.py读取request `{step,spec,output}`。所有图纸清晰/缺失/冲突须在构建前解决。

DrawingAnalysis：`{schema_version:2,source_sha256,kind,parameters,holes,facts:[{id,parameter,value,unit,evidence:{type,entity_handle?,layer?,page_index?,bbox?,text?}}],questions:[{id,parameter,label,reason,type:'number'|'choice',choices?,unit?}],preview_file,notes,unsupported?}`。缺失关键尺寸仅提出问题，不猜造；图纸明确值冲突不择一忽略。parameters/hole字段用原始单位，units:'mm'|'inch'|null，native规格统一mm。

API追加：POST /modeling/drawing-inputs multipart file→201 bundle；原POST sessions接受此bundle，返回analyzing/needs_clarification/queued等。GET session含analysis/source_preview/questions/basis_sha256/clarification_revision/spec；POST sessions/{id}/answers带basis_sha256,revision,answers:{question_id:typed_value},Idempotency-Key→继续，重复同键同内容不重复构建，旧依据409。原采用/导出/出图链不改权威。

页面增加文字/图纸切换、实际上传、源图预览/尺寸出处、关键问题表单；参数卡按kind动态。全部本地处理；机器检查/用户澄清/采用与工程审图分开。

PDF/图片追加`text_consumption:[{id,status,text,roles,residual?,evidence}]`，status为`consumed_role|allowed_text|unread_role|unexplained`。对每条识字文本消费有限语法片段，只允许明确的图号/页码/比例元数据；同一行剩余要求、词表外自由备注及低置信未解释内容不能忽略。`unexplained_annotation`保留类型/尺寸/原图证据，API进入needs_clarification并把readonly问题归一为blocked，禁止覆盖、采用或出STEP，须更正重传。单个长宽角色未读出只允许在对应参数缺失时追问，不采用该模糊数字。

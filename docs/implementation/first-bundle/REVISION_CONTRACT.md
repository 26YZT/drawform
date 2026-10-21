# 本轮模型修改版本契约（2026-10-18）

范围：N10不可变模型修改/历史/恢复子集，及N04明确文字无孔/多孔板；不新增CAD几何种类，不更改legacy Drawing/审图版本链，不宣称已有统一项目HEAD或完整发布journal。

每次修改创建新session、新STEP和独立2D Job，父session/原图事实/旧图件保持。采用仅确认所选模型版本可用于出图，模型版本与审图台DXF修订版本是两种记录。历史分支明确显示父模型版本；不会自动替换旧模型或自动采用。

POST /api/workbench/v1/modeling/sessions/{parent}/revisions；Idempotency-Key。JSON必须恰为：

```
{expected_step_sha256,expected_spec_sha256,expected_revision,
 changes:[{op:"set_parameter",parameter:"thickness_mm",value_mm:10},
          {op:"set_hole",hole_id:"hole_2",diameter_mm:12}]}
```

expected_revision是GET当前模型时返回的model_version.latest_number（整个根模型族最近已分配版本号），并发旧页面409。两个SHA绑定所选父模型；同键同内容返回同一子session，同键不同内容409。修改仅允许当前Spec已有长/宽/厚/内外径及既有孔的直径/XY；hole_id为hole_1...hole_16，映射绑定父spec SHA，本段不增删孔/重排孔。所有数值须有限，重复字段/未知op/表达式/越界/孔相交/空修改拒绝。

GET session追加editable_spec（统一schema2）、model_version：{number,latest_number,root_session_id,parent_session_id,parent_step_sha256,parent_spec_sha256,spec_sha256,changes,mode}。mode为original或design_variant。changes由服务端生成：[{path,label,before,after,unit:"mm",hole_id?}]。图纸派生修改保留原analysis事实，在独立修改列表显示覆盖值，不把它称为复现原图。

GET sessions/{sid}/history返回{root_session_id,selected_session_id,latest_number,versions:[{session_id,number,parent_session_id,state,step_sha256,job_id,created_at,changes,view_url}]}，只含当前席位模型。根模型为v1，版本号事务分配，不由前端数组长度推算。父子链最多64层。原模型修改后不清旧job_id、不覆盖旧artifacts；迟到进程只能完成自己的session，旧回答不能修改新session。

状态复用queued/building/awaiting_adoption/adopted/failed/interrupted。完成文件缺失/摘要不符返回完整性错误，禁采用/出图/派生，不删除历史记录。构建中断如实保留，不自动复用未验证文件。GET追加artifact_integrity:{status:"verified"|"unavailable",error?}供已完成模型展示；没有STEP时不声称已验证。

验收：真实双孔板仅改孔2直径10→12，其余尺寸/孔位不变；父STEP/2D字节保持；子版本未采用无2D，新采用后新Job，幂等/旧依据/跨席位/超界拒绝；真实重启后父子及各自图纸绑定保持。文字无孔/多孔完整语法直接生成现有schema2；不替代未知制造尺寸，本段缺项文字仍明确拒绝。

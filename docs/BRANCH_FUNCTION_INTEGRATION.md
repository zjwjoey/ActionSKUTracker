# 分支功能整合候选（未合并、未部署）

基线：2026-10-31 fetch 的 main `6f2052929d5a35268893a97634fab40820b1c25d`。
完整远端清单与 A–F 判断保存在本轮 ScratchTask 审查报告；分支数量和 CI 是本次快照。

## 本次实际移植

| 来源 | 当前实现 | 选择原因与边界 |
| --- | --- | --- |
| `fix/preserve-export-quality-20261013` / `d133dd6`、`7f33705` | `localization/qa.py::_detail_material_color_findings` | main 已有大部分 Source Fidelity 和属性语义控制，但六个颜色/材质反例仍被事实 QA 接受。只移植有限属性内检查，复用原 QA 和 `DETAIL_CONTEXT_MISTRANSLATION`；不恢复旧 `fidelity.py` 的品牌字典、自动改译和数量分类器。 |
| `fix/stage6-provenance-v2` / `9b729c2`、`e749099`，前身 `850c498` | `stage6/legacy_provenance.py` → `stage6/preview.py::preview_one` | 有价值的是既有历史证据的可核对性。旧适配器仅检查部分路径/Hash 声明，不直接复制。现核对实际文件字节、唯一 SKU/字段/候选、原文与字段 Hash、审核值、审核人/时间及目标基线；不补造现代模型或审批元数据。 |

颜色/材质只看本详情字段的相应属性，重复角色按出现顺序核对。普通正文、其他字段、
未知词项和否定不借此生成事实；否定继续交给现有 QA。已支持 MDF/PET 复合材质及有限同义词。
首轮真实只读回放暴露了限定属性误拦，已增加原文回归并收窄：未实现的墨水颜色、
适用表面、烛台/瓶身/鞋底/支脚等限定角色不得降级成普通商品颜色或材质。
这些角色仍由既有 QA 负责，不能声称本次有限映射已覆盖所有材质属性。
`guard_translation` 和 `exporting/repair_report.py::audit_repaired_rows` 使用同一规则；没有第二套 QA 引擎。

## 历史证据预览合同

既有 `preview_one` 接受 `provenance_type=LEGACY_ARTIFACT`，其中 `legacy_provenance` 包含
`provenance_type/sku/field/artifact_path/artifact_sha256/owner_approval_artifact/owner_approval_sha256`。
两份文件必须为绝对路径 UTF-8 JSON 或 CSV，不超过4 MiB；Hash 为实际文件 SHA-256。
JSON 是行数组或含 `entries` 数组的对象。两份文件都必须唯一绑定以下真实历史字段：

- `sku`、canonical `field`、`candidate_id`、`source_spanish_value`、字段级 `source_hash`、`reviewed_value`；
- `reviewed_by`、带时区且非未来的 `reviewed_at`；
- 审核文件的 `review_decision=ACCEPT/ACCEPT_WITH_MINOR_EDIT`；
- Owner 文件的 `owner_decision=ACCEPT`、`current_target_value` 与本次目标基线一致。

这些内容必须原本存在。其它历史格式保持阻断，不能临时补写一个“已批准”文件冒充原件。
本接口不声称验证签名/人工身份，也不把这些声明当成现代正式授权：所有历史候选
始终带 `LEGACY_REAPPROVAL_REQUIRED` 并 `DO_NOT_APPLY`，供现有审核流程重新审阅。
原生候选不新增长期历史豁免；未知 provenance 类型阻断。
旧 `legacy_overall` 即使带 `legacy_compatibility=true` 也必须重新取得字段级来源证据。
本次没有历史批准自动升级、Stage 6 Apply、数据库 Schema 或新的发布入口。

## 不恢复的实现

- `post-release-data-integrity` 与 `preserve-production-local` 的事实保留、当前价格、增量详情刷新已由主线和 Data Foundation V3 覆盖。直接覆盖会丢失现代字段 provenance 和受限提交边界。
- `snapshot_ingest.py` 的未提交 Snapshot 恢复不等于现行已提交事实复用。旧实现缺文件摘要绑定、重复身份检查、强布尔 QA、持久提交范围及现代恢复事务绑定；不接入。现行 Operations 的提交后恢复、Registry retry、detail retry、V2 committed-daily adapter 继续各司其职。
- Workflow V2 旧 closure 由现行 shared lock、committed daily source、Registry 来源身份、持久 Resume 覆盖；不引回旧部分写入器。
- Scrapling、Qwen 训练/质量实验、历史 Skill 留在隔离分支，不启用网络采集、付费模型、训练或正式字典发布。
- main 已有 `_approved_primary_fields` 的精确来源/审批/Apply 绑定保护，不重复移植旧仅比较审批时间的导出优先级实现。

没有删除远端分支、原始资产、备份或证据。未引入的旧模块不复制进本候选，不维护双套逻辑。

## PR #26 独立处理与回退

PR #26 `2cc4eae` 单独评估：来源跟踪按字段，旧非 tracking 条目不升级成豁免；
变化保留 PENDING，AI 和兼容补丁准备排除登记字段，正常字段保留原条件。该功能默认关闭，
只覆盖显式启用的本地审计；不是所有外部自编 Apply 脚本的全局权限拦截器。
正式审批和 immutable Apply 仍须通过现有合同。CI 成功不是21个来源冲突已经解决。

建议先独立合入 #26，再在确认本候选的完整回归、组合兼容测试、双平台 CI 后评审本候选。
整合候选仅将审查过的 #26 合入本地 integration 分支，用于冻结组合测试的精确 SHA；
整合 PR 以 #26 的分支为 base，差异仅为本轮两项移植，不把 #26 强制打包。
#26 合入 main 后须将整合 PR 改为以 main 为 base 并重新核实 CI。本轮不代为合并。
该本地组合不改变 main、PR #26 或生产。本轮授权止于 PR，不能自动执行上述合并顺序或部署。
回退仅撤销本候选代码提交，或维持当前生产 release；历史证据本身不回写，无数据库迁移。
若未来已有生产 Apply，必须另走备份/正式恢复，不能以 Git 回退代替数据恢复。

验收使用临时数据库和生成文件，真实来源只读审查。真实官网采集、生产 daily、
生产备份恢复、正式翻译 Apply 和正式导出覆盖不在本轮执行，不能由工程测试推定通过。

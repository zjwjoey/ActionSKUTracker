# 当前仓库与生产状态

历史治理快照截止：2026-10-08；2026-10-10 授权历史中文修复进度见末节。以下旧候选和运行目录描述保留其当时身份，不表示历史修复分支已部署到 daily 主线。

## 已冻结生产

- origin/main：231550eb74d24c927e100bc585aa728bf7ab7f19。
- 标签 production/phase1-3-20261007 指向该 SHA。
- 历史本地完整测试 826、该基线 Ubuntu/Windows CI-safe 各 823。
- Phase 1–3 曾在真实 PRIMARY 验收并发布当次 5,626 SKU；数量不是永久业务规则。
- 正式入口 data-update/production-run，SQLite PRIMARY 权威，Excel/State 为兼容投影。

## 本轮候选

fix/post-production-repository-closure-20261008 从核实 main 建立，在独立 checkout 开发。

1. Preview 独立目录、可信摘要，保护正式/未知三件套。
2. Template 1 共用完整 Release Gate。
3. V2 正式 FACT_COMMIT 验证并复用已提交同日 daily；禁止部分 bundle 独立写 PRIMARY。独立 canary 仍只写临时库。
4. Registry FAILED/PENDING 独立后续状态，Operations DEGRADED，按 run/commit/date 幂等恢复。
5. Operations 传业务日期；daily 计算前冻结 base；V2 共用运行锁及 head 校验。
6. 更新入口、恢复、部署、回滚合同，审批回归加入 CI。

部署状态：候选尚未合并 main，也未替换今日生产代码或重启任务。实际 full/CI-safe 数量、final SHA、exact-head CI 和副本证据位于本轮 runtime/reports/post_production_repository_closure_20261008/；runtime 不提交 Git。

今日实际执行代码位于 F:\ActionSKUTracker_workflow_v2，分支 fix/workflow-v2-phase3-final-closure，HEAD 3d28f0b356ded3fd2b5d24862954b581e0ef60f5。已冻结 main 基线和当前运行 checkout 是不同身份；额外 snapshot-ingest 尚不属于本轮主线候选。该 checkout 本轮保持不变。

## 外部治理与排除范围

main protection 只读查询显示未保护；建议 PR required、Ubuntu/Windows required、禁止 force push/delete，变更须 Owner 授权。PR #5 Selective Workflow V2 runtime backport 已被生产主线取代，建议另行关闭，本轮未合并/关闭。3d28f0b snapshot-ingest 不纳入本轮，须专项审查。

本轮不注册任务、不重新采集、不调用新 Qwen、不修今日数据、不恢复 PRIMARY。参见 [README](../README.md)、[Operations](OPERATIONS_RUNBOOK_V2.md)。

## 2026-10-10 历史中文修复：已验收与待处理

用户授权的独立 checkout 为 `F:\ActionSKUTracker_history_20261009`，分支 `fix/historical-localization-recovery-20261009`。正式数据库仍为 `F:\ActionSKUTracker\runtime\db\action_tracker.db`。历史中文通过既有 Registry / QA / delegated Approval / Immutable Patch / Apply / Master Sync 写入；没有合并或推送 main，也没有发布字典基线或修改 daily 的配置开关。

规格第 24 批验收 PRIMARY head：`2026-10-10_historical_existing_spec24_20261010_b316539f6ded`。类目第 19、20 批共恢复 1,531 个正确原值的审批及来源绑定；规格第 21、22、23、24 批分别恢复 38、41、51、110 个绑定，第 23、24 批另修正 2、9 条规格文本。描述第 24 批修正 1 条包耳式耳垫特性及随附线缆措辞。各批均完成备份、受保护数据和全库中文差异检查、Master Sync 及实际同批 Resume；重复 Apply 为 0。原值元数据补丁不能计为补译或错误译文修正。

规格独立审查第 21–24 组共覆盖 600 组、1,174 个 SKU 字段：1,154 个现有中文可保留，11 个已修正并入库，9 个来源/色号问题保留待复核。第 24 组的 565 个字段中，550 个正确值保留、9 个修正、6 个阻断；440 个正确字段直接复用已有有效审批，110 个恢复绑定。9 条修正包含 7 条洗涤次数用途遗漏、1 条功率单位尾缀残留和 1 条拉伸器档位误译。6 条阻断涉及唇线笔/染发剂色号及四个无法明确分解的包装计数差异。正确中文不重新翻译；本轮 120 个实际 Apply（规格 119、描述 1）不包含缺失字段补译。

| 截止此已验收 head 的核实范围 | 数量及解释 |
| --- | --- |
| 历史 SKU / 六字段机械检查 | 4,244 / 25,464；不等同于全量独立语义审查 |
| 非空中文 | 20,470 |
| 既有独立审查证据重新验证 | 3,771；精确原文、目标、来源文件、当前审批和 QA 均核对 |
| 已人工确认类目映射证据重新验证 | 6,629；全部当前有效审批和绑定，单独统计映射政策验证 |
| 上述两类当前有效审核覆盖 | 10,400 个不同字段；其余 10,070 个非空字段仍未被这两类证据覆盖 |
| 缺失中文 | 4,994；可信来源 709，其中 708 与 PRIMARY 精确一致、1 有已记录的不一致 |
| 缺失来源分类 | 缺源 3,460、版本冲突 469、语言复核 355、明确空源 1；不编造中文 |
| 历史实际 Apply 账本 | 16,376 个不同不可变补丁 ID：14,575 个文本改动补丁、1,801 个原值元数据补丁；重复修正单独计补丁，不是唯一字段数 |

最近核心代码为 `cb1e72f`：既有字段级 VARIANT 机制保护数字 lavados 的洗涤用途，QA 阻断中文数字单位残留 s/es；22 项真实来源和边界测试加入 CI_SAFE，完整回归 1,267 项通过。此前 `cb54da9` 保护 Over-ear 包耳特性，`c84f0e8` 为来源限定的霓虹灯 QA 别名，`0c3cbea` 绑定有限翻译缓存的实际 adapter 配置。本批没有新的模型服务调用。六项真实历史来源 fixture 的 daily 兼容性测试通过，完整在线 daily-run 仍未验证。全六字段独立语义审查及任务整体验收尚未完成。

可追溯证据保存在 `F:\ActionSKUTracker\runtime\reports\historical_localization_20261009`：各批 `acceptance.json`、规格第 21–24 批 `reviewed_all.json`、各批 `owner_queue.json`、`prior_independent_semantic_review_revalidated_after_existing_spec24.json`、`historical_actual_apply_ledger_after_existing_spec24.json`、全字段 `existing_chinese_reaudit_20261010_after_existing_spec24`。这些运行产物不提交 Git。下一轮 `existing_name20_pending100_groups.json` 包含 100 组、154 个品名字段，尚未独立审查或 Apply。继续其他字段及可信来源缺失候选；不得把未 Apply 候选计为正式入库。2546793 描述的唯一可信来源不一致是旧归档 `null.` 前缀与已清理 PRIMARY 的差异，需核对来源规范化证据，禁止重新写入 null 或伪造归档精确匹配。

## 2026-10-10 用户指定历史表格中文匹配与首批入库

最新验收 PRIMARY head 为 `2026-10-10_historical_user_archive_pilot49_20261010_896fb33466e9`。按用户要求只读扫描 `F:\按日期整理\action表格` 中 17 个日期的 75 份商品表，包括带图、无图和修复版本。仅按官方 SKU 及本字段西语版本匹配，不按行号或相似标题，也不把 4 月表中的 `vivienda_…` 内部编号猜成 SKU。75 个输入文件在入库后重新校验 SHA-256，均未改变。

此前 4,994 个中文空字段中，4,920 个在归档有非空候选；其中 535 个具有可信且与 PRIMARY 精确一致的西语配对和唯一中文值（品名 249、规格 4、描述 136、详情 146）。另 171 个有多个中文版本，4,214 个不能建立当前精确西语配对，74 个未找到候选。以上是候选发现，不能算成入库。

首批独立审核 49 个品名/规格字段，31 个经过既有 QA、delegated Approval、Immutable Patch、Apply 和 Master Sync 实际补入（30 品名、1 规格）。27 个归档候选原样复用，4 个在恢复前可靠修正；没有覆盖已有中文，没有新模型服务调用。实际中文差异为 31 个字段，完整性、外键与八项受保护数据检查通过；同批实际 Resume 为 0 Apply、31 NO_OP、ALREADY_SYNCED。18 个首批字段保持空值待复核，其中 4 个 QA 阻断、14 个来源/主体等语义阻断。

其余 486 个候选已完成机械 QA，316 PASS、170 FAIL，尚未独立语义审查或 Apply。原 535 个候选共剩 504 个未入库；不能把 316 个机械 PASS 当成语义批准。QA 及待复核问题包括木纹外观被误按木材材质保护、iPhone 型号残留判定、PPP/DPI 标记差异，以及旧表把盒装茶误写为收纳盒、把淋浴收纳架误写为花洒支架。核心代码未修改，保留 `cb1e72f` 和此前完整回归 1,267 PASS 的身份，不声称本次重新运行完整代码测试。

验收后中文仍空 4,963 个：可信来源 678（677 与 PRIMARY 精确一致、1 个旧 `null.` 前缀差异）、缺源 3,460、版本冲突 469、语言复核 355、明确空源 1。所有匹配证据、冻结审查、实际入库与 Resume、待复核及剩余清单保存在 `runtime/reports/historical_localization_20261009/user_archive_chinese_20261010/`；入口为 `final_summary.json` 和 `pilot49_apply/acceptance.json`。全量独立语义审查、剩余恢复及整体验收继续进行，任务未完成。

## 2026-10-10 剩余504项中文候选复审完成

504项已全部独立核对西语来源及中文：246项经QA、自主审批、Immutable Patch、正式Apply和Master Sync实际补入，涉及234个SKU（品名124、描述72、详情50）；258项保持待复核。42项复用归档正确中文，204项修正候选后补译，已有PRIMARY中文覆盖0、纯元数据Apply0。机械QA由330 PASS /174 FAIL改善为395 PASS /109 FAIL，其中149个PASS仍被语义或来源审查阻断，未批准或入库。

最新验收PRIMARY head：`2026-10-10_historical_archive_all246_20261010_3f4cee1060ce`。246项来源绑定APPROVED/FRESH，Master Sync SUCCESS；同批实际Resume为0 Apply、246 NO_OP、ALREADY_SYNCED。完整性ok、外键无违规，八项受保护数据不变，75份只读历史文件SHA复核均未改变。当前4244个历史SKU的六字段仍空4717项：可信来源432、缺源3460、版本冲突469、语言待复核355、明确空源1。历史表累计实际补入277项（此前31+本轮246）。

独立分支核心提交`08ac633`、`7638176`修复木纹外观和竹纤维限定词保护，并阻断外观来源被擅称实木。真实来源fixture及负例加入CI_SAFE，最终完整回归1278 PASS，包含六种隔离daily兼容场景；本地代码尚未部署到生产默认daily入口，正式词典及默认开关不变，外部模型服务调用0。全六字段独立语义审查及整个历史优化目标仍未完成。

详见[504项审查报告](HISTORICAL_ARCHIVE_504_REVIEW_20261010.md)。正式运行证据位于上述归档报告目录：`remaining504_final_summary.json`、`remaining_all246_apply/acceptance_final.json`、`remaining_all246_resume/pilot_review_apply.json`、`remaining258_manual_review_index.json`。早期`acceptance.json`草稿的空值指标已由`acceptance_supersession.json`明确作废，正式统计只涵盖历史六字段，实际Apply结论不变。


## 2026-10-11 可信来源432项审查及入库完成

432项全部完成本轮审查，正式补入84字段/79 SKU（品名42、规格10、描述15、详情17）；另外3次无来源限定词修正，累计87补丁但仍84个不同字段。原范围仍待复核347个历史字段，另1个字段所属商品CURRENT。两个正式批次Master Sync、完整性、外键、八项受保护数据及23份来源SHA核验通过；两次实际Resume分别0 Apply/84 NO_OP和0 Apply/3 NO_OP，最终84项APPROVED/FRESH。

最终PRIMARY头`2026-10-10_detail_apply_20261010T161740273910Z_4c999604_7e3d96056e6e`，历史SKU4243，历史六字段空值4623。145次真实Qwen-MT生成、28个确定性候选，review provider调用0；候选不计入库。核心代码`4ce68d3`，完整回归1288 PASS，未部署生产默认daily入口。全历史语义审查及整体目标仍未完成。

详见[432项审查及正式验收](HISTORICAL_TRUSTED432_REVIEW_20261010.md)，运行证据`runtime/reports/historical_localization_20261009/trusted432_20261010/overall_final_summary.json`及`final_acceptance.json`；人工复核索引`manual_review_index_at_final_head.json`。

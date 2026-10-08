# 上线后数据完整性修复

快照截止：2026-10-08。生产代码基线为已合并 PR #6 的 `main@b09bcea`；本轮独立分支是 `fix/post-release-data-integrity-20261008`。本轮不合并、不部署、不调用模型，也不写生产 PRIMARY。

## 回归商品事实合同

- CURRENT 基线继续用于 Presence、数量比较和生命周期；全部历史 Product + ES/ZH 投影仅用于字段合并。历史基线不能扩大今日 CURRENT 集合。
- Detail 导航选择实际观察到的 Listing URL、当前 Sitemap URL、历史官方 URL，校验 HTTPS、Action 官方主机、西班牙路径和精确 SKU。没有 URL 单独记录 `DETAIL_URL_MISSING`，不能冒充访问失败。
- 非空且来源有效的新字段优先；缺失字段保留历史事实并记录 `HISTORY_RETAINED`，没有历史值记录 `SOURCE_MISSING / NEEDS_FETCH`。当前抓取确认的字段记录 `CURRENT_VERIFIED`。
- 回归商品未取得本轮售价时，`current_price` 保持未知，旧价仅保留为历史参考和历史事件，不能通过发布门禁。原价不能替代当前售价；已确认的普通售价卡片没有原价节点时，清除旧促销原价。
- `fact_field_provenance` 是 Snapshot/CommitBundle 行的传输元数据，并持久化到既有 `run_evidence.evidence_json`；六个 ES 来源也写入既有 localization source 列。没有数据库 schema 或 CommitBundle 结构迁移。
- 已入库的历史西语字段不在详情失败时重新清洗，防止未观察到的格式变化制造假 source hash。Registry retry 使用同一份字段来源证据核验冻结事实。
- 中文日常写入继续保护内容和审批；仅真实变化字段变为 STALE。重复相同西语不构成审批，不能解除尚未通过 Apply 的 STALE。
- Listing 品名、规格、分类和链接变化进入增量更新；缺失图片不制造 IMAGE_CHANGE。
- V2 正式 Fact Adapter 继续复用经 Collection QA 的 daily commit，无独立部分事实生产写入。

## 本轮副本验证

`scripts/audit_post_release_integrity.py` 只读审计生产数据，通过 SQLite Backup API 创建隔离副本，并核对生产表、Master/State 和导出摘要。输出不能放入生产目录。

`scripts/validate_post_release_replica.py` 只在本分支固定报告目录的副本验证官方候选。SKU 3207872 官网当前售价 2.99 EUR；缺失根因是回归基线遗漏和导航 URL 缺失，当前价格节点可以被现有解析器读取。价格候选及配套官方西语字段验证不修改中文，也不晋升审批；实际生产应用必须另走受控操作。

`scripts/simulate_post_release_daily.py` 使用真实冻结事实做离线事务/Registry 场景回放。模拟日期、变化字段和 `human:fixture` 审批只属于临时测试库，不代表生产审批或真实连续运行验收。

报告位于本隔离分支的 `runtime/reports/post_release_data_integrity_20261008/`，不提交 Git。全库扫描分为发布门禁、字段就绪证据审查和最终修复审计，三者数量不相加。缺失旧 QA 证据保留 REVIEW_REQUIRED，STALE 不自动重翻。未经 Owner 批准的内容不 Apply。

## Phase 3 判定

同一最终中文投影经过 Repair、ES/ZH 审计、事实对齐、Master Quality、严格发布 Gate 和 Excel 回读哈希。真实副本存在中文阻塞时结果必须为 BLOCKED；`candidate_only_NOT_FORMAL` 中的测试工作簿不是正式发布文件。

代码测试/CI 通过与未来连续真实 daily-run 观察分别验收。原有 Branch Protection、Collection Integrity、审批、锁、base HEAD 和原子发布门禁保持。

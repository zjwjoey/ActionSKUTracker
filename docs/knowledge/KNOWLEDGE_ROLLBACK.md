# Knowledge Production V1 回滚合同

所有 localization Apply 必须在单一 SQLite 事务中执行，并以 `base_commit_id`、source hash 和
dictionary hash 做乐观门禁。任何校验、审计或提交失败都整体 rollback，保留旧正式中文。

若新西语事实导致 hash 变化而新候选失败，旧 localization 不删除，标记为 STALE/REVIEW_PENDING；
导出可保留旧值并显示待审标记。关闭 AI 或 Auto-Approval 不影响人工 Review 和既有生产事实。

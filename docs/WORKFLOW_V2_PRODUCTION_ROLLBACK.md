# 生产回滚与恢复合同

## 版本身份

当前冻结基线 production/phase1-3-20261007，核实 target 231550eb74d24c927e100bc585aa728bf7ab7f19。它是当前版本，不是已验证上一版。

旧 fix/naming-history-export-20260917 只保留历史证据，不能作为当前自动回滚建议。本轮没有已验证 schema 兼容且具备对应备份的上一生产版本，自动回滚 BLOCKED，不猜旧 SHA。

## 触发与现场

完整性错误、事实/生命周期/价格回归、批次越界、发布破坏时保存 run/state/log、配置 hash、当前 HEAD，停止后续写入。不删除 PRIMARY/队列/审计/历史版本；代码降级不能解除 QA blocker。

## 必要回滚清单

- 当前和目标代码不可变 SHA，以及目标兼容验证记录。
- PRIMARY schema family/version/role、目标代码读写兼容性。
- SQLite Backup API 对应备份：head、业务日期、时间、SHA-256、integrity、foreign keys。
- 目标代码在副本上的回归、事实/Lifecycle/Price/Event/中文/投影对账。
- 当前/目标配置与数据目录、恢复范围和业务影响。
- Owner 明确批准数据库恢复；代码部署授权不能代替数据库覆盖授权。

任何证据缺失都停止，不能自动 restore/reset/downgrade schema。

## 恢复步骤

1. 停止并确认全部写入进程，创建新的一致性备份；不复制活动 WAL 主文件。
2. 先恢复独立副本，核验 schema/role、head、PRAGMA integrity_check、PRAGMA foreign_key_check。
3. 用目标代码跑相关完整回归和 compatibility projection，核对来源 hash、审批/provenance。
4. Owner 批准后执行审查过的正式恢复，记录操作人、备份、before/after head/hash。
5. 重新生成恢复后 committed head 的 Master/State，export_sync SUCCESS。
6. 验证运行锁/参数，最小受控检查通过后恢复授权 daily。

    python -m action_tracker db-validate-production
    python -m action_tracker status
    python -m action_tracker sync-exports --commit-id <restored_current_commit_id>

在核实数据根执行。sync-exports 重建合法当前投影，不是 DB restore 接口；目前没有可安全自动执行的数据库回滚命令。

审计保存代码/schema/备份/Owner 决定/停写证据/恢复日志/完整性/投影对账/新 head/任务恢复时间/失败恢复方案。密钥 Cookie 不写报告，不清队列、不直接改 source hash。

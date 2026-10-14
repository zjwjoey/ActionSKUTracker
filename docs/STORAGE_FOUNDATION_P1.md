# Storage Foundation P1：本地一期实现

本模块是现有 Operations 备份入口的增强，不新增业务数据库 schema，不替换 Translation/QA/Approval/Apply。
独立分支基于 7a51fe4；实际生产仍从 deployment/active.json 识别，不能把候选 HEAD 当部署。

## 已实现与状态

- `storage-foundation audit` 只读比较当前 SQLite committed head 对应 export_sync 与 Master/known/offline；不会调用 ensure_runtime_dirs 或带写副作用的 database.connection.connect。不是对比旧 daily report。
- Operations 的 `backup_sqlite` 使用同一 `verified_backup`。Source 为 mode=ro，Backup API 包含 committed WAL；从不 checkpoint/VACUUM source。流式 SHA 及小 page cache 防止整库 read_bytes 占满内存。
- 独占预留 backup，现存未知、失败 partial、异任务产物不覆盖。失败留下新 partial 和 failure marker。相同 task/source/head/code/signature 且已有完整 SHA/验证记录才复用，满足恢复阶段幂等性。
- source head、data_version 和 main/WAL signature 检查拦截并发或同 head 数据变更。readonly 开源会话允许 SQLite 必要共享内存读锁活动，但不写业务数据。
- baseline 使用同名 daily-run.lock，拒绝所有已有锁、不回收陈旧未知锁；只移除自己的 nonce。发布备份后隔离恢复用新 scratch，先验证完整 SHA，再 integrity/FK/head；只移除本次新建 scratch，不留下永久 restore-check。
- immutable asset/context 记录 task、schema、commit、code dirty patch、配置 Hash、来源文件 signature、恢复/引用义务。恢复报告独立保存，不修改旧 asset manifest。
- retention-plan 按 source/schema/role/purpose 保留最新 N 份；未知引用、恢复义务、未完成任务、未演练均保护。只输出 RETAIN/ARCHIVE_REVIEW_CANDIDATE，永远 delete_allowed=false，没有删除入口。
- 根目录明确分 data/code/archive/temp，archive 缺失返回 ASSET_UNAVAILABLE，不设置 SOURCE_EMPTY。
- image-index 从原 manifest 保存 SKU/canonical_id/source URL/source version/PNG Hash，不猜文件名，不改原件。resolve 用根+相对路径+完整 SHA 拒绝路径穿越/Windows ADS/符号链接逃逸。

## 操作命令

```powershell
$env:PYTHONPATH = '<candidate checkout>/src'
python -m action_tracker storage-foundation audit --data-root F:/ActionSKUTracker
python -m action_tracker storage-foundation baseline --data-root F:/ActionSKUTracker --output C:/StorageEvidence/run1
python -m action_tracker storage-foundation retention-plan --manifest C:/StorageEvidence/run1/primary.sqlite3.asset.json --data-root F:/ActionSKUTracker
python -m action_tracker storage-foundation image-index --data-root F:/ActionSKUTracker --manifest F:/ActionSKUTracker/runtime/images/manifests/image_manifest.csv --output C:/StorageEvidence/image-assets.json
python -m action_tracker storage-foundation resolve --asset C:/StorageEvidence/asset-ref.json --roots C:/StorageEvidence/roots.json
```

重复 baseline 只复用完全相同的已验证备份及 restore report。Context/code/config/head/source signature 变了就阻断，不能用相同 head 掩盖未进入 commit 的队列/图片数据变化。无新 source head 时不重新生成整库副本。

## 数据契约

`BACKUP_ASSET_V1`：source/path、run_id/owner_task、schema identity、commit、code_version、SHA/size、source signature、integrity/FK、recovery_required/reference_status。默认恢复义务保留、引用未知、没有清理授权。
`STORAGE_BASELINE_CONTEXT_V1`：来源库、code dirty snapshot、配置文件 Hash 与本命令解析配置证据；不导出 API key/Cookie，不冒称已恢复凭证。
`ASSET_INDEX_V1`：root_id/relative_path、legacy_path、SKU、source_version/source_url、content SHA；内容相同仍保留不同业务绑定。resolve 的 AVAILABLE 只表示可定位，不自动晋升 QA/事实来源质量。

`load_settings(project_root_override=...)` 是不修改全局 root 的可选参数，旧调用默认行为不变。数据路径布局及 SQLite schema 保持原有结构。profile overlay 显式记录 Hash。

## 保留与部署边界

没有自动删除、实际归档、资产迁移、主线合并或生产部署。既有历史 helper 的长期 restore-check 及旧 backup_database 不会被这次静默改写；它们的兼容升级属于后续明确范围。新 P1 路径无需永久恢复副本。
现有 backup caller 合约保留原字段，新增审计字段；失败后已完整校验同 task 的 backup 可安全复用，部分产物须复核。
生产 credential 和浏览器环境独立管理。真实跨电脑 daily-run 尚未验收；全部 consumer 尚未自动使用 locator。P1 此阶段提供可测试基础与6,824条兼容索引，不声称所有旧绝对路径已修复。

## 验证

新测试仅临时数据库/文件、无官网/模型；包含 WAL 保全、源只读、同 head 并发写、不可覆盖、阶段重试、恢复 SHA、锁归属、保留分组、离线档案、Windows路径和多数据根。
真实证据独立放在 C:/Users/Administrator/StorageAuditOutputs/ActionSKUTracker_P1_20261014，不能进入 Git。

推送前复核补强：baseline Resume 完整核对备份契约、来源/路径、schema、完整性/外键、大小和 head；Hash 后再次核对源 signature；缓存恢复报告必须匹配 head、完整性/外键及临时副本约定。新增负例证明错误证据和同 head 并发写均阻断。

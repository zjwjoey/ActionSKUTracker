# P2 Storage Lifecycle（开发分支；未部署）

P1 已合并 PR #10，main 合并提交 e69dfe1。P2 复用 P1 Backup API、完整 Hash、恢复验证和 retention preview，不新增事实数据库、迁移 schema 或修改生产默认流程。

## 根目录与实际隔离候选

ActionApp 是独立 checkout；ActionData 保留现有 config/data/runtime 相对布局。
ActionArchive 保存受控真实样本及包含原路径、目标逻辑路径、SHA、SKU/run/version、引用和恢复职责的索引。
ActionScratch 是独立根，每个新任务有随机专属目录、owner.json 和追加生命周期记录。
配置中的候选与 legacy 删除默认均禁用；本模块没有旧资产删除 API。操作脚本需要显式根、预算和报告目录，不自动调用 daily/Apply/官网。

`scripts/storage_p2_candidate.py` 是 LOCAL_ONLY 的 P0.6 清单适配器。它逐文件校验复制最低运行资产，数据库仅接受不可变验证备份，活跃 WAL/SHM 不作为普通文件复制；当前图片只按 PRIMARY CURRENT 身份及原 manifest 精确绑定选择。读取已知目录和既有审计 CSV，不重新扫描磁盘。

`materialize_plan` 在大文件复制前检查预算及路径；`materialize_asset` 独占创建、Hash 检查、不覆盖未知结果，完整一致文件可复用。失败保留证据，不回退覆盖原件。一次脚本失败后不得直接删除旧候选，须先核对 manifest；下层复制原语可以复用匹配产物。

Asset ID 是内容身份，binding_id 包含原路径、root/path、SKU、run、来源版本和 commit。相同内容的多个业务绑定不被抹掉。`resolve_legacy_binding` 仅按完整原路径匹配，不猜 basename/SKU；有歧义阻断。档案离线返回 ASSET_UNAVAILABLE。

## Scratch 与恢复

资产创建前登记 owner/run/时间/来源/预计容量，完成后登记实际容量、Hash、任务终态、引用、恢复义务及期限。默认未知引用和恢复义务保留。预算不足阻断，超过预计容量记录 budget_alert。

ScratchTask.backup 共用 P1 verified_backup/rehearse_restore，只有单份永久验证备份。恢复复制只存在于 P1 临时 scratch，演练后移除。重新加载同任务日志后的 Resume 校验 owner/root/路径和 P1 source signature/完整 Hash；不再创建整库副本。重新加载的任务禁用删除。

新建同一 live task 内的可重建小文件，只有完整身份、终态、零引用证据、无唯一数据/恢复义务、期限到达、替代恢复文件和报告实时 Hash 均通过，才允许从其专属范围删除。旧文件只产生 preview。无批量目录删除入口。

历史 history_audit.verified_backup 已共用 P1，实现 WAL、schema/role 和源变化保护，同时继续对所有表做逻辑 Hash 比较。保留旧结果键；restore 明确变为 null，独立 restore_validation 给出验证结果，禁止把已移除测试副本冒称仍存在。可选 scratch_root 将恢复证据登记到独立 Scratch。内部调用者只记录返回值，没有按 restore 路径继续读取数据库。

## 清理证据和统计口径

cleanup_preview 不信任名字、时间或同 SKU；完整 Hash、当前文件校验、任务完成、明确无引用、无独有数据、无恢复义务、保留期限和实时替代恢复证明缺一即保留。达到条件仍 delete_allowed=false，白名单不代表执行。

旧数据库分类复用 P05 角色候选与已知引用文件。候选类别不晋升为可删资格；未证明完成/无引用/恢复闭包的字段保持 UNKNOWN。P06 M5 口径和其他类别内的引用/唯一性未知分开核算。逻辑分类容量、已复制物理容量、候选可释放容量、已实际释放容量分开报告。

## 未覆盖消费者和迁移限制

- images/sync.py 仍把记录路径直接交给 derivative service。
- exporting/service.py 仍用 Path(asset.master_image_path)。
- scripts/build_dictionary.py 等历史字典脚本依赖配置/历史 basename；数字原文索引仍须档案可访问。
- 数据库 commit/run/artifact 记录中的历史绝对路径不修改；P2 提供显式 adapter，尚未替换所有旧调用。
- 活跃部署记录原样保存在候选；本次没有将其改成新的生产入口或配置 Cookie。
- 候选只验收离线存储/来源/恢复能力；没有执行真实官网 daily、生产发布或跨电脑凭证恢复。

P3 前必须完成旧消费者兼容、外部来源/审批/Resume 闭包、图片缺索引处理、代码及私有环境重建、单活写者控制和授权切换演练。

## 2026-10-14 真实验证补充
真实 ActionData（最低运行清单、当前 Snapshot、5654 张 CURRENT 原图）、8 个归档样本及 Scratch 恢复验证通过。现有正式导出按原 commit/version 保存，衍生图只按原图 Hash/Profile 绑定。既有 ZIP 当前不可访问，相关操作停止并记录 ASSET_UNAVAILABLE；39 个当前图片索引缺口不被伪补齐。全量回归 1782 PASS，远端结果与最终容量以独立验收证据为准。

P2 合并前复核补强：替代恢复证据不能指向待清理文件自身或同一文件的 hardlink/path alias；samefile 门禁阻断循环恢复证明，测试覆盖两种形式。旧文件删除仍禁用。

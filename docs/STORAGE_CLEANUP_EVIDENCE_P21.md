# P2.1 旧数据库清理资格证据（独立分支；未部署）

P2 经 PR #11 正常合并 main，合并 SHA 14b3747。合并前修复循环恢复证明：待清理文件自身或其同一 File ID 的别名不能作为替代恢复点；新 HEAD 46269e7 本地/双平台 CI 验证后合并。没有激活生产。

## 复用与职责

cleanup_evidence 是 P2 storage_lifecycle.cleanup_preview 的输入证据适配器，不新增删除系统。
P1 Backup/restore/retention 与 P2 Scratch 不改变。旧资产证据契约 LEGACY_CLEANUP_EVIDENCE_V1 加严 file ID、immutable 完整性/外键、绑定终态、生产依赖、实际保留政策及独立恢复责任核对。默认缺证据保留；旧资产 delete_allowed 永远 false。

配置 storage_foundation.cleanup_review 集中限制首批、来源文件/总读取、完整 Hash 和配对数量。脚本 scripts/storage_cleanup_review.py 只读既有清单与明确相关资料，不扫描全部磁盘，不连接活跃 PRIMARY，不创建旧数据库副本，不修改 WAL/SHM。

备份 Hash 只能与同一 JSON 对象的 backup/backup_path 绑定；不能串用邻近 Hash。恢复演练完成不能代替整个任务终态。终态必须 SUCCESS、可解析 finished_at、精确 run_id 和数据库绑定。QA PASS、目录、时间、文件名都不是终态或保留释放证明。

引用覆盖 configuration/manifests/database_records/runtime_logs/recovery_lists/review_apply/active_tasks/code_consumers。
精确完整路径和嵌入命令路径是观察引用；不以 basename 推断。有限搜索、外部任务和动态/相对路径未闭合时 references_verified=false，reference_count=null。观察引用数单列，不能冒称有效引用全数。

## 真实执行与范围限制

首批 14 份/约 17.035GB，跨任务目录、日期和容量。取得实际备份/演练记录，并有限读取当前生产及开发代码、配置、Manifest、Review/Apply/运行证据；SQLite 路径字段取自已验证闭合 P1 副本。
3 组新的完整 Hash 配对读取约 7.694GB，两文件同内容，immutable integrity/FK 通过；另 1 组复用 P2 完整 Hash 及完全相同 source signature。恢复证明适配到既有 status/backup_sha256 契约，无新整库复制或长期 restore-check。

首批白名单 0。共性阻断是 owner-task 终态/释放、全引用覆盖、适用保留期限和独立恢复职责。额外 Hash 不能解决这些缺口，因此不机械扩大完整核验。89 restore-check 和 193 开发副本只补全当前存在/大小/file ID 与有限引用概览；其余 75 restore-check 和全部开发副本不冒称已经深度认证。

历史图片 ZIP 和 39 CURRENT 图片索引作为独立兼容清单；不下载、不猜替代 ZIP、不让图片问题阻断无关数据库核查。

## 最小下一步

对已经具有同内容及有效原备份的 4 份候选，补精确任务关闭/Resume 撤销证据、受控消费者与外部任务引用闭包、明确的独立回滚职责释放和适用保留政策，重新跑既有 preview。不能自动签署这些缺失事实。
资格成功后仍需用户单独授权实际旧文件删除。P2.1 只提交审核工具、逐文件证据、阻断/白名单和验证报告，不合并本分支、不部署或启动 P3。

补充：10份开发副本实际immutable身份/引用试审（9识别、1未识别需复核），无完整Hash扩批。保护路径及其samefile别名在SQL连接之前阻断；新的回归覆盖这一保护。旧源代码、部署、PRIMARY/WAL/SHM不改。

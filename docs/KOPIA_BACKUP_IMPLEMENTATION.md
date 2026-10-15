# Kopia Backup V1 实现

`operations/kopia_backend.py` 把 Kopia CLI 封装为可替换的 `BackupBackend` 接口。
它只接收由资产管理层准备的明确目录；PRIMARY 的一致性仍由 P1 Verified Backup 负责。
模块不导入 daily-run，不创建调度任务，不改业务数据库 Schema，不提供删除快照或仓库维护命令。

## 接口和默认值

`KopiaBackend(BackendConfig, BackendCredentials, workspace)` 支持：

| 方法 | 行为与结果 |
| --- | --- |
| `probe()` | 检查 CLI、版本与 S3 参数支持；禁用时不启动子进程 |
| `create_repository(creation_authorized=False, creation_grant=None)` | 明确授权后建立新仓库；返回真实 `repository_id` |
| `connect(readonly=False)` | 必须给出预期仓库 ID，并在连接后核对 `uniqueIDHex` |
| `create_snapshot(source, backup_set_id, upload_limit_bytes, resume_only=False)` | 创建带业务集合标签、来源摘要和保留 pin 的快照 |
| `list_snapshots(backup_set_id=None)` | 返回受控字段列表，区分 manifest snapshot ID 与 root object ID |
| `verify_snapshot(snapshot_id)` | 执行 `--verify-files-percent=100`，要求零错误和完整预期文件数 |
| `restore_snapshot(snapshot_id, target)` | 恢复到新建、私有、独立目录，禁止覆盖和删除额外文件 |
| `repository_status()` | 每次核对仓库身份及已禁用 actions 的配置 |
| `storage_usage()` | 统计对象实际长度、快照数、逻辑容量和本机缓存 |
| `disconnect()` | 断开专用连接；不清理仓库对象 |
| `set_deadline(monotonic_deadline)` | 接受操作层的整个任务截止时间，限制 CLI 和来源摘要计算 |

默认 `enabled=false`，并发为 1，单命令时限 300 秒，只读操作最多重试 1 次。
并发上限为 4，只读重试最多 2 次。写命令不自动重试。
`filesystem` / `local` 不需要云端凭证。`oss`、`cos`、`s3` 使用同一受控 S3 连接接口。
默认 `purpose=business`；单独人工测试仓库可显式选择 `synthetic_test`。

所有路径均由配置提供。仓库、私有运行目录、备份来源和恢复目录必须相互隔离。
已有仓库目录即使为空也不能由 create 接口重建；首次创建只接受不存在的路径及已存在的父目录。
恢复目录也必须不存在，父目录由操作层经过预算、保护根校验后准备。

## 来源、快照与 Resume

快照的 `backup-set:<id>` 标签标识业务操作；`source-sha256:<digest>` 对排序后的
相对路径、文件长度、逐文件 SHA-256 列表取摘要。创建前后重新核对来源，避免同一
集合 ID 复用同长度但已变更的文件。文件身份变化、符号链接、Windows reparse point、
hardlink、特殊文件和 `.kopiaignore` 都阻断。

每次创建前查询所有主机的相同标签，包括相同内容和不完整快照。恰好一份完整快照，且
路径、文件数、长度、来源摘要一致时直接复用。多份、不完整或不同来源均报冲突。
超时或非 JSON 的未知写结果产生 `KOPIA_WRITE_OUTCOME_UNKNOWN`；操作层的持久日志随后
只能使用 `resume_only=True` 查询已提交结果。未发现结果时返回
`SNAPSHOT_OUTCOME_UNRESOLVED`，不再次提交快照。

快照附加 `action-backup-retention` pin，自动维护禁用。保留责任、审批、失效预览由上层
实现；本接口不会因为 Kopia 默认 retention reason 而删除任何恢复点。

## 恢复验证的范围

Kopia 的 100% verify 读取每个不同的文件对象：两个相同文件可仅产生一次对象读取。
因此校验 `expectedTotalFileCount` 与快照文件数一致，并检查零错误及确有文件读取；不把
去重后的 `readFileCount` 误当逻辑文件数。结果分别报告 `file_count`、
`unique_file_objects_read` 和 `unique_object_bytes_read`。

适配器返回的 `RESTORED` 和 `SNAPSHOT_VERIFIED` 均带
`business_restore_verified=false`。完整业务恢复仍由上层核对 BACKUP_SET_V1、逐文件 SHA-256、
SQLite integrity/foreign keys/schema/commit、投影关系及资产绑定后判定。
缺失快照和缺失来源返回 `ASSET_UNAVAILABLE`，不生成 `SOURCE_EMPTY`。

## 凭证和命令隔离

`BackendCredentials` 的所有字段不进入 repr；配置对象不包含密钥。
环境变量入口为 `ACTION_KOPIA_LOCAL_PASSWORD`，或云服务商对应的
`ACTION_KOPIA_OSS_PASSWORD`、`ACCESS_KEY`、`SECRET_KEY`、`SESSION_TOKEN` 等。
仓库密码与云端凭证始终独立。

每个子进程使用明确的操作系统环境变量白名单。只有实际 Kopia 仓库命令额外获得所需
`KOPIA_PASSWORD` 和 AWS 凭证变量。本地后端不接收云端密钥。
CLI 版本探测及 Windows `whoami` / `icacls` 不继承应用密钥。
凭证不进入 argv、普通报告或异常消息；原始 CLI 输出只在内存中解析，不导出。

每次会话使用新建的私有目录，Windows 去除继承并仅授权当前用户与 SYSTEM；其他系统
使用 0700。CLI 连接信息和缓存留在此受保护目录，其内容不进入备份集合或报告。
命令固定禁用凭证持久化、系统 credential manager、自动维护、actions、更新检查、
文件日志、内容日志、仓库日志和快照通知。所有 S3 Endpoint 必须为 HTTPS 主机地址，
不允许 userinfo、query、非 443 端口或关闭 TLS 验证。

## 云端新建授权

云端创建除了 `creation_authorized=True`，还需要专门的 `creation_grant`：

- `approved_by`、`approval_id`、带时区且未过期的 `expires_at`。
- 与配置完全一致的 `provider`、`bucket`、`endpoint`、`region`、`prefix`。
- `empty_prefix_evidence_id`、整数 `observed_object_count=0`、最近 5 分钟的
  `empty_prefix_observed_at`，指向调用者保存的实际空前缀观察证据。

这是调用者提供的范围证据，报告明确标记 `CALLER_SCOPED_OBSERVATION`，不冒称适配器
独立完成 Bucket 空检查。Kopia 原生 create 的已有仓库拒绝行为仍保留，不传覆盖、重建
或删除选项。生产首次业务上传的单独审批由操作层再校验。

原 `actionskutracker-kopia-poc/` 根以及 `oss-*` 测试仓库拒绝写入。
仅 `purpose=synthetic_test` 的新 `actionskutracker-kopia-poc/storage-v3-<独立标识>/`
可在上述授权和空前缀证据齐全时使用；操作层还需验证人工测试内容，拒绝真实业务资产。
Kopia 创建和提交可能需要删除自己刚建立的会话标记，不能据此授予删除既有对象的权限。

## 预算和统计口径

快照写入传递 `--upload-limit-mb`，向下取整为 MiB；低于 1 MiB 的预算拒绝。
该限制是 Kopia 上传限制，不能作为云厂商计费流量的精确上界；元数据、重试与请求费用
需要操作层的保守预算和显式估算授权。

`blob list --json` 的对象长度之和标记 `OBSERVED`，是当前可观察仓库对象占用。
它不等于本次上传量，也不等于计费单。无法从 CLI 可靠测量的上传/下载/计费字节均为
`UNKNOWN`，不把文件逻辑大小或缓存读取量伪装成网络用量。

## 验收位置

`tests/test_kopia_backend.py` 和 `tests/test_kopia_connectivity.py` 使用合成凭证和模拟 CLI，
验证默认关闭、参数类型、子进程凭证隔离、身份、来源变更、幂等、超时、只读限制、恢复
覆盖保护、计数及日志脱敏，可加入 CI_SAFE。

独立本机实际验收使用官方 Kopia 0.23.1，已验证人工文件的仓库建立、快照、标签复用、
对象占用、100% 检查、断开重连和 SHA-256 恢复。正式任务的 Gate 2 / Gate 4 实际输出、
版本差异和容量统计由外部 StorageAuditOutputs 验收报告保存。
既有 OSS PoC 的 ROUNDTRIP_PASS 属于历史实测证据；本轮 OSS 运行若缺少新 STS 或范围授权，
必须单独标记 BLOCKED，不能以本地验证或旧 PoC 替代新代码的云端验收。

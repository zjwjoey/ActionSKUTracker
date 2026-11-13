# 统一备份池：第二阶段开发

本阶段新增受控的本地副本池和容量门禁。配置默认关闭，不迁移、不登记、不删除旧目录，不修改生产数据库、Master 或业务 Schema，不启用 Kopia。第三阶段再统一中文修复、Apply 和全部写入口的任务级复用；第四阶段接入现有 Kopia。不能把本阶段视为所有旧入口已经迁移完成。

## 入口和布局

`backup_pool.enabled: true` 时，Operations BACKUP 通过同一池 API 创建恢复基准；关闭时原入口保持不变。开发验收仅使用生成的 SQLite 数据库和新建沙箱。

统一根目录默认为 `runtime/backup_pool`，相对 `project_root` 解析，布局如下：

- `pool.json`：根目录身份、固定容量策略、保护路径，不可覆盖。
- `index.json`：带校验封印的生命周期登记，串行锁内原子替换。
- `active/`：已登记完整数据库备份及其验证清单。
- `staging/`：恢复演练、可再生临时副本、尚未发布的控制记录。
- `cleanup/`：不可覆盖的恢复证明、删除意图、删除结果和容量检查记录。
- `leases/`、`pool.lock`：进程退出自动释放的系统文件锁；锁文件本身保留。

独立管理命令不读取生产配置，不连接生产数据库。示例使用显式的新开发池路径：

```powershell
python -m action_tracker backup-pool --root F:/ai文件/example/runtime/backup_pool init
python -m action_tracker backup-pool --root F:/ai文件/example/runtime/backup_pool usage
python -m action_tracker backup-pool --root F:/ai文件/example/runtime/backup_pool cleanup-preview
python -m action_tracker backup-pool --root F:/ai文件/example/runtime/backup_pool cleanup
```

`capture --source GENERATED.sqlite3 --task-id TASK --code-version SHA` 使用 SQLite Backup API 创建基准，再独立复制到池内恢复验证。成功后自动释放恢复测试副本，仅保留证明。`verify --asset-id ID` 重新检查文件身份、Hash、恢复证明、数据库身份和完整性。`finish-task --task-id TASK --state COMPLETED|FAILED|UNKNOWN` 登记任务结束并触发临时副本退出。CLI 失败返回非零状态，容量/清理失败输出原因和完整报告。

## 5 GiB / 4 GiB

使用二进制 GiB：触发值 **5,368,709,120 bytes**，目标 **4,294,967,296 bytes**。生产配置禁止改成其他容量；缩小策略只用于生成数据的单元测试。

Windows 用 `FileStandardInfo.AllocationSize`，POSIX 用 `st_blocks * 512` 统计实际文件占用；逻辑长度单独列示。同一文件身份只计一次，未知文件也计入容量。目录元数据不计入文件分配总量。磁盘可用空间变化另行观测，包含其他进程影响，不冒充可归因的释放量。

新写入前在全池系统锁内检查：当前实际占用 + 其他进程未消耗的预约 + 本次峰值估算 + 控制记录余量。数据库备份同时预约基准和恢复副本。预测或当前达到 5 GiB，先删除符合条件的已登记文件，到 4 GiB 以下才允许继续；无法达标就阻断，不能为了腾空间删除受保护资产。任务结束主动释放符合条件的临时副本。无后台扫描定时器，自动管理发生于池入口、写入完成、任务结束和显式检查点。

删除先获得资产 lease 和全部文件的系统锁，核验文件 ID、单硬链接、大小和 SHA-256，再检查生命周期，记录意图，逐文件删除，记录实际占用和磁盘可用空间变化。任何异常明确失败；组内部分删除后中断也保留意图记录，不能假报成功。Windows 使用排他文件句柄；POSIX 使用协作式 flock，不能抵御不遵守锁的外部程序，删除前会再次核对路径身份。

## 保留的恢复保护

- 不接管非空旧目录；拒绝与保护路径重叠，拒绝链接、junction/reparse、硬链接、越界删除。
- RUNNING、FAILED、UNKNOWN 任务，CREATING/FAILED 资产、在用文件、引用未释放、恢复职责未解除、未验证副本不删除。
- 最新有效恢复点不删除；唯一恢复点不删除。旧基准须显式解除职责，并具备不同资产 ID、完全相同 Hash/数据库身份且独立恢复验证通过的替代基准，才有资格淘汰。不同数据库版本不能互当等价替代。
- 恢复证明缺失或改变会阻止淘汰；恢复验证失败保留失败副本和原基准。
- 临时副本默认为恢复职责未解除；只有调用者明确声明可再生、可丢弃，任务成功且无引用/锁才自动退出。不能将 SQLite 文件冒充普通临时文件绕过恢复验证。
- 未登记文件计入容量、展示于状态，但永不自动删除。控制日志和故障证据保留；它们持续增长到容量不可满足时，同样阻断。
- 操作结果与任务提交分开：业务已经提交后维护失败报告 DEGRADED/非零退出码，不声称回滚了业务数据。

## 验收范围

新增 CI-safe 测试包括真实生成的 SQLite 备份与恢复、原文件 Hash 不变、预测门禁、未知/失败/唯一/最新保护、Hash/登记篡改、硬链接、并发预约与双进程清理、CLI、Operations 可选接入和恢复复用。原有业务回归继续执行。实际 5 GiB/4 GiB 文件写入与空间回收验收在独立沙箱执行，不加入普通 CI 的磁盘负担；测试证据存放于开发任务工作区。

尚未实施：全写入口迁移、任务批次共享与增量日志、Kopia 生产接入、历史备份清理、自动部署。生产启用须待后续阶段审查验收。

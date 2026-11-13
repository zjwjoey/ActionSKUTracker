# 第四阶段：复用任务基准的 Kopia 往返验证

状态：隔离开发能力。未合并 main、未部署，未接入 daily-run 自动上传或生产清理。
继续使用已有 `KopiaBackend`、`backup_sets` 的业务计划与恢复校验、
`OperationPolicy` 的运行限制和第二阶段备份池的容量/持有句柄删除保护。
旧 capture/lifecycle/operator API 保留兼容，不在本阶段自动迁移历史仓库或备份。

## 复用及恢复范围

- 输入是已登记、SQLite Backup API 生成并经过独立恢复验证的任务基准 asset ID。
  不再另行 capture PRIMARY，也不因为分批或重试再复制一次完整任务基准。
- 同一基准、仓库身份和投影来源路径形成持久 job；一次 stage、一个固定快照、
  一次独立下载恢复。独占 job 锁、持久资产预留和写入前日志共同约束重试。
- 任务基准代表任务开始前的恢复点，**不等同于任务结束后的最新 PRIMARY**。
  不读取生产库来推断最新状态，也不允许将较新的 Master/CSV 混入旧基准。
- 提供 master/known/offline 三项时使用 CORE_BACKUP：逐项验证 Commit 与 export_sync
  SUCCESS、三项内容 Hash。均不提供时使用 CURRENT_BUSINESS_BACKUP，验证数据库中的
 完整 export_sync 记录，不声称恢复了未提供的兼容投影。
- 保留完整文件 allowlist、manifest seal、长度/SHA256、Schema 2.0、Commit、
  integrity_check、foreign_key_check、精确 export_sync、仓库 ID、快照标签与清单绑定。
  Kopia 原生 verify 保持 100% 文件校验，不以抽样或计数相等替代内容/业务检查。

## 中断和释放

预留资产 ID 在建目录前持久化；若 pool 已预留但 job 回执未保存，恢复时认领同一
task/role/plan/source 的唯一预留，拒绝歧义。stage 和恢复目录均不覆盖已有目录。
完整副本丢回执后重新全量校验并复用；不完整、未知、损坏副本保留并阻断，不删除重来。
上传结果不明只按原标签查找快照；无法唯一确定则阻断，不盲目再次上传。

只有远端 100% 校验和独立业务恢复都通过，才保存 sealed roundtrip 证据，授权释放
本 job 的 stage/restore。删除前核对目录身份、精确文件集合、每个文件身份/Hash，
持有全部待删文件句柄；逐文件删除后只 rmdir 空的同身份目录，不递归删未知内容。
清理不完整要报告错误；已有证明可重试清理，无需再次 stage/restore。
已成功 job 再运行仍验证原仓库与快照并进行原生 100% 校验，不生成新完整副本。
任务基准、恢复报告、引用、状态日志、快照 pin 和仓库对象继续保留。

所有 pool/kopia 仓库对象、会话、缓存与日志均计入实际占用。
上传、校验和下载前额外预留 I/O 增长预算；私有 Kopia 客户端使用原生 cache set，
内容缓存硬限 16 MiB、元数据硬限 8 MiB，不改变仓库保留或 maintenance 策略。
5 GiB 准入与 4 GiB 回收目标不变；基准+stage+仓库+独立 restore 的安全峰值超过
预算时明确阻断，不提前删恢复副本腾空间。不能承诺所有大库都适合默认 5 GiB 本地池。
缓存仍是受限的私有会话数据，不声称清空全部缓存或释放全部仓库占用。

## 显式开发入口

```text
python -m action_tracker pool-kopia --root <已初始化的隔离池> \
  --base-asset-id <已验证任务基准ID> --repository-id <64位仓库ID> \
  --cli-path <Kopia可执行文件> [--master <path> --known <path> --offline <path>] \
  --execute
```

不传 `--execute` 则 DISABLED，连备份池也不打开。密码仅通过
`ACTION_KOPIA_LOCAL_PASSWORD` 输入；不放在命令参数/日志/报告中。
仓库必须已存在于该池已登记的 `kopia/repository`，私有会话限 `kopia/sessions`。
手动入口不创建或重置仓库、不加载生产设置、不安装任务、不处理旧历史副本。
本阶段 pooled seam 明确仅支持 filesystem/local；已有云适配器保留，但没有借本轮
授权开启云端业务上传、快照 expiration、生产自动清理或生产恢复覆盖。

## 验收边界

CI_SAFE 使用生成 SQLite 与假后端验证复用、失败保留、崩溃预留认领、容量与并发。
LOCAL_ONLY 真实验收使用官方 Kopia 0.23.1、新建独立 filesystem 仓库、生成 SQLite
及三项投影，经正式 `pool-kopia --execute` 子进程首跑和重启再跑验证。
真实仓库验证不是 fake acceptance；生成数据库也不冒充生产业务库恢复。
验收保存仓库/快照 ID、实际 Kopia 校验结果、独立业务恢复证明、源及基准前后 Hash、
实际占用和临时副本删除结果。云端真实仓库和生产数据恢复未在本轮验证。
所有证据放在独立 ScratchTask；生产 PRIMARY、历史备份和现有工作树只做保护核对。

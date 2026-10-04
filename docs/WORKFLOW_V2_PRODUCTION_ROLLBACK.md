# Workflow V2 生产回滚

## 回滚触发条件

出现数据库完整性错误、事实提交回归、翻译批次越界、导出校验失败或运行时异常时，立即停止后续任务并回滚代码。不要删除 PRIMARY 数据库或历史队列。

## 代码回滚

回滚目标是切换生产代码目录回旧 head：

```text
fix/naming-history-export-20260917
3a9df7af807875b97d4eab1b5d3bb57c69f5c041
```

切换前保存现场，确认没有未提交的生产代码改动；不要把部署分支合并回命名历史分支。

## 数据回滚

优先使用部署清单中的 SQLite 一致性备份恢复到单独的临时路径并运行 `PRAGMA integrity_check`。只有 Owner 明确授权并停止所有写入进程后，才可以用该备份替换 PRIMARY。配置恢复使用同目录的 `settings.yaml.pre_cutover`。

备份目录：

```text
F:\ActionSKUTracker\runtime\backups\workflow_v2_production_20261004\
```

恢复后必须重新运行 `workflow_v2_production_preflight.py`（显式指定 `workflow_v2_production_profile.yaml`）、`db-validate-production` 和最小 fixture canary，并记录新的 commit/run 证据。

## 回滚边界

回滚只改变经过授权的生产代码或备份恢复动作。不得清空翻译队列、删除审计记录、重写事实版本，或把西语写入中文字段。Qwen 密钥不从日志、清单或报告中输出。

# Final Approval & Provenance Gate Hotfix

本轮只收口 `feat/master-dictionary-export-closure-v1` 的 Approval、Provenance、Source Gate，不改采集、Presence、Lifecycle、Cloudflare、图片、Scheduler、Qwen、训练集或真实 PRIMARY。

## 已实现

- `apply_localization_correction()` 现在是兼容性 PREPARE 入口，只创建 `PATCH_CREATED` 并返回 `patch_ids`，不会自动 `PATCH_APPROVED` 或 `PATCH_APPLIED`。
- Knowledge candidate 缺少 `approval_status`、处于 `PENDING`、缺少 `approved_by/approved_at/approval_evidence` 或审批主体不是 `human:`/`service:` 时 fail closed。
- `validate_patch_apply()` 与 Production Apply 共用事务安全验证器；`source_allowlist`、base commit、old value、source hash、字段和审批事件均使用同一合同。
- Production Apply 将 approval actor/time 写入字段 provenance；Apply operator 只记录在 `PATCH_APPLIED` event，不再冒充审批者。
- Category approval 必须提供当前 `current_source_hash`，并与 queue evidence hash 一致。
- Fresh 与 Active PRIMARY patch lifecycle 统一允许 append-only `PATCH_APPLIED → PATCH_REVOKED`；撤销不删除历史、不直接回滚事实。
- Research Release 在 aggregate hash 之外逐字段校验六个中文字段的 `source_hash`；`APPROVED_SOURCE_ABSENT` 同样受当前 hash 绑定。
- Release exception 必须绑定 issue、SKU、字段、source hash、审批/创建时间、理由和证据，审批主体必须为 `human:`/`service:`，TTL 不得超过 90 天；无效或 source hash 过期的 exception 不会抑制问题。
- 新增 `tests/test_final_approval_provenance_gate.py`，并加入 CI 白名单；所有测试模块均被白名单覆盖。

## 安全边界

生产开关保持关闭：dictionary apply、Knowledge apply、Localization AI、Scoped Dictionary、translation AI、auto approval 均为 `false`。本轮测试只使用临时 SQLite；真实 PRIMARY 不写入。

## 验收命令

```powershell
$env:PYTHONPATH="src"
python -m pytest -q
```

本地全量回归：`458 passed`。

远端 exact-head CI 需要在推送新提交后重新核对 Ubuntu/Windows 的 `head_sha`，不能复用旧提交 `47f18bd` 的 CI 结果。


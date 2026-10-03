# Repository Consolidation Closure V1

结论：`PARTIAL_PASS`（代码、文档、分支和 CI 已完成；main branch protection 仍需管理员配置）。

审计日期：2026-10-04
范围：仓库结构、文档契约、翻译安全默认值、Stage6 provenance v2、Scrapling detail shadow v3、命名历史迁移盘点。

## A. 基线与远端

- 基线 main：`b47e4c56a0274fdddceff347e2571d77c1476014`。
- 闭环分支从该基线创建，未使用旧 integration 分支的 dirty state。
- `git fetch --all --prune` 已通过临时清除 Git proxy 配置成功；`origin/main` 仍为上述基线。
- 主线合并 CI run `37125971802`、Scrapling `37128908030`、Closure `37136163546`、Stage6 `37136385976` 均为 Windows/Ubuntu 成功。

## B. AGENTS 与架构契约

- PASS：AGENTS 已明确 SQLite PRIMARY 是生产主链和唯一正式读事实源。
- PASS：正式写入必须经过 CommitBundle、QA、integrity、base commit、事务和 export boundary；schema/commit/PRIMARY 结构变更需单独项目、迁移和回归测试。
- PASS：Presence 在 Detail 前冻结；Detail 不决定 Presence/Lifecycle；历史源和生产数据只读边界保留。
- PASS：NO_BRAND 展示策略已统一到 AGENTS、CURRENT_STATE、DATA_MODEL、EXPORT_PROFILE、命名和本地化标准。

## C. 当前状态与分支库存

- PASS：`docs/CURRENT_STATE.md` 已重写为当前事实，不再混写旧分支数量、旧生产快照或过期验收结论。
- PASS：`docs/audits/REPOSITORY_BRANCH_INVENTORY_20261003.md` 使用 `origin/main@b47e4c5` 重算 behind/ahead，并明确以 `git cherry origin/main origin/<branch>` 的 `+` 行作为 `unique_patch_count`、`-` 行作为 patch-equivalent；已合并分支标为 `SUPERSEDED_AFTER_CONSOLIDATION`。
- PASS：旧分支、实验分支和迁移源均保留；本轮未删除远端分支或移动 recovery tag。
- PASS：五个 recovery tag 均仍存在，且已核对远端 annotated tag 的解引用提交 SHA。

## D. 翻译安全不变量

- PASS：`knowledge.production_apply_enabled=false`。
- PASS：`localization.production_apply_enabled=false`。
- PASS：`localization.auto_approval_enabled=false`。
- PASS：`localization.ai.enabled=false`。
- PASS：`translation.ai_enabled=false`、`translation.auto_approval_enabled=false`。
- PASS：`knowledge.fallback_to_spanish=false`；缺失配置由 parser 归一化为关闭。
- PASS：缺失、过期、源损坏和未确认字段进入 `PENDING`/`REVIEW_REQUIRED`；formal path 不把西语复制到中文。
- PASS：`display_fallback=ES` 仅为 presentation-only，已有回归测试证明它不能把 export plan 变成 release-ready。
- PASS：NO_BRAND 只移除已识别品牌/IP span，不删除型号、标准、接口、技术 token、数字或单位。
- `tests/test_translation_safety_defaults.py`：6 passed；CI 白名单和完整回归均包含该测试。

## E. Stage6 provenance v2

- 分支：`fix/stage6-provenance-v2`
- 基线：`b47e4c5`
- 仅 cherry-pick：`850c49832c48a21f3847cd63447e6fd4ebe11b6b`、`bf2bd7878ba1110b95d389f865ee1aea6ddd66c5`；本轮提交 `e7490997254b20e1dad29f70977321224044d78e`、`ed1205b7550433d241f8dd83b14af8fbe26aa859`。
- 明确未 cherry-pick：`add8734aaa72c8794a72de97bf893312bda9e5e5`
- 专项测试：`25 passed`（含运行时 Git 证明、Git 失败闭锁和混合排除状态测试）。报告不再写死旧 SHA/旧分支；readiness CSV 保留所有候选并统计 total/eligible/excluded/previewed/blocked。

## F. Scrapling detail shadow v3

- 分支：`experiment/scrapling-detail-shadow-v3`
- 基线：`b47e4c5`
- 仅 cherry-pick：`6a8ffe328ca1a299a5c5859ab674c1fa2c149e65`
- 明确未 cherry-pick：`1a68003198c66d4bc04cfc9b332a45f7a1e2f64d`
- 代码、fixture、脚本和依赖均位于 experiment 路径；依赖文件为 `requirements-experiments/scrapling-detail-shadow.txt`，未接入生产 requirements、daily-run 或 PRIMARY。
- 专项测试：`20 passed, 28 warnings`（仅 lxml `strip_cdata` 弃用警告）。

## G. Naming history

- 文件：`docs/audits/NAMING_HISTORY_MIGRATION_INVENTORY_20261003.md`；当前旧命名分支只保留迁移证据，不直接合并。
- 分类：`MIGRATION_ONLY`；未把旧 Stage5/Stage6 数据资产或历史字典批量合入主线。

## H. 文档、配置与 CI

- PASS：README、AGENTS、CURRENT_STATE、ARCHITECTURE、DATA_MODEL、QA_RULES、Translation docs、export profile 和 settings 的 PRIMARY、Presence-before-Detail、NO_BRAND 与 Translation V1 默认值一致。
- PASS：完整本地回归：`705 passed`。
- PASS：按 `.github/workflows/ci.yml` 使用 `tests/ci_safe_tests.txt` 执行：`705 passed`。
- PASS：未删除测试、未修改 CI 白名单以规避失败、未安装实验依赖到生产 requirements。

## I. 平台保护与剩余阻塞

- `BRANCH_PROTECTION_REQUIRED`：GitHub API 返回 main 未保护（404），rulesets 返回空数组；本任务未修改 GitHub 设置，需管理员另行配置 required checks、review 和禁止直接 push/force-push。
- 本闭环已完成本地与远端分支/CI 审计；分支保护仍需管理员动作，因此结论为 `PARTIAL_PASS`，提交 owner review。

## Final report

Repository Closure Final Fix
Base main:
b47e4c56a0274fdddceff347e2571d77c1476014
Closure branch:
chore/repository-consolidation-closure-v1
7b153b72e805b4b5dbe8b36f55d05d9eb691557d
Stage6 branch:
fix/stage6-provenance-v2
4744fba5523df5ebac5e00773cb96e1fe679979d
Branch Inventory algorithm:
git cherry
Branch Inventory:
PASS
Stage6 runtime Git provenance:
PASS
Stage6 stale hardcoded metadata:
REMOVED
Stage6 mixed exclusion state:
PASS
Stage6 targeted tests:
25 passed
Full pytest:
705 passed / 0 failures
Closure CI:
37136163546 / PASS
Stage6 CI:
37136811440 / PASS
Scrapling modified:
NO
Translation modified:
NO
Production data modified:
NO
Main modified:
NO
Remote branches deleted:
NO
Remaining blocker:
BRANCH_PROTECTION_REQUIRED
Final code status:
PASS
Repository closure status:
PARTIAL_PASS
Recommendation:
READY_FOR_PR

# Repository Consolidation Closure V1

结论：`PARTIAL_PASS`（代码、文档、分支和 CI 已完成；main branch protection 仍需管理员配置）。

审计日期：2026-10-03  
范围：仓库结构、文档契约、翻译安全默认值、Stage6 provenance v2、Scrapling detail shadow v3、命名历史迁移盘点。

## A. 基线与远端

- 基线 main：`b47e4c56a0274fdddceff347e2571d77c1476014`。
- 闭环分支从该基线创建，未使用旧 integration 分支的 dirty state。
- `git fetch --all --prune` 已通过临时清除 Git proxy 配置成功；`origin/main` 仍为上述基线。
- 主线合并 CI run `37125971802`、闭环 `37128907845`/`37129203699`、Stage6 `37128907786`、Scrapling `37128908030` 均为 Windows/Ubuntu 成功。

## B. AGENTS 与架构契约

- PASS：AGENTS 已明确 SQLite PRIMARY 是生产主链和唯一正式读事实源。
- PASS：正式写入必须经过 CommitBundle、QA、integrity、base commit、事务和 export boundary；schema/commit/PRIMARY 结构变更需单独项目、迁移和回归测试。
- PASS：Presence 在 Detail 前冻结；Detail 不决定 Presence/Lifecycle；历史源和生产数据只读边界保留。
- PASS：NO_BRAND 展示策略已统一到 AGENTS、CURRENT_STATE、DATA_MODEL、EXPORT_PROFILE、命名和本地化标准。

## C. 当前状态与分支库存

- PASS：`docs/CURRENT_STATE.md` 已重写为当前事实，不再混写旧分支数量、旧生产快照或过期验收结论。
- PASS：`docs/audits/REPOSITORY_BRANCH_INVENTORY_20261003.md` 使用 `origin/main@b47e4c5` 重算 behind/ahead/unique，并将已合并分支标为 `SUPERSEDED_AFTER_CONSOLIDATION`。
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
- 仅 cherry-pick：`850c49832c48a21f3847cd63447e6fd4ebe11b6b`、`bf2bd7878ba1110b95d389f865ee1aea6ddd66c5`
- 明确未 cherry-pick：`add8734aaa72c8794a72de97bf893312bda9e5e5`
- 专项测试：`21 passed`（`tests/test_stage6_legacy_provenance.py`、`tests/test_stage6_preview.py`）。

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
- PASS：完整本地回归：`688 passed in 60.82s`。
- PASS：按 `.github/workflows/ci.yml` 使用 `tests/ci_safe_tests.txt` 执行：`688 passed in 58.40s`。
- PASS：未删除测试、未修改 CI 白名单以规避失败、未安装实验依赖到生产 requirements。

## I. 平台保护与剩余阻塞

- `BRANCH_PROTECTION_REQUIRED`：GitHub API 返回 main 未保护（404），rulesets 返回空数组；本任务未修改 GitHub 设置，需管理员另行配置 required checks、review 和禁止直接 push/force-push。
- 本闭环已完成本地与远端分支/CI 审计；分支保护仍需管理员动作，因此结论为 `PARTIAL_PASS`，提交 owner review。

## Final report

Repository Consolidation Closure V1
Base main:
b47e4c56a0274fdddceff347e2571d77c1476014
Closure branch:
chore/repository-consolidation-closure-v1
be05e10 (remote tip verified before this final audit commit)
Full pytest:
688 passed / 0 failures
CI-safe:
PASS — 688 passed
AGENTS alignment:
PASS
CURRENT_STATE:
PASS
Branch inventory:
PASS
Translation safety:
PASS
Stage6 v2:
fix/stage6-provenance-v2 / 47971fd / 21 passed
Scrapling v3:
experiment/scrapling-detail-shadow-v3 / c1a3ffc / 20 passed, 28 warnings
Naming-history:
MIGRATION_ONLY
Production data modified:
NO
Remote branches deleted:
NO
Main modified directly:
NO
Production boundary:
REAL_COLLECTION_NOT_RUN
PRODUCTION_PRIMARY_NOT_MODIFIED
DICTIONARY_PRODUCTION_APPLY_NOT_RUN
LOCALIZATION_PRODUCTION_APPLY_NOT_RUN
Remaining blockers:
BRANCH_PROTECTION_REQUIRED
Recommendation:
READY_FOR_OWNER_REVIEW

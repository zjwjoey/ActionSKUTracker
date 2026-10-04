# Workflow V2 Production Final Audit — 2026-10-04

## Audited source

- Branch: `deploy/workflow-v2-production-20261004`
- Audited ref: `production/workflow-v2-phase1-rc3`
- Audited content head: `0184a8faf00f23343598ed4d11b1721f9469d01a` (`fix: keep canary export resilient to missing formal run`)
- Publication metadata head: `921e05e737af286136843ae0a31734338354ef01`
- Naming-history branch was not merged.

The candidate tag is the immutable code boundary for this audit. Later report or
publication metadata commits must not change the audited content head.

## Evidence

- `python -m pytest tests/workflow_v2 -q`: **48 passed**
- `python -m pytest tests/test_workflow_v2_production_preflight.py -q`: **8 passed**
- `python -m pytest tests/test_translation_registry_qwen.py -q`: **73 passed**
- `python -m pytest tests/test_translation_safety_defaults.py -q`: **6 passed**
- `python -m pytest -q`: **750 passed**
- CI-safe allowlist: **750 passed locally**
- Exact-head GitHub CI for the previous publication metadata head `921e05e737af286136843ae0a31734338354ef01`: **PASS**, run `37202816201` (Ubuntu and Windows). The new candidate commit requires one fresh exact-head CI run after push.

The tests use temporary SQLite databases, fixtures, fake providers, and mock
environment variables. They do not run the production database or make Qwen or
Action requests.

## Acceptance matrix

| Gate | Result |
|---|---|
| DEPLOY_BRANCH_LINEAGE | PASS |
| NAMING_HISTORY_NOT_MERGED | PASS |
| PRODUCTION_PROFILE | PASS |
| DEFAULT_CONFIG_FAIL_CLOSED | PASS |
| PRODUCTION_PREFLIGHT | PASS |
| PRODUCTION_PREFLIGHT_TESTS | PASS |
| PRIMARY_GUARD | PASS |
| FACT_COMMIT_BOUNDARY | PASS |
| PRODUCTION_PHASE1_MODE | PASS |
| QUEUE_RUN_ID_ISOLATION | PASS |
| HISTORICAL_BACKLOG_ISOLATION | PASS |
| TRANSLATION_BATCH_LIMIT | PASS |
| BATCH_PENDING_CONTINUATION | PASS |
| BATCH_RESUME | PASS |
| TRANSLATION_REVIEW_REQUIRED_STATE | PASS |
| TRANSLATION_APPLY_PENDING_STATE | PASS |
| AUTO_EXPORT_PENDING_STATE | PASS |
| TRANSLATION_QA_FAIL_CLOSED | PASS |
| SOURCE_QA_FAIL_CLOSED | PASS |
| SUCCESS_WITH_PENDING_FINAL_STATE | PASS |
| TEMPORARY_QWEN_FAILURE_SEMANTICS | PASS |
| BACKUP_CONTRACT | PASS |
| ROLLBACK_CONTRACT | PASS |
| AUDIT_REF_FRESHNESS | PASS |
| FULL_PYTEST | PASS |
| CI_SAFE | PASS locally |
| EXACT_HEAD_CI | PENDING fresh run for rc3 |

## Runtime safety record

```text
PRODUCTION_PRIMARY_MUTATED             NO
REAL_QWEN_CALLED                       NO
REAL_ACTION_FULL_RUN                   NO
REAL_FORMAL_EXPORT_PUBLISHED           NO
MAIN_MODIFIED                          NO
```

The code fix is locally verified and will remain not ready for a new canary
until exact-head CI for rc3 is green on both required operating systems. The
current conclusion is:

```text
NOT_READY_FOR_PHASE1_PRODUCTION
```

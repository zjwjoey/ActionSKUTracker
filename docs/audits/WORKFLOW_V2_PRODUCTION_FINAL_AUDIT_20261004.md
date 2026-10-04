# Workflow V2 Production Final Audit — 2026-10-04

## Audited head

- Branch: `deploy/workflow-v2-production-20261004`
- Audited ref: `production/workflow-v2-phase1-rc4`
- Audited content head: `16c626f1063ba3757b93368ee76869545a5f83ad`
- Publication metadata head: `9021a9a5146d058c042dae80df5cfa31f7ea83cc`

The immutable RC4 tag is the code boundary. This report records evidence by audited head and publication metadata; it does not follow later report-only commits by current SHA.

## Test and CI evidence

- `python -m pytest -q`: **768 passed**
- CI-safe allowlist: **762 passed locally**
- RC4 exact-head CI: run `37206480278`, Ubuntu and Windows **SUCCESS**
- Deployment publication head exact-head CI: run `37209417046`, Ubuntu and Windows **SUCCESS**
- Preflight and runtime closure tests cover profile gates, preflight/runtime hash parity, historical/cross-midnight business-date propagation, config hash resume guard, field-level localization freshness, and stale export blocking.

## Acceptance matrix

| Gate | Result |
|---|---|
| DEPLOY_BRANCH_LINEAGE | PASS |
| RC4_IMMUTABLE_CODE_BOUNDARY | PASS |
| PRODUCTION_PROFILE | PASS |
| DEFAULT_CONFIG_FAIL_CLOSED | PASS |
| CONFIG_EVIDENCE_AND_HASH | PASS |
| BUSINESS_DATE_SINGLE_SOURCE | PASS |
| EXTRACTION_DATE_MISMATCH_BLOCK | PASS |
| ZH_FIELD_FRESHNESS_INVALIDATION | PASS |
| REGISTRY_PROJECTION_PARITY | PASS |
| STALE_RELEASE_BLOCK | PASS |
| PRODUCTION_PHASE1_MODE | PASS |
| APPLY_APPROVAL_EXPORT_DISABLED | PASS |
| QUEUE_RUN_ID_ISOLATION | PASS |
| BATCH_LIMIT_AND_RESUME | PASS |
| PRODUCTION_PREFLIGHT | PASS |
| FULL_PYTEST | PASS |
| CI_SAFE | PASS |
| PREFLIGHT_RUNTIME_HASH_PARITY | PASS |
| HISTORICAL_AND_CROSS_MIDNIGHT_DATE | PASS |
| EXACT_HEAD_CI | PASS (runs 37206480278 and 37209417046) |

## Safety record

```text
PRODUCTION_PRIMARY_MUTATED             NO
REAL_QWEN_CALLED                       NO
REAL_ACTION_FULL_RUN                   NO
REAL_FORMAL_EXPORT_PUBLISHED           NO
MAIN_MODIFIED                          NO
```

```text
READY_FOR_PHASE1_REAL_PRIMARY_CANARY  YES
```



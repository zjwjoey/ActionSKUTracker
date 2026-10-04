# Workflow V2 state machine

```text
PREFLIGHT -> BACKUP -> EXTRACT -> SOURCE_AUDIT -> SOURCE_CLEAN
  -> SOURCE_REAUDIT -> FACT_COMMIT -> DETAIL_PLAN -> DETAIL_ENRICH
  -> TRANSLATION_SOURCE_AUDIT -> REGISTRY_INGEST -> TRANSLATION_PLAN
  -> QWEN_TRANSLATE -> TRANSLATION_QA -> TRANSLATION_POLICY
  -> TRANSLATION_APPLY -> EXPORT_AUDIT -> EXPORT_WRITE -> REPORT
```

The source gates fail closed. `SOURCE_SKU_COMPLETENESS` and
`NEW_SKU_COMPLETENESS_GATE` must pass before a Qwen call. Empty optional
official fields are `SOURCE_EMPTY_VALID`; a failed detail fetch is
`PENDING_DETAIL` and blocks only dependent translation fields.

Critical dependencies are persisted. If a stage is `BLOCKED` or `FAILED`, each
dependent stage is recorded as `BLOCKED_BY_DEPENDENCY` and cannot execute
business logic. `WorkflowContext.blockers` keeps the originating stage, code,
SKU/field when available, severity and details until that stage succeeds on a
later retry.

Presence and facts are separate contracts:

```text
PRESENCE_READINESS -> safe lifecycle/Presence commit
SOURCE_FACT_READINESS -> whether today’s Spanish facts may be refreshed
TRANSLATION_SOURCE_READINESS -> field-level source availability
```

The first two states are written into the run report independently. A detail
timeout does not erase a safe Presence commit and does not prevent translation
of unrelated ready fields.

Top-level states:

- `SUCCESS`: all enabled gates and export stages pass.
- `DEGRADED`: facts committed but a retryable downstream stage is pending.
- `BLOCKED`: a non-negotiable source, translation or export gate failed.
- `FAILED`: an unexpected stage error occurred.

Resume atomically writes `workflow_state.json` after each stage start and end.
It restores the complete context, database path, records and JSON artifacts
for the translation plan/results/policy. With `--resume --run-id` and no date,
the runner locates the unique `runtime/reports/workflow_v2/<date>/<run_id>`
directory and preserves the original business date. Completed stages and
successful provider/apply/export artifacts are not repeated.

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

Top-level states:

- `SUCCESS`: all enabled gates and export stages pass.
- `DEGRADED`: facts committed but a retryable downstream stage is pending.
- `BLOCKED`: a non-negotiable source, translation or export gate failed.
- `FAILED`: an unexpected stage error occurred.

Resume reads `workflow_state.json`, skips completed stages, and reuses the
same business date and run id. Provider calls and export writes are therefore
not repeated for successful stages.

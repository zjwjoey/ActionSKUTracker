# Workflow V2 architecture

Workflow V2 is an opt-in orchestration layer. It coordinates the existing
extractor, `CommitBundle`/`ProductionWriter`, Translation System V1, QA and
export modules; it does not create a second crawler, product writer or
translation database.

The public development command is:

```powershell
python -m action_tracker data-update-v2 --date YYYY-MM-DD --fixture fixture.json --fake-provider
```

The fixture path is offline and the fake provider is deterministic. Production
settings keep `workflow_v2.enabled`, automatic translation, policy approval and
automatic export disabled. A future production adapter must pass the source,
fact commit and translation source gates before constructing a provider.

The isolated Apply canary is:

```powershell
python -m action_tracker workflow-v2-local-canary --fixture fixture.json --output runtime/canary
```

It creates a temporary SQLite database, commits fixture Spanish facts through
`ProductionWriter`, registers a Translation System V1 source/revision, approves
the fixture revision, and applies it through the existing immutable patch
coordinator. It never resolves or mutates the configured production database.

Stages are explicit: `PREFLIGHT`, `BACKUP`, `EXTRACT`, `SOURCE_AUDIT`,
`SOURCE_CLEAN`, `SOURCE_REAUDIT`, `FACT_COMMIT`, `DETAIL_PLAN`,
`DETAIL_ENRICH`, `TRANSLATION_SOURCE_AUDIT`, `REGISTRY_INGEST`,
`TRANSLATION_PLAN`, `QWEN_TRANSLATE`, `TRANSLATION_QA`,
`TRANSLATION_POLICY`, `TRANSLATION_APPLY`, `EXPORT_AUDIT`, `EXPORT_WRITE`,
and `REPORT`.

`WorkflowContext` creates the Madrid business date once. All stages share the
same run id, source snapshot and commit ids. State and audit artifacts are
written under `runtime/reports/workflow_v2/<business_date>/<workflow_run_id>`.

Spanish fact commit and translation are separate stages. A provider failure
can leave `FACT_COMMITTED` intact and make the run `DEGRADED` or `BLOCKED`.
Translation candidates are not approved by the provider; apply remains an
explicit stage.

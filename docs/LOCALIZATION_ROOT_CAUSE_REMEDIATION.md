# Localization root-cause remediation

This document describes the program-level safety layer.  It is separate from
workbook repair and does not modify any Excel/CSV source or translation data.

## Implemented locally

- `data/qa/localization_regressions_v1.jsonl` is the owner-confirmed,
  source-bound regression corpus.  `scripts/audit_localization_regressions.py`
  reports all same-source occurrences and translation variants.
- `src/action_tracker/localization/repair_service.py` is the single field-level
  Preview/Apply/Verify/Rollback contract.  It rechecks source, target and policy
  hashes in the write transaction and preserves details pair structure.
- `src/action_tracker/localization/shadow_audit.py` is wired into daily-run as a
  read-only audit.  It never calls a model, writes Chinese fields, or changes
  lifecycle/PRESENCE state.
- `src/action_tracker/localization/gold_gate.py` aggregates the six Gold layers
  and fails closed when policy coverage or provenance is partial.
- `OFFICIAL_LABELS.csv` is published as an atomic export sidecar.  Raw official
  label tokens remain traceable even when business remarks use a normalized
  display label.
- The offline candidate adapter uses `GUARD_PASS_PENDING_REVIEW`; Guard PASS
  cannot become semantic approval.

## Deliberately not claimed

- The current Gold policy remains `gold_claim_allowed=false`; existing partial
  semantic coverage must not be declared Gold.
- AI translation and automatic approval remain disabled in settings.
- Unknown detail values, source conflicts, and unresolved semantic findings are
  review work, not automatic corrections.
- No workbook, Master, known SKU state, offline state, or formal dictionary
  baseline is changed by the shadow path.

## Required operator flow

```text
source snapshot -> shadow audit -> owner repair manifest -> preview
-> hash gate -> explicit Apply -> verify -> export Gold gate
```

The repair CLI is `scripts/localization_repair.py`.  Apply requires both an
owner identity and an explicit `--commit`; the default is dry-run.

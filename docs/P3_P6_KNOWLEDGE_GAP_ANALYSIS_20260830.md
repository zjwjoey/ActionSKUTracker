# P3–P6 Knowledge Production V1 Gap Analysis — 2026-08-30

Audit baseline: branch `feat/knowledge-production-v1`, parent main `a3ce3de`. Existing implementation was inventoried before changes; correct P0–P2 code was not rewritten.

| Area | Current implementation | Tests | Runtime/gate | Status | Gap / action |
|---|---|---|---|---|---|
| P3 resolver | Field-level resolver, stable six-field source hash, fallback contract | `tests/test_knowledge_production.py` | Apply disabled | IMPLEMENTED | Added explicit approved-source handling and AI-candidate proposal input |
| P3 apply | Preview and field-level PRIMARY-only apply in `KnowledgeStore`; no Master writer | targeted Knowledge tests | `knowledge.production_apply_enabled=false` | IMPLEMENTED | Stale hash, approval, role and disabled gates covered |
| P3 provenance | Per-field source/freshness columns already in V2 schema | schema/store tests | Formal export reads localization only | IMPLEMENTED | Preserve six-field ownership and field-level updates |
| P4 scoped dictionary | Deterministic GLOBAL/FIELD/CAT1/CAT2/PRODUCT matching | new scoped tests | `scoped_dictionary.enabled=false` | IMPLEMENTED | Human-approved rules only; specificity and same-level conflict fail closed; blast-radius report |
| P5 queue | Incremental stable IDs, dedupe and source-change detection | queue tests | `translation.ai_enabled=false` | IMPLEMENTED | Added provider-neutral retry runner; candidate-only output |
| P5 validator | SKU/hash/URL/type/length/confidence checks | validator tests | No live provider configured | IMPLEMENTED | Added per-field numeric-fact preservation |
| P5 live provider | No credential/provider configured in repo/runtime | offline only | Disabled | NEEDS_VALIDATION | Live 20-SKU pilot is intentionally not claimed |
| P6 approval | Low-risk cat1/cat2/spec shadow policy; high-risk fields blocked | approval tests | `auto_approval_enabled=false` | IMPLEMENTED | Keep shadow-only and no dictionary promotion |
| P3–P6 export isolation | SQLite formal localization + fallback only; candidates are not joined | export regression | Production switches off | IMPLEMENTED | No second product truth introduced |

No HIGH or MEDIUM gaps remain in the offline/fixture scope. A real SQLite field-level preview covered all 5,396 CURRENT SKUs (32,324 fields, zero rejects). The legacy Excel-oriented `dictionary-apply` CLI is intentionally not the PRIMARY Knowledge writer and rejects the historical Master order; this is recorded as LOW compatibility follow-up, not a production data-path failure. The only other non-release validation item is an optional live AI provider pilot; it is not a blocker while the provider switch remains disabled.

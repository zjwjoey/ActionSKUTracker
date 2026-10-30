# Data Foundation V3 source retirement

These removals concern development source only. They do not authorize deleting
outputs, dictionaries, databases, snapshots, backups or review materials.
All removed source remains recoverable from parent commit
`e6b8801d494c160b971212a1e9089957a7794aed`.

| Removed source | Reason | Retained capability |
| --- | --- | --- |
| `scripts/_explore_listing.py` | Unreferenced one-card experiment with obsolete cookie path | Guarded monitor/listing, browser and access services |
| `scripts/backup_master.ps1` | Unreferenced Master-only copy, lacking database/projection consistency | Verified Backup and CORE capture/restore |
| `scripts/reconcile_baseline.py` | Unreferenced fixed historical CURRENT-count reconciliation | Configured database parity |
| `scripts/semantic_audit_translation_review.py` | Fixed ten-SKU recommendations, including obsolete branded names | Source-bound localization QA and review queue |
| `scripts/prune_stale_model_overrides.py` | Unreferenced direct dictionary rewrite | Existing source-version rejection in dictionary resolver |
| `scripts/apply_stage2_confirmed_translations.py` | Fixed old translation mutation without current field approval binding | Approval, Immutable Patch and Apply |

The two unused provider classes in `translation/service.py` were also removed.
The active provider protocol remains in `localization/providers/base.py`; the
formal adapter and translation-state logic remain available.

The legacy `apply_zh` helper copied Spanish into Chinese fields. Its only code
consumer was the old fixture suite. Those tests now exercise the existing
`apply_zh_formal`: valid Chinese/status are preserved, missing Chinese remains
empty/PENDING, display fallback is explicit and input facts are unchanged.
The obsolete Spanish-as-Chinese expectation is retired, not treated as valid
behavior to preserve.

Dependency review covered Python imports, CLI/scripts, configuration, dynamic
loads, tests, deployment records and Windows task references. A historical prompt
directory listing mentioning `backup_master.ps1` is archival text, not a caller.
No currently used Workflow V2, baseline initializer, translation provider,
approved dictionary or export entry point was removed.

Catalog, Template 1 and Workflow V2 already consume `build_final_zh_projection`.
This retirement does not introduce a second projection or alter the 14-column
contract. Final integration and Windows/Ubuntu CI remain separate acceptance
gates; this document alone is not evidence that those gates passed.

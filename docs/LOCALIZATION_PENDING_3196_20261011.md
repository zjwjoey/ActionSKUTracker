# 3,196 SKU localization continuation

The execution scope is frozen from PRIMARY at
`2026-10-10_desc_details_repair_20261010T180308518582Z_c0f6b8cee2de`.
Its 3,196 unique SKUs contain 8,756 outstanding fields: 4,633 blank targets
and 4,123 nonblank targets requiring approval, freshness or source binding
review. These are completion/review counts, not approved Apply counts.

Runtime evidence lives at
`F:/ActionSKUTracker/runtime/reports/localization_pending_scope_20261011/execution/`.
`scope.jsonl` records each field's current value, Spanish source, full source
context, provenance, issue reasons and historical source evidence. Its SHA-256
is bound by `baseline.json`. Unknown source evidence is kept for source audit;
it is never treated as a verified official empty field.

Work uses the existing historical Translation Registry / QA / owner delegation
/ immutable patch / Apply path. It preserves the historical-only boundary of
`HISTORICAL_OWNER_DELEGATION_V1`; CURRENT fields cannot be passed to that grant.
Review, translation and approved-but-unapplied counts remain separate. Candidate
creation never counts as a production Apply. Metadata rebindings are reported
separately from text corrections and missing-field backfills.

## Compact measurement regression

Real specifications for SKUs 3215347, 3224104, 2561124 and 2581866 preserve
their counts, measurements and units in `8×70ml`, `12×50ml`, `30×17cm` and
`38×38cm`. The unit scanner nevertheless rejected them because its broad
Latin-1 identifier boundary included the multiplication sign U+00D7.

The boundary now excludes multiplication/division separators while retaining
the Latin identifier and digit guards. It does not change source facts,
generation rules, dictionaries, protected models or approval policy. The real
source/target pairs and source artifact hashes are in the regression fixture;
tests also reject dropped/changed units and numbers embedded in identifiers.
An independent source comparison supplies KEEP decisions; Guard PASS alone
never supplies semantic approval.

The 30-field specification pilot retains four quantity/sheet-page source
conflicts. Decibel token-spacing/localization and PPP/DPI equivalence remain
separate QA review issues; this fix does not waive them.

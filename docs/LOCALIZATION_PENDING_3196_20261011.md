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

## Cubic flow regression

The source-bound specification for pool pump 3004507 is
`2,3 m3/hora | 12 voltios`. Its retained candidate
`2.3 立方米/小时 | 12 伏特` is independently faithful, but the old Guard
counted the ASCII exponent as another quantity and classified cubic metres
as ordinary metres. This is a reproducible QA false rejection, not a reason
to waive source or approval requirements.

The Guard now recognizes cubic units and equivalent Chinese unit names,
removes standalone measurement exponents from numeric quantity comparison,
and preserves the hourly cubic-flow denominator. Model suffix digits remain
literal. New cubic measurements require their own source field; a quantity
in the description cannot authorize adding flow to the specification.
The fixture binds the real source, retained candidate and source artifact
hash. Regression negatives cover area/length substitutions, dropped/changed
rate denominators, numeric changes and cross-field fact imports.

This change does not generate translations, publish dictionaries, change
the source hash contract, deploy the daily runner or approve high-risk name
corrections. Only the existing native Registry / QA / Approval / immutable
Apply flow can publish reviewed targets.

Validation: the real pool-pump case and two numeric/own-field assertions fail
before the change. After the change, the 18 cubic/compact-unit regressions
pass and the full local suite passes 1,325 tests. The source-bound pool-pump
candidate passes preflight QA after the repair; preflight is not an approval
or an actual PRIMARY Apply.

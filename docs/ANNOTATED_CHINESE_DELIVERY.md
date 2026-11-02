# Source-notes Chinese catalog delivery

`ANNOTATED_RELEASE` is an explicit, authorized catalog delivery mode. Default
PREVIEW and strict PRODUCTION_RELEASE retain their existing behavior. It is
not research-verified release approval and does not mutate source facts,
localizations, freshness, approvals, Master, State or dictionaries.

Run the existing catalog exporter with a reviewed plan:

```powershell
python -m action_tracker export --lang zh --date 2026-10-26 --no-images --annotation-plan <approved-plan.json>
```

The plan contract is `ANNOTATED_DELIVERY_V1`. It binds the CURRENT commit,
entire selected source hash, date, exact SKU/field, SKU source hash, field
source hash, current target hash, action and the exact observed repair codes.
Owner proxy identity, authorization text/reference and timezone-aware approval
and expiry are mandatory. No generic rule/category exception is accepted.

The existing 14 columns and single-sheet template are preserved. The derived
profile has its own identity/version and a filename containing 来源待核备注版,
under `runtime/exports/annotated/`. It accepts only the entire SQLite CURRENT
set; ES, selections and historical snapshots cannot use this mode. Existing
local image rules still apply; the no-image profile keeps image URLs.

Supported field actions:

- RETAIN_SOURCE_NOTE: keep an already approved, complete literal source anomaly
  note matching the same details source; only its declared invalid-source
  physical-state finding may be retained as an explicit disclosed issue.
- QUARANTINE: display 待复核：来源字段存在异常，详见备注. Original Spanish,
  original Chinese, source/provenance and the original strict failure remain
  in the manifest. This is required for STALE and contested assertions.
- RETAIN_WITH_CONFLICT_NOTE: keep source-faithful approved content that has no
  other blocking repair finding, and disclose the source conflict.
- RETAIN_PENDING_BLANK: preserve an empty PENDING/REVIEW_REQUIRED field only
  when a current, exact-field tracking entry exists; disclose the missing
  Chinese value and its source. It never approves or writes a translation.

An operator can request a plan from the configured persistent conflict tracker
for the current run, supplying the explicit authorization reference and text:

```powershell
python -m action_tracker export --lang zh --date 2026-10-26 --no-images --tracked-conflicts --tracking-authorization-ref 'user:request' --tracking-authorization-text '保留来源异常和备注'
```

This explicit mode reads the current tracker without modifying configuration.
It uses existing exact-match confirmed brand exceptions for this run. Tracking
entries with changed source/Chinese hashes, expired registrations, or other
unhandled QA findings fail closed. An unmatched price, Master quality or other
independent blocking finding remains a blocker. The generated plan is tied to
the current commit and expires after one hour; it does not approve an Apply.

Every reviewed item receives a visible remark. All undeclared cells remain
unchanged. New or changed findings, stale retained content, invalid approvals,
missing source evidence or changed hashes fail closed. Strict results are
recorded unchanged as `strict_source_audit`; annotated readiness is separate
from `research_verified=false`.

Publication uses the existing atomic workbook/manifest/repair-report bundle.
The additional full-14-column digest covers remarks and row order, and is
verified from the written XLSX. Exact row count, duplicate/order checks and
source price/URL parity are mandatory. An intact bundle may be reused only
with the identical plan/source/head, audit and workbook content; unknown or
changed existing files are never overwritten.

No scheduled annotated publication or automatic translation Apply is enabled.

For this internal research mode only, `--annotate-original-prices` may be added
to `--tracked-conflicts`. The current committed Master quality report must
contain only `INVALID_ORIGINAL_PRICE` blocking issues. Every affected SKU must
have finite positive current/original prices, an original price no higher than
the current price, and a blank exported original price. Its exact source values
are preserved in the manifest and disclosed in a visible remark. No source
prices or QA outcomes are changed; `research_verified` remains false. Missing
original prices do not create notices, and promotions never invent an original
price. Any other Master issue or unmatched price still blocks. Strict release
and the default annotated mode are unchanged.

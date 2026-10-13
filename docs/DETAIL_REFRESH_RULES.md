# Detail freshness (local implementation, 2026-10-11)

Daily detail planning reads `product_detail_state`, never `last_seen`. A
successful committed detail fetch records its actual UTC capture time, run and
six-field official source hash inside the fact transaction. Carried baseline
fields do not advance this timestamp; failed planned fetches remain pending.
Dry-run does not persist detail freshness.

Missing description, specifications, second category or details (including a
table containing only the article number) requests a check. A successful
official check that confirms sparse/empty source waits until the configured
refresh interval, preventing an endless daily retry. A pending fetch or a
changed source hash invalidates this grace period. Lightweight price, badge or
image changes do not suppress detail work; sitemap-only products can use their
baseline product URL.

Legacy complete products without registered successful capture time have
**unknown freshness**, not a proven expiry date. They do not trigger a daily
full crawl. Daily probes at most 25 complete legacy products per run, in stable
SKU order, to register real capture evidence gradually. The optional
`run.detail_freshness_probe_limit` can override this budget (0 disables probes).
Missing/pending and proven expired items are separate from this bootstrap
budget. Successful probes leave the unknown queue; failed probes become pending.
Timestamp-free legacy checkpoints are rechecked rather
than presented as a fresh collection.

Explicit finite browser verification may pass capture timestamps to the native
detail-correction transaction. Only SKUs whose description and details were
both verified can receive successful detail state; category-only verification
does not advance description/details freshness. Source-empty fields remain
empty. This enrichment never creates a presence observation or changes price
or lifecycle state.

## Listing comparison and canonical field ownership

Incremental card changes compare Listing fields to a previous committed
Listing observation, including category membership sets, not to the Detail
projection. Exact name/specification/category/URL/image changes and explicit
empty card fields still request Detail confirmation. Category entry order
alone is not a source change.

`LISTING_FIELD_EVIDENCE_V1` binds finite card rows, run/date and their hash in
existing `run_evidence` JSON. Readers require committed QA-eligible complete
Listing observations for each exact SKU, schema/hash parity and the frozen
PRIMARY head. Legacy snapshots additionally require run/date and CSV/raw-card
parity; their existing full-directory hash is checked without redefining its
scope. A post-commit report rewrite can make that digest differ. Such evidence
is explicitly `LEGACY_MATCHED`, not immutable proof, and receives bounded
`LISTING_BASELINE_UNKNOWN` probes. Newer invalid observations remain unknown;
an older card cannot silently replace them.

Unknown comparison and legacy freshness probes share the configured 25-item
budget. Unconfirmed canonical differences are retained in the next bound
Listing envelope, so copying today's card into the comparison baseline cannot
erase outstanding confirmation. Successful Detail confirmation clears that
pending binding; failed planned captures keep `PENDING` and are retried.
The first legacy bootstrap does not claim all real changes have been detected.

Nonempty freshly captured Detail name/category/specification/URL/image and
body facts own their canonical values and provenance. Listing can supplement
empty fields; disagreement cannot overwrite nonempty canonical history when
Detail is unavailable. Selling-price precedence follows the complete price
observation contract. Unit price remains its own field fact.
When no fresh Detail unit-price owner exists, a nonempty current Listing unit
price refreshes that same official field; blank observations retain history.
A unit-price-only change is a lightweight metadata plan and does not infer a
selling price or trigger an identity Detail fetch.

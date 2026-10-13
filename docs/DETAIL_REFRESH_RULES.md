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

# Reviewed historical fact import

Local Data Foundation V3 candidate; not a production deployment or Apply grant.

`localization.history_recovery.build_historical_import_bundle` extends the
existing historical recovery module. It previews a `HISTORICAL_FACTS_ONLY`
`CommitBundle`; the sole `ProductionWriter` reconstructs the exact scope inside
its normal transaction. No schema, second fact writer or online collection is
introduced. The original missing-field recovery APIs remain available.

Each candidate contains `sku`, explicit `canonical_id`, `record_hash`, and a
reviewed `source`. Source configuration contains absolute `path`, SHA-256
`asset_sha256`, `kind` (`SNAPSHOT_CSV` or `VERIFIED_XLSX`), `sheet` where needed,
`sku_header`, exact canonical-to-source-column `fields`, `language=es`,
`evidence_level=A`, original `source_version`, `reviewed_by`, zoned
`reviewed_at`, and `review_evidence`. Source configuration is reviewed evidence,
not a request to infer trust from a filename or a self-declared language alone.

The record hash uses the ACK module's typed JSON digest of the actual parsed
source record. Asset bytes are hashed before and after parsing, and again at
commit/Resume. Duplicate source SKUs or ambiguous CSV/XLSX headers fail closed.
Only the six ES descriptive fields are accepted. Price columns can be present
in the original record but cannot be included in the import field map.

Original `observed_at` is optional. A known date/time requires explicit
`observed_at_evidence`; future dates and naive timestamps fail closed. Directory
names and import/review timestamps do not supply an original observation date.
Unknown stays unknown in field lineage. Import time remains the commit time.

Reviewers reuse the existing immutable-patch actor validator. Human identities
are explicit `human:<reviewer>`. Exact authorized service actors may be supplied
through external builder `authorized_service_actors` and writer
`authorized_fact_review_service_actors` context, both defaulting to empty.
Payloads cannot authorize themselves; wildcard service configuration is rejected
by the writer. This context does not authorize Chinese approval or Apply.

Existing identities retain their status, all Presence dates, missing counts,
prices, badges, URLs and product fields. Missing ES fields can be filled from a
unique verified value. Existing nonempty facts are protected, including an
official product-level title when the ES localization title is blank. Unrelated
old text is retained exactly instead of being incidentally normalized.

Two different historical values remain two canonical source versions plus
immutable field evidence. The projection does not choose a winner. Such fields
are listed in the durable blocked/review record; other unique fields can still
be saved. Exact previously imported source versions are referenced again,
without appending duplicate fact versions merely because another task resumes.

A new verified identity is `HISTORICAL_ONLY`, with no inferred current price,
Presence date, website URL, absence event or lifecycle row. An optional URL is
accepted only when the actual source supplies a matching HTTPS Action ES SKU
URL. No new current observation, price history or lifecycle event is created.

Registry recovery reads the committed exact filled-field receipt, rechecks
current field hashes, and queues only those fields. It does not turn missing
source into an official empty value. Aggregate Registry observation is UNKNOWN;
the authoritative original field times remain in historical evidence/lineage.
Existing Chinese text and reviewer metadata are retained; changed field source
bindings can become STALE through the existing localization writer.

Repository CURRENT excludes historical-only identities. Formal Catalog uses an
existing complete Presence authority and latest fact head, with unchanged head
race checks; historical import cannot create that Presence authority. Without a
prior complete observation it supplies no formal CURRENT source. V2 complete
daily reuse does not promote a history-only latest head into a daily source.
Research Selection includes HISTORICAL_ONLY/UNKNOWN through existing extraction;
its research CSV contract remains separate from formal 14-column Catalog.

Identical run/bundle Resume returns its original commit without new facts,
events or source versions and rechecks its source assets. Changed bundle/run,
stale base head, changed asset/record or out-of-scope mutation fails closed.
Projection and Registry retries use the existing recovery mechanisms. This
module does not enable scheduling, production changes or historical deletion.

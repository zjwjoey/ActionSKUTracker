# Extraction Contract V1

`ExtractionQuery` supports canonical/SKU identity, keyword, lifecycle status,
categories, current/original price and promotion, price-change direction and
amount/percentage, historical low/high filters, explicit first-seen,
last-seen and event-time ranges, event-relative windows, image readiness and
localization quality, deterministic sorting and `limit/offset` pagination.

`statuses` defaults to `CURRENT` for every entry point. The legacy
`date_from/date_to/last_n_days` aliases retain last-seen semantics; new callers
should use `first_seen_from/to`, `last_seen_from/to` and
`event_from/to/event_last_n_days`. Event type and event time predicates apply
to the same `event_history` row.

Chinese completeness is evaluated across all six fields (`name_zh`, `cat1_zh`,
`cat2_zh`, `spec_zh`, `desc_zh`, `details_zh`). `missing_fields` accepts each
of those field names and their short aliases.

`ExtractionResult` contains the canonical query, SHA-256 `query_hash`, total
match count, items, sort/pagination metadata, source commit and UTC timestamp.
Canonical JSON makes identical queries hash identically. SQL is built only in
`ExtractionService`; CLI, Workspace and Selection call this service.

Example:

```powershell
python -m action_tracker extract --status CURRENT --max-price 2 --limit 50 --json
```

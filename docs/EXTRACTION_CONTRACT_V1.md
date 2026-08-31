# Extraction Contract V1

`ExtractionQuery` supports identity, keyword, lifecycle status, categories,
price/original-price/promotion, price change, event/date filters, image and
localization quality, deterministic sorting and `limit/offset` pagination.

`ExtractionResult` contains the canonical query, SHA-256 `query_hash`, total
match count, items, sort/pagination metadata, source commit and UTC timestamp.
Canonical JSON makes identical queries hash identically. SQL is built only in
`ExtractionService`; CLI, Workspace and Selection call this service.

Example:

```powershell
python -m action_tracker extract --status CURRENT --max-price 2 --limit 50 --json
```

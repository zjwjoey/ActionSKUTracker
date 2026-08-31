# Data Workspace V1

The localhost Operations server exposes the workspace read surface:
`/api/products` (Extraction Contract), `/api/views`, `/api/selections`,
`/api/artifacts`, `/api/runs`, `/api/quality` and `/api/health`. Individual
saved views and selections can be read by id. The `/workspace` page supports
keyword, status, price-range, image and promotion filters; it remains a read
surface over SQLite and never mutates product facts.

CLI management is available through `extract`, `saved-view create/list/update/delete` and
`selection create/list/get`. Saved views also have explicit service/API update
and delete operations; selection membership remains immutable after creation.
Selection exports use `export --selection-id` and record artifact provenance in
SQLite. The server remains localhost-only; no public bind, authentication
bypass or direct product mutation was added.

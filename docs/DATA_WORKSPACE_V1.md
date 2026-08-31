# Data Workspace V1

The localhost Operations server now exposes the first usable workspace read
surface: `/api/products` (Extraction Contract), `/api/views`,
`/api/selections`, `/api/artifacts`, `/api/runs`, `/api/quality` and
`/api/health`. The home page links to product queries and saved data sets.

CLI management is available through `extract`, `saved-view create/list` and
`selection create/list/get`. Selection exports use `export --selection-id` and
record artifact provenance in SQLite. The server remains localhost-only; no
public bind, authentication bypass or direct product mutation was added.

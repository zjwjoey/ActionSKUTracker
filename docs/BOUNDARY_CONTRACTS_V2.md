# Boundary Contracts V2

| Owner | Owns | Must not own |
|---|---|---|
| Core Data | official Spanish facts, lifecycle, prices, events | Chinese translation or UI filters |
| Localization/Enrichment | `product_localizations`, dictionary and review candidates | official Spanish facts |
| Image Pipeline | image metadata in SQLite and binaries on disk | lifecycle decisions |
| ExtractionService | query normalization, filters, sorting and pagination | product mutations |
| Selection domain | saved query or SKU membership | copied prices/names/categories |
| Delivery/Artifact | files, manifests and artifact metadata | re-running business selection |
| Operations | jobs, locks, backups and reports | a second product truth source |

SQLite PRIMARY remains the sole formal product fact source. Excel/CSV are
compatibility projections or delivery artifacts.

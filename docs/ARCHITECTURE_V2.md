# ActionSKUTracker Architecture V2

ActionSKUTracker is a local Action Spain data platform: acquire facts, keep a
SQLite history, enrich localization/images, extract SKU sets, and deliver
artifacts on demand.

1. **Acquisition** — `monitor/`, `orchestrator/daily.py`, snapshots, access control and collection QA. It never decides Chinese text or selections.
2. **Core Data** — SQLite PRIMARY in `database/`; products, lifecycle, observations, prices, events and commit evidence are the only facts.
3. **Enrichment** — `knowledge/`, `dictionary_*`, `images/`; localization and image metadata are derived and auditable.
4. **Extraction** — `extraction/contracts.py` and `extraction/service.py`; one deterministic read contract answers which SKUs match.
5. **Delivery** — existing `exporting/` plus `delivery/artifacts.py`; a selection is rendered without copying product facts.
6. **Operations** — `operations/`; lock, backup, resume, preflight, health, scheduler and reports.

`Saved View` stores a dynamic query. `Selection Set` stores only fixed SKU
membership. Both display current facts from SQLite when read.

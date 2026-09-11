"""Apply fully audited official breadcrumb evidence for the current cat2 backlog."""
from pathlib import Path
import csv
import sqlite3

from action_tracker.config import load_settings
from action_tracker.database.integration import database_path, regenerate_compatibility_exports
from action_tracker.database.production import apply_verified_listing_reconciliation
from action_tracker.database.repository import ProductionRepository
from action_tracker.products.badges import parse_badges

ROOT = Path(__file__).resolve().parents[1]
CFG = load_settings()
csv_path = ROOT / "runtime" / "temp" / "cat2_edge_evidence_20260907.csv"
rows = list(csv.DictReader(csv_path.open(encoding="utf-8-sig")))
db_path = database_path(CFG)
repo = ProductionRepository(db_path)
current = {str(r["sku"]): r for r in repo.load_current_export_records()}
lifecycle = repo.load_known_skus()
conn = sqlite3.connect(db_path)
expected = {str(r[0]) for r in conn.execute("""select p.official_sku from products p
    left join product_localizations es on es.official_sku=p.official_sku and es.language='es'
    where p.status='CURRENT' and trim(coalesce(es.cat2,''))=''""")}
conn.close()
got = [str(r["sku"]) for r in rows]
if len(rows) != 282 or len(set(got)) != 282 or set(got) != expected:
    raise SystemExit({"expected": len(expected), "got": len(rows), "unique": len(set(got)), "missing": sorted(expected-set(got)), "extra": sorted(set(got)-expected)})
plan = []
for row in rows:
    sku = str(row["sku"])
    cur = current.get(sku) or {}
    life = lifecycle.get(sku) or {}
    first_seen = str(life.get("first_seen_date") or "").strip()[:10]
    if not first_seen:
        raise SystemExit(f"missing lifecycle first_seen: {sku}")
    plan.append({
        "sku": sku, "product_url": str(cur.get("product_url") or row["source_url"]),
        "cat1_es": str(row["cat1_es"]).strip(), "cat2_es": str(row["cat2_es"]).strip(),
        "action_new_badge": bool(parse_badges(cur.get("raw_tags")).action_new_badge),
        "first_seen": first_seen,
    })
import_id = "cat2-edge-backfill-20260907-282"
result = apply_verified_listing_reconciliation(
    db_path, plan, import_id=import_id,
    evidence={"source": "EDGE_PLUGIN", "authority": "official_product_breadcrumb", "evidence_file": str(csv_path)},
)
head = repo.current_head()
sync = regenerate_compatibility_exports(CFG, head) if head else None
print({"status": "APPLIED", "import_id": import_id, **result, "master_sync": sync})

from pathlib import Path
import csv
import sqlite3

root = Path(__file__).resolve().parents[1]
rows = list(csv.DictReader((root / "runtime" / "temp" / "cat2_edge_evidence_20260907.csv").open(encoding="utf-8-sig")))
db = sqlite3.connect(root / "runtime" / "db" / "action_tracker.db")
expected = {str(r[0]) for r in db.execute("""select p.official_sku from products p
    left join product_localizations es on es.official_sku=p.official_sku and es.language='es'
    where p.status='CURRENT' and trim(coalesce(es.cat2,''))=''""")}
db.close()
got = [str(r["sku"]) for r in rows]
print({"expected": len(expected), "got": len(got), "unique": len(set(got)),
       "missing": sorted(expected - set(got)), "extra": sorted(set(got) - expected),
       "blank_cat2": sum(not str(r["cat2_es"]).strip() for r in rows),
       "blank_cat1": sum(not str(r["cat1_es"]).strip() for r in rows)})

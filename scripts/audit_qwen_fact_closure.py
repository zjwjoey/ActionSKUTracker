"""Audit the immutable Spanish fact pool used by the Qwen data factory."""
from __future__ import annotations
import csv, hashlib, json, re, sqlite3
from collections import Counter
from pathlib import Path

CJK=re.compile(r"[\u3400-\u9fff]")
FIELDS=("name_es_raw","cat1_es","cat2_es","spec_es_raw")

def main():
    root=Path(__file__).resolve().parents[1]; rows=list(csv.DictReader((root/"data/dictionary/product_dictionary.csv").open(encoding="utf-8-sig")))
    blocked={r["sku"] for r in csv.DictReader((root/"data/dictionary/source_damage_report.csv").open(encoding="utf-8-sig")) if r.get("status") in {"SOURCE_DAMAGED","SOURCE_POLLUTED"}}
    dup=len(rows)-len({r.get("sku") for r in rows}); reliable=[r for r in rows if r.get("sku") not in blocked]; complete=[r for r in reliable if all(str(r.get(f) or "").strip() for f in ("name_es_raw","spec_es_raw"))]
    c=Counter();
    for f in FIELDS: c[f]=sum(bool(str(r.get(f) or "").strip()) for r in reliable)
    cjk=[r["sku"] for r in reliable if any(CJK.search(str(r.get(f) or "")) for f in FIELDS)]
    con=sqlite3.connect(root/"runtime/db/action_tracker.db"); current={r[0] for r in con.execute("select official_sku from products where status='CURRENT'")}; con.close()
    out={"total_product_rows":len(rows),"unique_skus":len({r.get('sku') for r in rows}),"duplicate_skus":dup,"source_blocked":len(blocked),"reliable_rows":len(reliable),"reliable_name_spec_complete":len(complete),"current_skus":len(current),"current_reliable_name_spec_complete":sum(r.get('sku') in current for r in complete),"field_source_counts":dict(c),"cjk_in_spanish_source":len(cjk),"status":"PASS" if dup==0 and not cjk else "FAIL"}
    path=root/"runtime/training/qwen3_8b/20260908/fact_closure_audit.json"; path.write_text(json.dumps(out,ensure_ascii=False,indent=2)+"\n",encoding="utf-8"); print(json.dumps(out,ensure_ascii=False))
if __name__=="__main__": main()

"""Prepare unresolved current Chinese localization rows for GPT-5.6-Luna agents.

This is an offline staging helper only. It never writes SQLite, Master, or an
export workbook. The first 1,616 candidates are the completed cloud-model
prefix; later cache entries were produced by the local model and are ignored.
"""
from __future__ import annotations
import json, re, sys
from pathlib import Path
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
PROJECT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(PROJECT/"src"))
from action_tracker.config import load_settings
from action_tracker.database.integration import database_path
from action_tracker.database.repository import ProductionRepository
from action_tracker.exporting.dictionary_join import build_zh_rows, load_dictionary_context

CJK=re.compile(r"[\u3400-\u9fff]")
SPANISH=re.compile(r"\b(?:el|la|los|las|de|del|con|para|sin|una|un|unos|unas|varios|varias|diferentes|talla|tallas|unidades?|piezas?|gramos?|metros?|litros?|paquete|juego|calcetines|mallas|pantis|guantes|gorro|chocolate|cable|bolsa|discos|gel|colores?|hogar|oficina|papelería|mascotas|juguetes|manualidades|limpieza|deporte)\b", re.I)
FIELDS=("name","cat1","cat2","spec","description","details")
ROW={"name":"标题","cat1":"分类1","cat2":"分类2","spec":"规格","description":"描述","details":"产品详情"}
ES={"name":"name_es","cat1":"cat1_es","cat2":"cat2_es","spec":"spec_es","description":"desc_es","details":"details_es"}
def t(v): return "" if v is None else str(v).strip()
def need(field,current,source):
    if not source: return False
    if not current or current==source: return True
    if field in {"cat1","cat2","name","spec"} and not CJK.search(current): return True
    if field in {"description","details"} and (not CJK.search(current) or SPANISH.search(current)): return True
    return bool(SPANISH.search(current))
def main():
    cfg=load_settings(); records=ProductionRepository(database_path(cfg)).load_current_export_records(); base,_=build_zh_rows(records,load_dictionary_context(cfg))
    by={t(r["编号"]):r for r in base}; rec={t(r["sku"]):r for r in records}
    candidates=[]
    for sku in sorted(rec,key=lambda x:(int(x) if x.isdigit() else 10**18,x)):
        r=rec[sku]; b=by[sku]; needed=[f for f in FIELDS if need(f,t(b[ROW[f]]),t(r[ES[f]]))]
        if needed:
            candidates.append({"sku":sku,"needed":needed,"source":{f:t(r[ES[f]]) for f in FIELDS},"current":{f:t(b[ROW[f]]) for f in FIELDS}})
    unresolved=candidates[1616:]
    out=Path(cfg["paths"]["temp"])/"gpt56_luna_inputs"; out.mkdir(parents=True,exist_ok=True)
    n=3; size=(len(unresolved)+n-1)//n
    for i in range(n):
        part=unresolved[i*size:(i+1)*size]
        (out/f"batch_{i+1}.json").write_text(json.dumps(part,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"all_candidates":len(candidates),"trusted_cloud_prefix":1616,"unresolved_for_gpt56_luna":len(unresolved),"batch_sizes":[len(unresolved[i*size:(i+1)*size]) for i in range(n)],"directory":str(out)},ensure_ascii=False))
if __name__=="__main__": main()

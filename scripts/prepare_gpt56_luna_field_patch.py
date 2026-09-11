"""Stage fields omitted from earlier GPT-5.6-Luna batches.

The first pass intentionally omitted name/spec for the former cloud-prefix
rows.  This helper finds only those still-untranslated fields so no legacy
provider cache is needed.
"""
from __future__ import annotations
import json, re, sys
from pathlib import Path
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
P=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(P/"src"))
from action_tracker.config import load_settings
from action_tracker.database.integration import database_path
from action_tracker.database.repository import ProductionRepository
from action_tracker.exporting.dictionary_join import build_zh_rows, load_dictionary_context
CJK=re.compile(r"[\u3400-\u9fff]")
SP=re.compile(r"\b(?:el|la|los|las|de|del|con|para|sin|una|un|unos|unas|varios|varias|diferentes|talla|tallas|unidades?|piezas?|gramos?|metros?|litros?|paquete|juego|calcetines|mallas|pantis|guantes|gorro|chocolate|cable|bolsa|discos|gel|colores?|hogar|oficina|papelería|mascotas|juguetes|manualidades|limpieza|deporte|número|tipo|color|material|cantidad|incluye|sí|no)\b",re.I)
F=("name","cat1","cat2","spec","description","details")
ROW={"name":"标题","cat1":"分类1","cat2":"分类2","spec":"规格","description":"描述","details":"产品详情"}
ES={"name":"name_es","cat1":"cat1_es","cat2":"cat2_es","spec":"spec_es","description":"desc_es","details":"details_es"}
def t(v): return "" if v is None else str(v).strip()
def sort_key(x): return (int(x) if x.isdigit() else 10**18,x)
def need(f,c,s):
    return bool(s) and (not c or c==s or (f in {"cat1","cat2","name","spec"} and not CJK.search(c)) or (f in {"description","details"} and (not CJK.search(c) or SP.search(c))) or bool(SP.search(c)))
def main():
    cfg=load_settings(); rec=ProductionRepository(database_path(cfg)).load_current_export_records(); base,_=build_zh_rows(rec,load_dictionary_context(cfg)); by={t(x["编号"]):x for x in base}; rr={t(x["sku"]):x for x in rec}
    outdir=Path(cfg["paths"]["temp"])/"gpt56_luna_outputs"; outputs={}
    for n in range(1,10):
        p=outdir/f"batch_{n}.json"
        if not p.exists(): continue
        d=json.loads(p.read_text(encoding="utf-8")); d=d.get("items",d) if isinstance(d,dict) else d
        for x in d: outputs[t(x["sku"])]=x
    rows=[]
    for sku in sorted(rr,key=sort_key):
        r=rr[sku]; b=by[sku]; needed=[f for f in F if need(f,t(b[ROW[f]]),t(r[ES[f]]))]
        if not needed or sku not in outputs: continue
        missing=[]
        for f in needed:
            value=t(outputs[sku].get(f)); current=t(b[ROW[f]])
            if not value or value==current or (f in {"name","cat1","cat2","spec"} and not CJK.search(value)) or (f in {"description","details"} and (not CJK.search(value) or SP.search(value))): missing.append(f)
        if missing:
            rows.append({"sku":sku,"needed":missing,"source":{f:t(r[ES[f]]) for f in F},"current":{f:t(b[ROW[f]]) for f in F},"existing":{f:t(outputs[sku].get(f)) for f in F}})
    dest=Path(cfg["paths"]["temp"])/"gpt56_luna_field_patch_inputs"; dest.mkdir(parents=True,exist_ok=True); size=(len(rows)+2)//3
    for i in range(3): (dest/f"batch_{i+10}.json").write_text(json.dumps(rows[i*size:(i+1)*size],ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"patch_rows":len(rows),"sizes":[len(rows[i*size:(i+1)*size]) for i in range(3)],"directory":str(dest)},ensure_ascii=False))
if __name__=="__main__": main()

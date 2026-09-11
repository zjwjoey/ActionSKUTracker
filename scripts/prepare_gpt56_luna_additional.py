"""Prepare fields omitted from the initial cloud prefix for GPT-5.6-Luna."""
from __future__ import annotations
import json,re,sys
from pathlib import Path
if hasattr(sys.stdout,'reconfigure'): sys.stdout.reconfigure(encoding='utf-8')
P=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(P/'src'))
from action_tracker.config import load_settings
from action_tracker.database.integration import database_path
from action_tracker.database.repository import ProductionRepository
from action_tracker.exporting.dictionary_join import build_zh_rows,load_dictionary_context
CJK=re.compile(r'[\u3400-\u9fff]')
SP=re.compile(r'\b(?:el|la|los|las|de|del|con|para|sin|una|un|unos|unas|varios|varias|diferentes|talla|tallas|unidades?|piezas?|gramos?|metros?|litros?|paquete|juego|calcetines|mallas|pantis|guantes|gorro|chocolate|cable|bolsa|discos|gel|colores?|hogar|oficina|papelería|mascotas|juguetes|manualidades|limpieza|deporte)\b',re.I)
F=('name','cat1','cat2','spec','description','details')
ROW={'name':'标题','cat1':'分类1','cat2':'分类2','spec':'规格','description':'描述','details':'产品详情'}
ES={'name':'name_es','cat1':'cat1_es','cat2':'cat2_es','spec':'spec_es','description':'desc_es','details':'details_es'}
def t(v): return '' if v is None else str(v).strip()
def key(s): return (int(s) if s.isdigit() else 10**18,s)
def need(f,c,s):
    return bool(s) and (not c or c==s or (f in {'cat1','cat2','name','spec'} and not CJK.search(c)) or (f in {'description','details'} and (not CJK.search(c) or SP.search(c))) or bool(SP.search(c)))
def main():
    cfg=load_settings(); rec=ProductionRepository(database_path(cfg)).load_current_export_records(); base,_=build_zh_rows(rec,load_dictionary_context(cfg)); b={t(x['编号']):x for x in base}; ordered=sorted(rec,key=lambda x:key(t(x['sku']))); rows=[]
    for idx,r in enumerate(ordered):
        sku=t(r['sku']); n=[f for f in F if need(f,t(b[sku][ROW[f]]),t(r[ES[f]]))]
        if idx<1616:
            n=[f for f in n if f not in {'name','spec'}]
            if n: rows.append({'sku':sku,'needed':n,'source':{f:t(r[ES[f]]) for f in F},'current':{f:t(b[sku][ROW[f]]) for f in F}})
    out=Path(cfg['paths']['temp'])/'gpt56_luna_additional_inputs'; out.mkdir(parents=True,exist_ok=True); size=(len(rows)+2)//3
    for i in range(3): (out/f'batch_{i+4}.json').write_text(json.dumps(rows[i*size:(i+1)*size],ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'count':len(rows),'sizes':[len(rows[i*size:(i+1)*size]) for i in range(3)],'directory':str(out)},ensure_ascii=False))
if __name__=='__main__': main()

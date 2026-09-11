"""Merge GPT-5.6-Luna translations into a review export.

This script deliberately ignores all legacy provider caches.  The review export
must be reproducible from the explicit GPT-5.6-Luna batch artifacts only.
"""
from __future__ import annotations
import datetime as dt, hashlib, json, re, sys
from pathlib import Path
if hasattr(sys.stdout,"reconfigure"): sys.stdout.reconfigure(encoding="utf-8")
PROJECT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(PROJECT/"src"))
from action_tracker.config import load_settings
from action_tracker.database.integration import database_path
from action_tracker.database.repository import ProductionRepository
from action_tracker.exporting.dictionary_join import build_zh_rows, load_dictionary_context
from action_tracker.exporting.excel_writer import write_catalog_xlsx

FIELDS=("name","cat1","cat2","spec","description","details")
ROW={"name":"标题","cat1":"分类1","cat2":"分类2","spec":"规格","description":"描述","details":"产品详情"}
ES={"name":"name_es","cat1":"cat1_es","cat2":"cat2_es","spec":"spec_es","description":"desc_es","details":"details_es"}
SPANISH=re.compile(r"\b(?:el|la|los|las|de|del|con|para|sin|una|un|unos|unas|varios|varias|diferentes|talla|tallas|unidades?|piezas?|gramos?|metros?|litros?|paquete|juego|calcetines|mallas|pantis|guantes|gorro|chocolate|cable|bolsa|discos|gel|colores?|hogar|oficina|papelería|mascotas|juguetes|manualidades|limpieza|deporte|número|tipo|color|material|cantidad|incluye|sí|no)\b",re.I)
CJK=re.compile(r"[\u3400-\u9fff]")
def t(v): return "" if v is None else str(v).strip()
def sku_sort(x): return (int(x) if x.isdigit() else 10**18,x)
def need(field,current,source):
 return bool(source) and (not current or current==source or (field in {'cat1','cat2','name','spec'} and not CJK.search(current)) or (field in {'description','details'} and (not CJK.search(current) or SPANISH.search(current))) or bool(SPANISH.search(current)))
def main():
 cfg=load_settings(); records=ProductionRepository(database_path(cfg)).load_current_export_records(); baseline,_=build_zh_rows(records,load_dictionary_context(cfg)); base={t(r['编号']):dict(r) for r in baseline}; rec={t(r['sku']):r for r in records}
 outputs=Path(cfg['paths']['temp'])/'gpt56_luna_outputs'
 ordered=sorted(records,key=lambda r: sku_sort(t(r['sku'])))
 candidate_order=[]; needed_by={}
 for record in ordered:
  sku=t(record['sku']); row=base[sku]
  needed=[field for field in FIELDS if need(field,t(row[ROW[field]]),t(record[ES[field]]))]
  if needed:
   candidate_order.append(sku); needed_by[sku]=needed
 luna_by={}
 for n in range(1,13):
  path=outputs/f'batch_{n}.json'
  if not path.exists(): continue
  payload=json.loads(path.read_text(encoding='utf-8'))
  if isinstance(payload,dict) and isinstance(payload.get('items'),list): payload=payload['items']
  if not isinstance(payload,list): raise RuntimeError(f'LUNA_BATCH_NOT_LIST:{n}')
  for row in payload:
   luna_by[t(row['sku'])]=row
 missing_skus=sorted(set(candidate_order)-set(luna_by),key=sku_sort)
 if missing_skus: raise RuntimeError(f'LUNA_MISSING_SKUS:{len(missing_skus)}:{missing_skus[:20]}')
 applied=0
 for sku in sorted(base,key=sku_sort):
  if sku not in needed_by: continue
  needed=needed_by[sku]; luna=luna_by.get(sku,{})
  for field in needed:
   target=ROW[field]
   value=t(luna.get(field))
   if value: base[sku][target]=value
  applied+=1
 # preserve empty official source fields; no model may invent them.
 for sku,row in base.items():
  source=rec[sku]
  if not t(source.get('desc_es')): row['描述']=''
  if not t(source.get('details_es')): row['产品详情']=''
 rows=[base[k] for k in sorted(base,key=sku_sort)]
 out=Path(cfg['paths']['exports'])/'20260907Action商品全量_中文版_GPT56Luna优化版_不带图.xlsx'
 profile={'sheet_name':'商品全量','freeze_panes':'A2','auto_filter':True,'header':{'bold':True,'fill':'1F4E78','font_color':'FFFFFF'},'body':{'wrap_text_columns':['标题','分类1','分类2','规格','描述','产品详情','备注'],'max_row_height':405},'price':{'number_format':'€#,##0.00'}}
 headers=['图片','编号','标题','分类1','分类2','规格','折后价','原价','单价','描述','产品详情','图片链接','商品链接','备注']
 write_catalog_xlsx(out,headers=headers,rows=rows,workbook_format=profile)
 audit={'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),'date':'2026-09-07','sku_count':len(rows),'candidate_skus':len(candidate_order),'applied_candidate_skus':applied,'gpt56_luna_rows':len(luna_by),'model_provider':'GPT-5.6-Luna','legacy_cache_used':False,'local_model_cache_used':False,'output':str(out),'fields':{}}
 for field,target in ROW.items():
  blank=sum(not t(r.get(target)) for r in rows); same=sum(1 for r in rows if t(r.get(target)) and t(r.get(target))==t(rec[t(r['编号'])].get(ES[field]))); residual=[t(r['编号']) for r in rows if SPANISH.search(t(r.get(target))) and not (field in {'name','cat1','cat2','spec'} and CJK.search(t(r.get(target))))]
  audit['fields'][field]={'blank':blank,'same_as_spanish':same,'spanish_marker_candidates':len(residual),'sample_residual_skus':residual[:25]}
 audit_path=out.with_name('20260907Action商品全量_中文版_GPT56Luna优化版_不带图.audit.json'); audit_path.write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps(audit,ensure_ascii=False))
if __name__=='__main__': main()

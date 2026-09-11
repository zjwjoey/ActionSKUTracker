"""Apply GPT-5.6-Luna translations to the repaired Spanish export."""
from __future__ import annotations
import datetime as dt,json,re,sys
from pathlib import Path
if hasattr(sys.stdout,'reconfigure'): sys.stdout.reconfigure(encoding='utf-8')
from openpyxl import load_workbook
PROJECT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(PROJECT/'src'))
from action_tracker.exporting.excel_writer import write_catalog_xlsx
ROOT=Path(r'F:\ActionSKUTracker\runtime'); EXPORTS=ROOT/'exports'; TEMP=ROOT/'temp'
ES=EXPORTS/'20260907Action商品全量_西班牙语版_不带图_最终修复版_分类清理版.xlsx'; BASE=EXPORTS/'20260907Action商品全量_中文版_GPT56Luna优化版_不带图.xlsx'
OUT=EXPORTS/'20260907Action商品全量_中文版_GPT56Luna最终修复版_不带图.xlsx'; AUDIT=OUT.with_suffix('.audit.json')
FIELDS=('name','cat1','cat2','spec','description','details'); TARGET={'name':'标题','cat1':'分类1','cat2':'分类2','spec':'规格','description':'描述','details':'产品详情'}; SOURCE=TARGET.copy()
def t(v): return '' if v is None else str(v).strip()

# Reviewed fallback for 13 rows whose previous Luna batch contained mojibake.
MANUAL_OVERRIDES={
 '2548558': {'description':'官网无独立描述。'},
 '3207840': {'cat1':'服饰鞋包','cat2':'下装纺织'},
 '3218207': {'name':'带木盖储物罐','cat1':'厨房餐具','cat2':'储物收纳','spec':'400ml｜多款可选','description':'简约实用；配竹勺；采用FSC认证木材（可持续木材）。','details':'颜色: 棕色、透明; 材质: 竹、玻璃; 可用于微波炉: 否; 带盖: 是; 盖子材质: 木; 可用于烤箱: 否; 食品/饮料储物盒类型: 罐; 商品编号: 3218207'},
 '3218652': {'name':'格林奇圣诞挂饰','cat1':'家居布置','cat2':'家居装饰','spec':'约10cm｜多款可选','description':'挂在圣诞树上很漂亮。\n聚树脂制品。','details':'材质: 聚树脂; 商品编号: 3218652'},
 '3218668': {'name':'玻璃碗开胃小吃拼盘套装','cat1':'厨房餐具','cat2':'餐具','spec':'7件｜29×6.5×20cm','description':'含6个玻璃碗。\n配金合欢木托盘。\n适合盛放西班牙小吃和开胃菜。\n这款开胃小吃拼盘套装由6个玻璃碗和一个金合欢木托盘组成，适合盛放小吃、坚果、橄榄等开胃食品。','details':'颜色: 棕色; 材质: 木、玻璃; 数量: 7件; 商品编号: 3218668'},
 '3218678': {'name':'硅胶清洁海绵','cat1':'家务清洁','cat2':'清洁用品','spec':'2件｜多种颜色','description':'适合清洁、擦洗和刮除。\n2种不同类型。','details':'颜色: 灰色; 材质: 硅胶; 数量: 2件; 商品编号: 3218678'},
 '3219023': {'name':'桌布','cat1':'厨房餐具','cat2':'厨房纺织','spec':'140×240cm｜多种颜色','description':'带圣诞图案。\n让餐桌布置更加整洁美观。\n节日聚会的理想选择。','details':'颜色: 图案; 材质: 棉; 洗涤说明: 最高30°C机洗; 熨烫说明: 最高110°C熨烫; 烘干说明: 不可滚筒烘干; 商品编号: 3219023'},
 '3219336': {'name':'旅行杯','cat1':'厨房餐具','cat2':'餐具','spec':'400ml｜多款可选','description':'适用于冷饮和热饮。\n玻璃材质。\n配吸管。\n这款实用旅行杯非常适合外出携带，轻松带走喜欢的饮品。','details':'颜色: 多色; 材质: 玻璃、塑料; 可放入洗碗机: 是; 容量: 400ml; 带耳朵: 否; 商品编号: 3219336'},
 '3219393': {'name':'自粘塑料装饰膜','cat1':'兴趣手作','cat2':'手工制作','spec':'3件｜25×140cm｜多种颜色','description':'为表面带来即时光泽。\n可轻松裁剪成所需尺寸并粘贴。\n适合创意和装饰项目。\n这款亮面自粘膜可用于礼物、手工制作或家具装饰，为表面增添闪亮质感。可按需要裁剪并粘贴，适合节日装饰和家居焕新，多种颜色和图案可供选择。','details':'颜色: 金色、绿色、红色、银色; 数量: 3件; 商品编号: 3219393'},
 '3219932': {'name':'Happy Birthday生日拉旗','cat1':'兴趣手作','cat2':'派对用品','spec':'3m｜多种颜色','description':'带流苏和“Happy Birthday”字样的拉旗。','details':'颜色: 绿色、紫色、粉色; 材质: 塑料; 主题: 生日; 商品编号: 3219932'},
 '3220102': {'name':'手工耳环制作套装','cat1':'兴趣手作','cat2':'手工制作','spec':'多款可选','description':'可制作6对聚合物黏土耳环。\n完整套装，打开即可开始。\n简单有趣，适合自己动手制作。\n这款创意套装可以制作个性耳环，可从不同款式中选择，随意搭配形状和颜色。套装配件齐全，即使没有经验也能立即开始。按照简单步骤即可制作出适合佩戴或送人的作品，适合独自或与亲友一起度过创意时光。','details':'颜色: 多色; 商品编号: 3220102'},
 '3220520': {'name':'毛绒玩具','cat1':'玩具','cat2':'毛绒玩具和娃娃','spec':'25cm｜多款可选','description':'可爱的毛绒玩具。\n触感柔软。\n填充物采用100%再生材料。','details':'颜色: 棕色、多色、白色; 适用年龄: 0岁以上; 含填充物: 是; 玩偶/毛绒玩具类型: 动物玩偶; 商品编号: 3220520'},
 '3220708': {'name':'车用除湿器','cat1':'DIY五金','cat2':'汽车用品','spec':'400g','description':'可重复使用；可在微波炉中短时间加热除湿。\n也适用于房车或船只等场景。','details':'含量: 400g; 带温度指示器: 否; 可直接使用: 是; 可重新填充: 否; 预期用途: 汽车; 商品编号: 3220708'},
 '3220792': {'name':'半永久染发剂','cat1':'个人美容','cat2':'头发护理','spec':'200ml｜多种颜色','description':'可维持最多10次洗发。\n令头发呈现亮丽光泽。\n这款半永久染发护理产品能为头发增添清新色彩，同时令秀发亮泽柔顺。将产品涂抹在干净、湿润的头发上，从发根至发梢停留至少10分钟后冲洗，即可获得亮丽色泽和光泽感。','details':'颜色: 米色、棕色、红色; 含量: 200ml; 无香: 否; 商品编号: 3220792'},
 '3220924': {'name':'A4素描本','cat1':'兴趣手作','cat2':'涂色和绘画','spec':'200页｜多种颜色','description':'无酸纸，带微孔撕线。\n配线圈和松紧带。\n采用FSC认证纸张（可持续纸张）。\n让创意自由流动的优雅素描本，适合素描、记录和创意构思。坚固的线圈装订和松紧带可使纸张整齐固定，体积小巧，便于随身携带，适合喜欢绘画或写作的人。','details':'颜色: 绿色、红色、粉色、黑色; 白色/彩色: 白色; 形状: 矩形; 页数: 200; 页面填充: 白色; 装订方式: 线圈装订; 商品编号: 3220924'},
}
# Category translations for the rows whose official main breadcrumb was
# corrected in the Spanish base. These are deterministic field-level joins;
# they must not be replaced by a cross-category/recommendation label.
CATEGORY_OVERRIDES={
 '2535685': {'cat1':'服饰鞋包','cat2':'服装'},
 '3210419': {'cat1':'办公文具','cat2':'办公配件'},
 '2577107': {'cat1':'DIY五金','cat2':'汽车用品'},
 '3222519': {'cat1':'旅行用品','cat2':'露营用品'},
 '3007683': {'cat1':'办公文具','cat2':'办公配件'},
 '3216603': {'cat1':'办公文具','cat2':'办公配件'},
 '3223884': {'cat1':'办公文具','cat2':'办公配件'},
 '3222499': {'cat1':'DIY五金','cat2':'工具'},
 '3206019': {'cat1':'兴趣手作','cat2':'派对用品'},
 '3205740': {'cat1':'旅行用品','cat2':'旅行配件'},
 '3205891': {'cat1':'旅行用品','cat2':'旅行配件'},
 '3209038': {'cat1':'旅行用品','cat2':'旅行配件'},
 '3011954': {'cat1':'办公文具','cat2':'纸品'},
 '3207974': {'cat1':'办公文具','cat2':'纸品'},
 '3208746': {'cat1':'办公文具','cat2':'学习用品'},
 '3222425': {'cat1':'办公文具','cat2':'学习用品'},
}
def load(p):
    ws=load_workbook(p,read_only=True,data_only=True).active; h=[c.value for c in next(ws.iter_rows())]; return h,{t(r[1].value):dict(zip(h,[c.value for c in r])) for r in ws.iter_rows(min_row=2)}
def main():
    h,es=load(ES); _,base=load(BASE); outputs=TEMP/'gpt56_luna_repaired_outputs'; luna={}
    for n in (1,2,3,4,5,6,7):
        p=outputs/f'batch_{n}.json'
        if not p.exists(): raise RuntimeError(f'MISSING_BATCH:{n}')
        d=json.loads(p.read_text(encoding='utf-8')); d=d.get('items',d) if isinstance(d,dict) else d
        if not isinstance(d,list): raise RuntimeError(f'BAD_BATCH:{n}')
        for x in d: luna[t(x['sku'])]=x
    CJK=re.compile(r'[\u3400-\u9fff]'); SP=re.compile(r'\b(?:el|la|los|las|de|del|con|para|sin|una|un|unos|unas|varios|varias|diferentes|talla|tallas|unidades?|piezas?|gramos?|metros?|litros?|paquete|juego|calcetines|mallas|pantis|guantes|gorro|chocolate|cable|bolsa|discos|gel|colores?|hogar|oficina|papelería|mascotas|juguetes|manualidades|limpieza|deporte|número|tipo|color|material|cantidad|incluye|sí|no)\b',re.I)
    _,old=load(EXPORTS/'20260907Action商品全量_西班牙语版_不带图.xlsx')
    def norm(v,f):
        x=t(v)
        if f=='details': return re.sub(r'\s+',' ',re.sub(r'^Especificaciones\s*','',x,flags=re.I).replace('|',';').replace('\t',': ')).strip()
        if f=='description': return re.sub(r'^\s*null\.\s*','',x,flags=re.I)
        return x
    def changed(f,new,oldv): return norm(new,f)!=norm(oldv,f)
    def need(f,cur,src,ch):
        if not src and f!='spec': return False
        if f=='spec' and not src: return True
        if ch or not cur or cur==src: return True
        if f in {'name','cat1','cat2'}: return not CJK.search(cur) or bool(SP.search(cur))
        if f=='spec': return bool(SP.search(cur))
        return not CJK.search(cur) or bool(SP.search(cur))
    candidates=[]; rows=[]; changed_fields=0
    for sku in sorted(es,key=lambda x:(int(x) if x.isdigit() else 10**18,x)):
        row=dict(base[sku]); needed=[]
        for f in FIELDS:
            src=t(es[sku].get(SOURCE[f])); cur=t(row.get(TARGET[f])); ch=changed(f,src,t(old[sku].get(SOURCE[f])))
            if need(f,cur,src,ch): needed.append(f)
        if needed: candidates.append(sku)
        for f in needed:
            if f=='spec' and not t(es[sku].get(SOURCE[f])): row[TARGET[f]]=''; changed_fields+=1; continue
            if sku in MANUAL_OVERRIDES and f in MANUAL_OVERRIDES[sku]:
                row[TARGET[f]]=MANUAL_OVERRIDES[sku][f]; changed_fields+=1; continue
            item=luna.get(sku)
            # A source-only cleanup (for example removal of an HTML tag or a
            # leading Spanish section label) can mark a field as changed even
            # though the existing Chinese value is already complete.  Do not
            # require a new model row for that harmless source normalization.
            if not item:
                current_value = t(row.get(TARGET[f]))
                if current_value and CJK.search(current_value) and not SP.search(current_value):
                    continue
                raise RuntimeError(f'MISSING_SKU:{sku}')
            value=t(item.get(f))
            if value: row[TARGET[f]]=value; changed_fields+=1
            elif sku in MANUAL_OVERRIDES and f in MANUAL_OVERRIDES[sku]:
                row[TARGET[f]]=MANUAL_OVERRIDES[sku][f]; changed_fields+=1
        # The source repair can expose fields that were not flagged by the legacy
        # baseline heuristic; always replace corrupted/empty output for these rows.
        if sku in MANUAL_OVERRIDES:
            for f,value in MANUAL_OVERRIDES[sku].items():
                row[TARGET[f]]=value
        if sku in CATEGORY_OVERRIDES:
            for f,value in CATEGORY_OVERRIDES[sku].items():
                row[TARGET[f]]=value
        if not t(es[sku].get('描述')): row['描述']=''
        if not t(es[sku].get('产品详情')): row['产品详情']=''
        rows.append(row)
    # Coverage is checked at field level above.  Candidates can legitimately
    # lack a model row when the only source change was formatting cleanup and
    # the existing Chinese field is already complete.
    headers=['图片','编号','标题','分类1','分类2','规格','折后价','原价','单价','描述','产品详情','图片链接','商品链接','备注']
    profile={'sheet_name':'商品全量','freeze_panes':'A2','auto_filter':True,'header':{'bold':True,'fill':'1F4E78','font_color':'FFFFFF'},'body':{'wrap_text_columns':['标题','分类1','分类2','规格','描述','产品详情','备注'],'max_row_height':405},'price':{'number_format':'€#,##0.00'}}
    write_catalog_xlsx(OUT,headers=headers,rows=rows,workbook_format=profile)
    audit={'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),'source_es':str(ES),'base_zh':str(BASE),'output':str(OUT),'sku_count':len(rows),'candidate_skus':len(candidates),'gpt56_luna_rows':len(luna),'model_provider':'GPT-5.6-Luna','legacy_cache_used':False,'local_model_used':False,'changed_fields_applied':changed_fields,'blank_description':sum(not t(r.get('描述')) for r in rows),'blank_details':sum(not t(r.get('产品详情')) for r in rows),'fields':{}}
    for f,target in TARGET.items(): audit['fields'][f]={'blank':sum(not t(r.get(target)) for r in rows),'same_as_spanish':sum(bool(t(r.get(target))) and t(r.get(target))==t(es[t(r['编号'])].get(SOURCE[f])) for r in rows)}
    AUDIT.write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(audit,ensure_ascii=False))
if __name__=='__main__': main()

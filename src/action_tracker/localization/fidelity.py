"""Field-local identity, attribute and classifier contracts.

These checks do not translate free prose. Repairs restore only literal,
uniquely identified own-field facts. Uncertain identities remain review work.
"""
from __future__ import annotations
import re
import unicodedata
from functools import lru_cache
from .normalization.structured_details import parse_structured_details,StructuredDetail

IDENTITY_ALIASES = {
    'Disney': ('迪士尼',), 'Barbie': ('芭比',), 'Canon': ('佳能',),
    'Swarovski': ('施华洛世奇',), "Betty's": ('贝蒂',),
    'Van Bleiswijck': ('范·布莱斯韦克','范布莱斯韦克'),
    'Oreo': ('奥利奥',), 'Milka': ('妙卡',), 'Snickers': ('士力架',),
    'Twix': ('特趣',), 'Chupa Chups': ('珍宝珠',), 'Toblerone': ('瑞士三角',),
    'Adidas': ('阿迪达斯',), 'Nivea': ('妮维雅',), 'Gillette': ('吉列',),
    'Oral-B': ('欧乐B','欧乐-B'), 'Magnum': ('梦龙',), 'Dove': ('多芬',),
    'Whiskas': ('伟嘉',), 'Coca-Cola': ('可口可乐',), 'Colgate': ('高露洁',),
    'Haribo': ('哈瑞宝','哈里波'), 'Mars': ('玛氏',), 'Tefal': ('特福',),
    'Staedtler': ('施德楼',), 'Remington': ('雷明顿',), 'LEGO': ('乐高',),
    'Stitch': ('史迪奇','史迪仔'), 'Bluey': ('布鲁伊',),
    "Gabby's Dollhouse": ('盖比','加比','嘉比'), 'Pokémon': ('宝可梦',),
    'Daim': ('黛姆',), 'Cheetos': ('奇多',), 'Nestlé': ('雀巢',),
    'Paw Patrol': ('汪汪队',), 'KitKat': ('奇巧',),
    'George Wilkinson': ('乔治·威尔金森',), 'Sentimat': ('森蒂玛特',),
    'Werckmann': ('沃克曼','维尔克曼'), 'Sologic': ('索洛吉','索洛吉克'),
    'Grundig': ('格伦迪格',), 'Air Wick': ('爱丽威',),
    'Noodlicious': ('努德利修斯',), 'Bonjolie': ('邦乔莉',),
    'Pralin Art': ('巧克力艺术',), 'Philips': ('飞利浦',), 'Panasonic': ('松下',),
    'Body Love': ('身体之爱','身体宠爱'), 'Mickey Mouse': ('米奇老鼠','米奇'),
    'Frozen': ('冰雪奇缘',),
    '7Up': ('七喜',), 'Nortec': ('Nor-Tec','诺特克','诺尔泰克'),
    'Hotel Royal': ('皇家酒店',), 'Hobby Flora': ('霍比弗洛拉',),
    'Dumil': ('杜米尔',), 'Luminarc': ('卢米纳克',),
    'Crafts & Co': ('工艺品与公司',), 'Harry Potter': ('哈利·波特','哈利波特'),
    'Hatakosen': ('哈塔科森',), 'Grinch': ('格林奇',),
    'Alvira': ('阿尔维拉',), 'Nutini': ('努蒂尼',), 'Star Wars': ('星球大战',),
    'Amos': ('阿莫斯',), 'Nusuk': ('努苏克',),
    'Mr. Goodlad': ('古德莱德先生',), "Becky's": ('贝基',),
    'Trolli': ('特洛利',), 'Lion': ('狮子',), 'Bosch': ('博世',),
    'Head & Shoulders': ('Head&Shoulders','海飞丝'), 'MAOAM': ('毛阿姆',),
}
# Lowercase ordinary words are not evidence of a brand role.
AMBIGUOUS_IDENTITIES = {'sola','perla','jumbo','fabulosa','make-over','second skin'}
TECHNICAL_IDENTITIES = {'usb','usb-a','usb-c','led','lcd','fsc','pefc','mdf','rms','gsm','diy','pc','tv','uv','hss','bci','anc','enc','wc'}

def identity_key(text):
    return re.sub(r'[^\w]', '', unicodedata.normalize('NFKC',str(text or '')).casefold())

def identity_present(target, name):
    key=identity_key(target)
    return any(identity_key(v) in key for v in (name,*identity_aliases(name)) if identity_key(v))

def identity_aliases(name):
    return next((aliases for canonical,aliases in IDENTITY_ALIASES.items() if identity_key(canonical)==identity_key(name)),())

@lru_cache(maxsize=16)
def _identity_patterns(names):
    result = []
    for name in sorted(names,key=lambda v:(-len(v),v.casefold())):
        if name.casefold() in TECHNICAL_IDENTITIES or (len(name)<3 and name!='3M'):
            continue
        aliases = tuple(a for a in identity_aliases(name) if a.isascii())
        pattern = re.compile(r'(?<![\w])(?:'+ '|'.join(re.escape(a) for a in (name,*aliases)) +r')(?![\w])',re.I)
        result.append((name, pattern, name.casefold(), tuple(a.casefold() for a in aliases)))
    return tuple(result)

def source_identities(source, names):
    """Return approved names actually stated in this field, longest first."""
    source=str(source or '')
    if not source:return ()
    folded=source.casefold()
    results=[]
    covered=[]
    for name,pattern,lower,aliases in _identity_patterns(tuple(sorted(set(names)))):
        if lower not in folded and not any(alias in folded for alias in aliases):
            continue
        for m in pattern.finditer(source):
            if any(m.start()>=a and m.end()<=b for a,b in covered):continue
            if name.casefold() in AMBIGUOUS_IDENTITIES:
                # Explicit "de Second Skin" is a name; "una sola", pearl
                # effect, jumbo size and make-over activity are not names.
                if not (m.group()[0].isupper() and re.search(r'\bde\s*$',source[:m.start()])):continue
            results.append(m.group());covered.append(m.span());break
    return tuple(results)

def restore_body_identities(source,target,names):
    """Expose missing, approved own-field identities as an auditable clause."""
    missing=[n for n in source_identities(source,names) if not identity_present(target,n)]
    if not missing:return target
    # A source-faithful identity clause preserves role without inventing a
    # marketing sentence or borrowing an identity from the product title.
    return str(target).rstrip('；; ')+'；原文品牌/系列：'+'、'.join(missing)

def body_identity_facts(source,names):
    from .contracts import SemanticFact
    return tuple(SemanticFact('BRAND',name,name,name,field,
        'approved_dictionary_own_field_identity',1.0,'',source.source_hash)
        for field in ('desc_es','details_es')
        for name in source_identities(getattr(source,field,''),names))

def strip_name_identity_aliases(source,target,names):
    text=str(target or '')
    for name in source_identities(source,names):
        for alias in (name,*identity_aliases(name)):
            replacement='可乐' if identity_key(name)=='cocacola' else ''
            text=re.sub(r'(?<![A-Za-z0-9_])'+re.escape(alias)+r'(?:牌|的)?(?![A-Za-z0-9_])',replacement,text,flags=re.I)
    if re.search(r'\bCoca-Cola\s+Zero\b',str(source),re.I) and text.strip()=='可乐零':text='零度可乐'
    return re.sub(r'\s{2,}',' ',text).strip(' 、；;')

COLORS = {'azul':('蓝',),'púrpura':('紫',),'amarillo':('黄',),'rojo':('红',),
    'negro':('黑',),'blanco':('白',),'verde':('绿',),'rosa':('粉','玫瑰'),
    'marrón':('棕','褐'),'naranja':('橙','橘'),'gris':('灰',),'dorado':('金',),
    'plateado':('银',),'beige':('米','杏'),'transparente':('透明',),
    'gris topo':('灰褐','灰棕','褐灰','驼灰','鼹灰','灰驼')}
MATERIALS = {'dolomita':('白云石',),'vidrio':('玻璃',),
    'polipropileno':('聚丙烯','PP'),'polietileno':('聚乙烯','PE'),
    'poliestireno':('聚苯乙烯','PS'),'poliéster':('聚酯','涤纶'),
    'algodón':('棉',),'silicona':('硅胶','硅酮'),'cerámica':('陶瓷',),
    'acero inoxidable':('不锈钢',),'lino':('亚麻',),'cobre':('铜',),
    'aluminio':('铝',),'hierro':('铁',),'madera':('木',),'bambú':('竹',),
    'plástico':('塑料',),'poliamida':('聚酰胺','尼龙','锦纶'),'fieltro':('毡',),
    'lana':('羊毛','毛料'),'viscosa':('粘','黏','人造丝'),'cartón':('纸板',),
    'papel':('纸',),'polirresina':('聚树脂','树脂'),'piedra':('石',)}

def detail_attribute_issues(source,target):
    """Compare exact attribute roles; never pair Material base with Material."""
    src=parse_structured_details(str(source or ''))
    dst=parse_structured_details(str(target or ''))
    issues=[]
    for sk,tk,mapping in [('color','颜色',COLORS),('material','材质',MATERIALS)]:
        a=[r for r in src if (r.key.casefold()==sk or (sk=='material' and r.key.casefold()=='sustancia' and r.value.casefold() in MATERIALS)) and ':' not in r.value and '：' not in r.value and not (sk=='material' and re.match(r'^(?:base|del |de la |estructura)',r.value,re.I))]
        b=[r for r in dst if (r.key==tk or (sk=='material' and r.key=='形态' and any(x.key.casefold()=='sustancia' and x.value.casefold() in MATERIALS for x in src))) and ':' not in r.value and '：' not in r.value]
        if not a:continue
        if len(src)==1 and not dst:
            # Legacy single-attribute prose is still a valid representation;
            # multi-attribute tables must retain their roles/counts.
            b=[StructuredDetail(tk,str(target),str(target),tk)]
        if len(a)!=len(b):
            issues.append(('DETAIL_ATTRIBUTE_COUNT_MISMATCH',{'attribute':sk,'source_count':len(a),'target_count':len(b)}));continue
        for s,t in zip(a,b):
            terms=s.value.casefold()
            # Compounds are indivisible materials, not a separate assertion
            # of polyethylene or wood composition.
            if 'mdf' in terms and any(x in t.value for x in ['MDF','中密度纤维板']):terms=re.sub(r'\bmadera\s+mdf\b',' ',terms)
            if 'tereftalato de polietileno' in terms and any(x in t.value for x in ['聚对苯二甲酸乙二醇酯','PET']):terms=terms.replace('tereftalato de polietileno',' ')
            for term,aliases in mapping.items():
                if term=='gris' and 'gris topo' in terms:continue
                if re.search(r'(?<!\w)'+re.escape(term)+r'(?!\w)',terms,re.I) and not any(x.casefold() in t.value.casefold() for x in aliases):
                    issues.append(('DETAIL_ATTRIBUTE_VALUE_MISSING',{'attribute':sk,'source_value':s.value,'source_term':term,'target_value':t.value}))
    # Qualified materials are distinct attribute roles, checked independently.
    roles={'base':('基材','表壳材质'),'del asa':('把手材质','手柄材质','提手材质'),'del mango':('手柄材质','把手材质','提手材质'),
           'de la cubierta':('盖子材质','外罩材质'),'estructura':('主体结构材质','镜架材质','框架材质','结构材质'),
           'del candelabro':('烛台材质',),'de la lente':('镜片材质',)}
    for prefix,keys in roles.items():
        own=[r for r in src if r.key.casefold()=='material' and re.match(re.escape(prefix)+r'(?=\s|:|：)',r.value,re.I)]
        if not own:continue
        counterparts=[r for r in dst if r.key in keys or (r.key=='材质' and ((prefix=='estructura' and r.value.startswith('镜架：')) or (prefix=='de la lente' and r.value.startswith('镜片：'))))]
        if len(own)!=len(counterparts):
            issues.append(('DETAIL_ATTRIBUTE_COUNT_MISMATCH',{'attribute':'material '+prefix,'source_count':len(own),'target_count':len(counterparts)}));continue
        for source_pair,target_pair in zip(own,counterparts):
            value=re.sub('^'+re.escape(prefix)+r'\s*[:：]?\s*','',source_pair.value,flags=re.I).casefold()
            for term,aliases in {**MATERIALS,'metal':('金属',)}.items():
                if re.search(r'(?<!\w)'+re.escape(term)+r'(?!\w)',value,re.I) and not any(alias.casefold() in target_pair.value.casefold() for alias in aliases):
                    issues.append(('DETAIL_ATTRIBUTE_VALUE_MISSING',{'attribute':'material '+prefix,'source_term':term,'target_value':target_pair.value}))
    for item in src:
        if item.key.casefold()=='incluye oído' and item.value.strip().casefold() in {'sí','si','no'}:
            expected='官网字段Incluye oído='+item.value.strip()
            if expected not in str(target):issues.append(('SOURCE_ANOMALY_VALUE_DROPPED',{'source_key':item.key,'source_value':item.value}))
    return issues

def repair_detail_attributes(source,target):
    """Restore complete known lists in the same attribute, preserving order."""
    text=str(target or '')
    src=parse_structured_details(str(source or ''))
    color_names={'gris topo':'灰褐色','multicolor':'多色','diseño':'图案'}
    color_names.update({k:v[0]+('色' if v[0] not in {'透明','灰褐'} else '') for k,v in COLORS.items() if k!='gris topo'})
    material_names={k:v[0] for k,v in MATERIALS.items()}
    material_names.update({'poliéster':'聚酯纤维','polipropileno (pp)':'聚丙烯（PP）'})
    for sk,tk,terms in [('color','颜色',color_names),('material','材质',material_names)]:
        own=[r for r in src if r.key.casefold()==sk and ':' not in r.value and '：' not in r.value]
        matched=list(re.finditer(r'(?:^|(?<=[;；|｜\n]))\s*'+re.escape(tk)+r'\s*[:：]\s*([^;；|｜\n]+)',text))
        if len(own)!=len(matched):continue
        replacements=[]
        for s,m in zip(own,matched):
            # Apply only a diagnosed discrepancy, not a whole-list style sweep.
            local=detail_attribute_issues(s.source_text,tk+'：'+m.group(1))
            if not local:continue
            pieces=[v.strip().casefold() for v in s.value.split(',')]
            if not all(v in terms for v in pieces):continue
            replacements.append((m.start(1),m.end(1),'、'.join(terms[v] for v in pieces)))
        for start,end,value in reversed(replacements):text=text[:start]+value+text[end:]
    return text

def specification_classifier(source,name,target):
    """Only change a generic classifier attached to a source count.

    A package count (Paquete de 5) is packs, not five pieces of gum. Product
    context selects the classifier; the count itself must come from spec.
    """
    name=str(name or '')
    classifier=None
    for pattern,unit in [(r'\bservilletas\b','张'),(r'\bchicles\b','粒'),
                         (r'\b(?:cápsulas|comprimidos)\b','粒'),
                         (r'\bbastoncillos\b','支'),(r'\bpilas\b','节'),
                         (r'\b(?:calzoncillos|braguitas)\b','条')]:
        if re.search(pattern,name,re.I):classifier=unit;break
    if classifier is None:return target
    if re.search(r'\bpaquete\s+de\s+\d+',str(source),re.I):classifier='包'
    counts=set(re.findall(r'(?<!\d)(\d+)\s*(?:unidades?|uds?\.?|piezas?)\b',str(source),re.I))
    if classifier=='包':counts.update(re.findall(r'\bpaquete\s+de\s+(\d+)',str(source),re.I))
    return re.sub(r'(?<!\d)(\d+)\s*件',lambda m:m.group(1)+classifier if m.group(1) in counts else m.group(),str(target))


def field_semantic_issues(source, target, field):
    """Diagnose bounded word-sense contracts, without generating a translation.

    A matching source attribute supplies the role. Equal pair counts preserve
    occurrence order and duplicates; uncertain or unaligned data is left to
    the existing structure and human review gates.
    """
    source, target = str(source or ''), str(target or '')
    issues = []
    if field == 'description':
        if (re.search(r'\bpoliéster\b', source, re.I)
                and not re.search(r'\bpoliamida\b', source, re.I)
                and '聚酰胺' in target):
            issues.append(('POLYESTER_CHANGED_TO_POLYAMIDE', {}))
        if (re.search(r'\bAAA\b', source) and not re.search(r'\bAA\b', source)
                and re.search(r'(?<![A-Za-z])AA(?![A-Za-z])', target)):
            issues.append(('OWN_FIELD_BATTERY_MODEL_CONTRADICTION', {}))
        return issues
    if field == 'name':
        for pattern, bad, meaning in (
            (r'\bbolsas\s+para\s+lavadora\b', r'袜子', 'laundry bag'),
            (r'\b(?:lienzos|paneles de pintura)\b', r'清洁布', 'painting canvas/panel'),
            (r'\bcaballete\s+de\s+corte\b', r'画架', 'cutting support'),
            (r'\bfiletes\b.*\b(?:pollo|pato)\b', r'鱼片', 'poultry fillets'),
        ):
            if re.search(pattern, source, re.I) and re.search(bad, target):
                issues.append(('PRODUCT_WORD_SENSE_CHANGED', {'source_role': meaning}))
        return issues
    if field != 'details':
        return issues
    src, dst = parse_structured_details(source), parse_structured_details(target)
    if len(src) != len(dst):
        return issues
    for a, b in zip(src, dst):
        key, value = a.key.casefold(), a.value.casefold().strip()
        evidence = {'source_key': a.key, 'source_value': a.value,
                    'target_key': b.key, 'target_value': b.value}
        code = None
        # Preserve the literal negative predicate. No means the free-from
        # proposition is false; do not infer ingredient presence/absence.
        if key in {'sin colorantes artificiales', 'sin conservantes'}:
            subject = '人工色素' if key == 'sin colorantes artificiales' else '防腐剂'
            if subject in b.key and '不含' not in b.key and '无' not in b.key:
                code = 'NEGATIVE_ATTRIBUTE_ROLE_CHANGED'
        elif key == 'apto para tipo de dentadura' and value == 'dentadura de adultos' and '假牙' in b.key + b.value:
            code = 'DENTITION_CHANGED_TO_DENTURE'
        elif key == 'número de posiciones de sentado' and '座位数' in b.key:
            code = 'SEATING_POSITION_CHANGED_TO_CAPACITY'
        elif key == 'tipo de dispositivo de corrección' and '矫正器械' in b.key:
            code = 'STATIONERY_CHANGED_TO_MEDICAL_DEVICE'
        elif key == 'hipoalergénico' and not any(t in b.key for t in ('低敏', '低致敏', '低过敏')):
            code = 'HYPOALLERGENIC_SCOPE_CHANGED'
        elif key == 'vegano' and '素食' in b.key and '纯素' not in b.key:
            code = 'VEGAN_SCOPE_CHANGED'
        elif key == 'tipo de taza / vaso' and ('玻璃类型' in b.key or (value.startswith('vaso') and '玻璃杯' in b.value)):
            code = 'CUP_TYPE_CHANGED_TO_MATERIAL'
        elif key == 'modo de transporte' and 'oído' in value and '携带方式' in b.key:
            code = 'WEARING_FORM_CHANGED_TO_TRANSPORT'
        elif key == 'apto para tipo de fuente de calor' and 'eléctrico' in value and '电动' in b.value:
            code = 'ELECTRIC_HEAT_CHANGED_TO_MOTOR'
        elif key == 'contenido' and re.search(r'\d\s*(?:g|kg|gramos?|kilogramos?)\b', value) and '容量' in b.key:
            code = 'MASS_ATTRIBUTE_CHANGED_TO_CAPACITY'
        elif key == 'halal' and '认证' in b.key:
            code = 'CERTIFICATION_QUALIFIER_ADDED'
        if code:
            issues.append((code, evidence))
    return issues


def repair_reviewed_detail_roles(source, target):
    """Finite, own-attribute corrections used in a reviewed candidate stage.

    This does not approve or Apply anything. Source order and pair count must
    match, and unlisted attributes remain byte-for-byte intact.
    """
    src, dst = parse_structured_details(source), parse_structured_details(target)
    if len(src) != len(dst):
        raise ValueError('DETAIL_ROLE_REPAIR_ALIGNMENT_REQUIRED')
    result = []
    for a, b in zip(src, dst):
        key, value = b.key, b.value
        sk, sv = a.key.casefold(), a.value.casefold().strip()
        if sk == 'apto para tipo de dentadura' and sv == 'dentadura de adultos':
            key, value = '适用牙列类型', '成人牙齿'
        elif sk in {'sin colorantes artificiales', 'sin conservantes'}:
            key = '不含人工色素' if sk == 'sin colorantes artificiales' else '不含防腐剂'
        elif sk == 'tipo de dispositivo de corrección' and sv == 'goma':
            key, value = '涂改工具类型', '橡皮'
        elif sk == 'número de posiciones de sentado':
            key = '坐姿调节档位数'
        elif sk == 'hipoalergénico':
            key = '低致敏'
        elif sk == 'vegano':
            key = '纯素'
        elif sk == 'tipo de taza / vaso':
            key = '杯子类型'
            if sv == 'vaso (no desechable)':
                value = '饮用杯（非一次性）'
        elif sk == 'modo de transporte' and 'oído' in sv:
            key = '佩戴方式'
        elif sk == 'apto para tipo de fuente de calor':
            key = '适用热源类型'
            if 'eléctrico' in sv:
                value = value.replace('电动', '电加热')
        elif sk == 'contenido' and re.search(r'\d\s*(?:g|kg|gramos?|kilogramos?)\b', sv):
            key = '净含量'
        elif sk == 'halal' and '认证' in key:
            key = '清真'
        result.append(b.source_text if (key, value) == (b.key, b.value) else f'{key}：{value}')
    return '；'.join(result)

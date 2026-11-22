"""CI_SAFE: exposed real review fixtures, plus boundary/negative controls.

These reviewed examples are regression evidence, not unseen accuracy evidence.
No live database, provider, whitelist or production path is used by this test.
"""
import json,re
from pathlib import Path
import pytest
from action_tracker.localization.contracts import SourceFacts, SemanticFact
from action_tracker.localization.qa import guard_translation, _numbers, _semantic_aliases
from action_tracker.localization.body_names import mask_source_bound_body_names

CASES=json.loads((Path(__file__).parent/'fixtures/current156_qa.json').read_text('utf-8'))

def check(source,target,field='description',facts=()):
    attr={'name':'name_es','description':'desc_es','details':'details_es','spec':'spec_es'}[field]
    return guard_translation(SourceFacts(sku='regression',**{attr:source}),{field:target},(field,),semantic_facts=facts)

def rules(result):
    return {r['rule_id'] for r in result['findings']}

@pytest.mark.parametrize('case',CASES,ids=lambda r:r['sku']+'/'+r['field'])
def test_real_reviewed_own_field(case):
    result=guard_translation(SourceFacts(**case['source']),{case['field']:case['target']},(case['field'],),
                             semantic_facts=tuple(SemanticFact(**f) for f in case['semantic_facts']))
    assert result['overall_ready'], result['findings']

@pytest.mark.parametrize('phrase,term,canonical,target',[
 ('Paño hidrofílico','paño','清洁布','亲水巾'),
 ('Gorro y manoplas','manoplas','沐浴手套','连指手套'),
 ('Iluminación acogedora','iluminación','照明灯','温馨照明'),
 ('Iluminación sutil','iluminación','照明灯','柔和灯光'),
 ('Función: Iluminación ambiente','iluminación','照明灯','氛围照明'),
 ('14 funciones de iluminación','iluminación','照明灯','14种灯光模式'),
 ('imitación madera','madera','木质','木纹'),
 ('imitación madera','madera','木质','仿木纹'),
 ('calcetines de bambú. Producto hecho con bambú','bambú','竹制','竹纤维袜子'),
 ('68 % de viscosa de bambú. Calcetines de bambú','bambú','竹制','68%竹纤维粘胶袜子'),
])
def test_qualified_senses_are_not_global_waivers(phrase,term,canonical,target):
    fact=SemanticFact('MATERIAL' if term in {'madera','bambú'} else 'PRODUCT_TYPE',term,canonical,canonical,source_field='desc_es')
    assert 'SEMANTIC_FACT_DROPPED' not in rules(check(phrase,target,facts=(fact,)))
    negative=re.sub(r'^(\d+%?(?:种)?)',r'\1非',target) if target[0].isdigit() else '非'+target
    assert 'SEMANTIC_FACT_DROPPED' in rules(check(phrase,negative,facts=(fact,)))
    assert target not in _semantic_aliases(term,term,canonical)

def test_second_unqualified_occurrence_does_not_inherit_the_equivalence():
    assert '亲水巾' not in _semantic_aliases('paño','Paño hidrofílico; otro paño','清洁布')
    assert '竹纤维' not in _semantic_aliases('bambú','Calcetines de bambú; caja de bambú','竹制')
    assert '木纹' not in _semantic_aliases('madera','imitación madera; tapa de madera','木质')
    assert '连指手套' not in _semantic_aliases('manoplas','Gorro y manoplas; manoplas de baño','沐浴手套')
    assert '连指手套' not in _semantic_aliases('manoplas','Gorro y manoplas de baño','沐浴手套')
    assert '沐浴手套' in _semantic_aliases('manoplas','Gorro y manoplas de baño','沐浴手套')
    assert '柔和灯光' not in _semantic_aliases('iluminación','Iluminación sutil; otra iluminación','照明灯')

@pytest.mark.parametrize('term,canonical,atom',[('bambú','竹制','竹'),('madera','木质','木')])
def test_existing_typed_material_atoms_survive_affirmative_guard(term,canonical,atom):
    fact=SemanticFact('MATERIAL',term,canonical,source_field='details_es')
    assert 'SEMANTIC_FACT_DROPPED' not in rules(check('Material: '+term,'材质：'+atom,'details',(fact,)))
    assert 'SEMANTIC_FACT_DROPPED' in rules(check('Material: '+term,'材质：非'+atom,'details',(fact,)))

def test_mano_ordinary_attribute_does_not_authorize_model_loss():
    src='Tipo de fijación para el micrófono: MICRÓFONO DE MANO; Tipo de acoplamiento: USB-C'
    target='麦克风固定方式：手持麦克风；连接类型：USB-C'
    assert 'PROTECTED_TOKEN_MISSING' not in rules(check(src,target,'details'))
    assert 'PROTECTED_TOKEN_MISSING' in rules(check(src,target.replace('USB-C','USB'),'details'))
    assert {'PROTECTED_TOKEN_MISSING','PROTECTED_TOKEN_CHANGED'} & rules(check(src,target.replace('手持麦克风','非手持麦克风'),'details'))
    assert {'PROTECTED_TOKEN_MISSING','PROTECTED_TOKEN_CHANGED'} & rules(check(src+'; Modelo: MANO',target+'；型号：普通型','details'))
    assert 'PROTECTED_TOKEN_MISSING' in rules(check('Modelo: MANO','型号：手持麦克风','details'))

def test_one_hour_and_chinese_control_positions_keep_numbers_protected():
    assert _numbers('una hora')==_numbers('1小时')
    assert _numbers('3 posiciones')==_numbers('三档')
    assert _numbers('treinta y una hora')!={'1':1}
    assert 'NUMERIC_DROPPED' in rules(check('una hora y 45 minutos','充电约45分钟'))
    assert 'NUMERIC_ADDED' in rules(check('Una máscara','面罩需1小时'))
    assert 'NUMERIC_DROPPED' in rules(check('3 posiciones','两档'))

def test_repeated_spelled_lighting_counts_are_each_source_facts():
    # Real postscan case3221537: both source sentences state three settings.
    source='Con tres intensidades de luz; con tres posiciones de luz'
    target='三种亮度可选；三档灯光'
    assert _numbers(source)==_numbers(target)=={'3':2}
    assert 'NUMERIC_ADDED' not in rules(check(source,target))
    assert 'NUMERIC_DROPPED' in rules(check(source,'三档灯光'))
    assert 'NUMERIC_ADDED' in rules(check('Con tres intensidades de luz',target))
    assert 'NUMERIC_ADDED' in rules(check('Intensidades de luz','3档灯光'))
    assert 'NUMERIC_ADDED' in rules(check('treinta y tres intensidades de luz','3档灯光'))

@pytest.mark.parametrize('name',['Better Cotton Initiative','Action'])
def test_complete_own_field_body_identity_only(name):
    assert mask_source_bound_body_names(name,name).strip()==''
    assert mask_source_bound_body_names('Producto',name)==name
    assert mask_source_bound_body_names(name+'x',name)==name
    assert mask_source_bound_body_names(name,name+'x')==name+'x'
    assert 'SPANISH_RESIDUAL' in rules(check('Producto',name))
    assert 'SPANISH_RESIDUAL' in rules(check(name,name+' con producto'))

def test_verified_ip_brand_numbers_cannot_hide_quantities_or_cross_field():
    brand=SemanticFact('BRAND','Fórmula 1','Fórmula 1',source_field='name_es')
    assert 'NUMERIC_DROPPED' not in rules(check('KitKat Fórmula 1','赛车造型巧克力','name',(brand,)))
    assert 'NUMERIC_DROPPED' in rules(check('KitKat Fórmula 1, 1 unidad','赛车造型巧克力','name',(brand,)))
    assert 'NUMERIC_DROPPED' in rules(check('KitKat Fórmula 1','赛车造型巧克力','description',(brand,)))
    assert 'NUMERIC_DROPPED' in rules(check('Modelo F1, 1 unidad','赛车造型巧克力','name',(brand,)))


@pytest.mark.parametrize("phrase,term,canonical,target,extra,wrong", [
    ("Regaliz dulce de goma", "goma", "橡胶", "甘草软糖",
     "junta de goma", "甘草硬糖"),
    ("Despertador digital con iluminación que cambia de color", "iluminación",
     "照明灯", "带变色灯光的数字闹钟", "otra iluminación", "数字闹钟"),
])
def test_repair2_qualifies_only_the_complete_source_phrase(
        phrase, term, canonical, target, extra, wrong):
    kind = "MATERIAL" if term == "goma" else "PRODUCT_TYPE"
    fact = SemanticFact(kind, term, canonical, source_field="desc_es")

    def findings(source_text, translated_text):
        result = guard_translation(
            SourceFacts(sku="regression", desc_es=source_text),
            {"description": translated_text}, ("description",),
            semantic_facts=(fact,), production=True)
        return rules(result)

    assert "SEMANTIC_FACT_DROPPED" not in findings(phrase, target)
    assert "SEMANTIC_FACT_DROPPED" in findings(phrase, wrong)
    assert "SEMANTIC_FACT_DROPPED" in findings(extra, target)
    assert "SEMANTIC_FACT_DROPPED" in findings(phrase + "; " + extra, target)
    assert "SEMANTIC_FACT_DROPPED" not in findings(
        phrase + "; " + extra, target + "；" + canonical)


@pytest.mark.parametrize("source,target", [
    ("Calcetines footies", "船袜"),
    ("Calcetines de estar por casa", "家居袜"),
    ("Calcetines de estar por casa para niña", "女童居家袜"),
    ("Calcetines de mezcla de lana", "羊毛混纺袜"),
    ("Calcetines de trabajo", "工作袜"),
])
def test_reviewed_sock_title_noun_has_narrow_source_scope(source, target):
    fact = SemanticFact("PRODUCT_TYPE", "calcetines", "袜子", source_field="name_es")
    def has_error(text, localized):
        qa = guard_translation(
            SourceFacts(sku="regression", name_es=text), {"name": localized},
            ("name",), semantic_facts=(fact,), production=True)
        return "SEMANTIC_FACT_DROPPED" in rules(qa)
    assert not has_error(source, target)
    assert has_error(source, "毛线手套")
    assert has_error(source, "不含" + target)
    assert has_error("Calcetines", target)
    assert has_error(source + "; incluye otros calcetines", target)


def test_known_din_standard_accepts_format_without_accepting_other_numbers():
    failures = {"MODEL_DROPPED", "MODEL_CHANGED", "PROTECTED_TOKEN_ADDED",
                "PROTECTED_TOKEN_CHANGED", "PROTECTED_TOKEN_MISSING"}
    assert not findings_for_standard("Conforme a DIN13164", "符合DIN 13164标准") & failures
    assert not findings_for_standard("Conforme a DIN 13164", "符合DIN13164标准") & failures
    assert findings_for_standard("Conforme a DIN13164", "符合DIN 13165标准") & failures
    assert findings_for_standard("Conforme a DIN13164", "符合DIN标准") & failures
    assert findings_for_standard("Conforme a DIN13164; modelo GS27", "符合DIN 13164标准") & failures


def findings_for_standard(source, target):
    return rules(guard_translation(
        SourceFacts(sku="regression", desc_es=source), {"description": target},
        ("description",), production=True))


def test_mdf_translation_requires_the_exact_material_atom_and_position():
    source = "Material: Vidrio, Madera MDF; Color: Negro"
    target = "材质：玻璃、中密度纤维板；颜色：黑色"
    assert "PROTECTED_TOKEN_MISSING" not in rules(check(source, target, "details"))
    for invalid in (
        "材质：玻璃、木材；颜色：黑色",
        "材质：中密度纤维板、玻璃；颜色：黑色",
        "材质：玻璃、非中密度纤维板；颜色：黑色",
        "材质：玻璃、木材；颜色：中密度纤维板",
    ):
        assert "PROTECTED_TOKEN_MISSING" in rules(check(source, invalid, "details"))
    assert "PROTECTED_TOKEN_MISSING" in rules(check(
        source + "; Modelo: MDF", target + "；型号：普通", "details"))


def test_washing_machine_cleaner_does_not_accept_other_detergent_uses():
    fact = SemanticFact("PRODUCT_TYPE", "detergente", "洗洁精", source_field="desc_es")
    def missing(source,target):
        return "SEMANTIC_FACT_DROPPED" in rules(check(source,target,facts=(fact,)))
    assert not missing("Limpia con detergente para lavadoras", "使用洗衣机清洁剂清洁")
    assert missing("Limpia con detergente para lavadoras", "使用洗洁精清洁")
    assert missing("Limpia con detergente para lavadoras", "不含洗衣机清洁剂")
    assert missing("Detergente para lavadoras; incluye detergente para platos", "含洗衣机清洁剂")

    assert not missing("Detergente para lavadoras; incluye detergente para platos", "含洗衣机清洁剂和餐具洗涤剂")


@pytest.mark.parametrize("source,target", [
    ("como iluminación festiva", "用于节日照明"),
    ("con iluminación LED con efecto infinito", "带LED无限灯光效果"),
])
def test_qualified_lighting_keeps_unrelated_mentions_and_negation_checked(source, target):
    fact = SemanticFact("PRODUCT_TYPE", "iluminación", "照明灯", source_field="desc_es")

    def missing(original, translated):
        return "SEMANTIC_FACT_DROPPED" in rules(check(original, translated, facts=(fact,)))

    assert not missing(source, target)
    assert missing(source, "节日摆件")
    assert missing(source, target.replace("用于", "不具备").replace("带LED", "不带LED"))
    assert missing(source + "; incluye otra iluminación", target)


def test_facial_cloth_format_requires_every_typed_position():
    source = ("Apto para tipo de parte del cuerpo: Rostro; Sustancia: Paño; "
              "Tipo de dispensador: Paño")
    target = "适用身体部位：面部；产品形态：布片；分配器类型：布片"
    fact = SemanticFact("PRODUCT_TYPE", "paño", "清洁布", source_field="details_es")

    def missing(original, translated):
        return "SEMANTIC_FACT_DROPPED" in rules(check(original, translated, "details", facts=(fact,)))

    assert not missing(source, target)
    assert missing(source, target.replace("分配器类型：布片", "分配器类型：塑料"))
    assert missing(source, target.replace("分配器类型：布片", "材料：布片"))
    assert missing(source.replace("Rostro", "Manos"), target)
    assert missing(source + "; Accesorio: Paño", target + "；配件：刷子")


def test_mdf_material_fact_does_not_replace_a_separate_wood_claim():
    source = "Material: Vidrio, Madera MDF"
    target = "材质：玻璃、中密度纤维板（MDF）"
    fact = SemanticFact("MATERIAL", "madera", "木质", source_field="details_es")

    def missing(original, translated):
        return "SEMANTIC_FACT_DROPPED" in rules(check(original, translated, "details", facts=(fact,)))

    assert not missing(source, target)
    assert missing(source, "材质：中密度纤维板（MDF）、玻璃")
    assert missing(source + "; Material: Madera", target + "；材质：塑料")

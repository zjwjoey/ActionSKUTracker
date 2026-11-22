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

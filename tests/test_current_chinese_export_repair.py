"""CI_SAFE: export repair fidelity and precise QA boundaries."""
import pytest
import importlib.util
from pathlib import Path
from action_tracker.localization.contracts import SourceFacts
from action_tracker.localization.qa import audit_translation, _units, _numbers
from action_tracker.localization.policy import has_ordinary_spanish
from action_tracker.exporting.repair_rules import repair_content_with_context, repair_details_with_context
from action_tracker.exporting.dictionary_join import _repair_source_bound_content_facts

def test_standalone_export_audit_recognizes_rods_and_sets_without_hiding_count_changes():
    path = Path(__file__).resolve().parents[1] / 'scripts' / 'audit_chinese_export.py'
    spec = importlib.util.spec_from_file_location('chinese_export_audit', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module._numbers_with_chinese('两根叉子')['2'] == 1
    assert module._numbers_with_chinese('一套耳环')['1'] == 1
    assert module._numbers_with_chinese('一根叉子')['2'] == 0

@pytest.mark.parametrize('source,target', [
    ('Tiene 3 puertos USB. Con 3 puertos USB.', '带3个USB端口'),
    ('Con 2 puertos USB', '2个USB接口'),
    ('Cargador de 8 puertos', '8口充电器，4个USB-C接口和4个USB-A接口'),
])
def test_explicit_usb_port_count_does_not_get_duplicate_total(source,target):
    assert _repair_source_bound_content_facts(target,source) == target

def test_missing_usb_port_total_is_added_once_and_is_idempotent():
    source='Con 3 puertos USB. Tiene 3 puertos USB.'
    repaired=_repair_source_bound_content_facts('带USB接口',source)
    assert repaired.count('接口总数：3个') == 1
    assert _repair_source_bound_content_facts(repaired,source) == repaired

def codes(source, target, field='details'):
    facts = SourceFacts(sku='1001', **{ {'details':'details_es','spec':'spec_es','description':'desc_es'}[field]:source })
    return {f.rule_id for f in audit_translation(facts, {field:target}, (field,))}

@pytest.mark.parametrize('source,target', [('Ø 12 mm x 2 m','Ø12mm×2m'),('Ø 28 cm','Ø28cm')])
def test_diameter_marker_is_measurement_separator(source,target):
    assert _units(source) == _units(target)
    assert 'UNIT_DROPPED' not in codes(source,target,'spec')
    assert 'UNIT_DROPPED' in codes(source,'直径12','spec')

def test_charging_type_current_do_not_count_as_selected_speed():
    source='Cargador rápido / lento: Rápido; Tipo de cargador: Cargador universal; Corriente de carga: 5000 mA'
    target='充电器快/慢：快速；充电器类型：通用充电器；充电电流：5000 mA'
    assert not any(c.startswith('DETAIL_CHARGING_SPEED') for c in codes(source,target))
    assert 'DETAIL_CHARGING_SPEED_CHANGED' in codes(source,target.replace('快速','慢速'))
    assert 'DETAIL_CHARGING_SPEED_CHANGED' in codes(source,target.replace('快速','快速/慢速'))

def test_perfumed_and_perfume_free_are_two_distinct_boolean_fields():
    source='Perfumado: Sí; Sin perfume: No'
    assert not any(c.startswith('DETAIL_BOOLEAN') for c in codes(source,'是否香氛：是；不含香精：否'))
    assert 'DETAIL_BOOLEAN_POLARITY_CHANGED' in codes(source,'是否香氛：是；不含香精：是')
    assert 'DETAIL_BOOLEAN_FIELD_MISSING' in codes(source,'是否香氛：是')

def test_steel_model_does_not_look_like_latin_unit_plural():
    assert 'MALFORMED_TRANSLATED_UNIT' not in codes('24 puntas S2 de acero','24个S2钢批头','description')
    assert 'MALFORMED_TRANSLATED_UNIT' in codes('450 vatios','450瓦s','spec')

def test_source_anomaly_does_not_swallow_newline_details():
    source='Cantidad: 4 piezas; Sustancia: Válido, Loción; Tipo de dispensador: Tubo; Número del artículo: 1001'
    target='数量：4件\n产品形态：凝胶、乳液\n分配器类型：软管\n商品编号：1001'
    result=repair_details_with_context(target,source)
    assert '分配器类型：软管' in result and '商品编号：1001' in result
    assert '凝胶' not in result
    assert 'Válido, Loción' in result
    assert 'SOURCE_DETAILS_TYPED_VALUE_INVALID' in codes(source,result)

def test_source_faithful_brand_and_ip_remain_in_body():
    assert 'Disney' in repair_content_with_context('Disney Stitch主题','Stitch de Disney',excluded_tokens={'Disney'})
    assert 'Nutini' in repair_details_with_context('品牌：Nutini；商品编号：1001','Marca: Nutini; Número del artículo: 1001',excluded_tokens={'Nutini'})

def test_complete_allowed_identity_does_not_allow_ordinary_words():
    from action_tracker.localization.body_names import mask_source_bound_body_names
    assert not has_ordinary_spanish(mask_source_bound_body_names('Play-Doh','Play-Doh彩泥'))
    assert has_ordinary_spanish(mask_source_bound_body_names('Play-Doh','Play-Doh con material'))
    assert has_ordinary_spanish(mask_source_bound_body_names('Producto','Play-Doh彩泥'))

def test_same_dimension_and_bundle_do_not_get_appended_twice():
    assert _repair_source_bound_content_facts('尺寸：16.5 x 11厘米','16,5 x 11 cm',include_generic=True)=='尺寸：16.5 x 11厘米'
    assert _repair_source_bound_content_facts('三合一配方','3 en 1',include_generic=True)=='三合一配方'

def test_chinese_two_renderings_are_numeric_facts():
    assert _numbers('两台设备')['2']==1
    assert _numbers('双色')['2']==1
    assert _numbers('双片式面膜')['2']==1
    assert not _numbers('双向传输')

def test_facts_after_article_number_survive_repair():
    result=repair_details_with_context('商品编号：1001；颜色：红色','Número del artículo: 1001; Color: Rojo')
    assert '颜色：红色' in result

def test_review_registers_only_missing_source_versions_and_resumes(tmp_path):
    import sqlite3
    from scripts.run_current_localization_review import missing_review_source_records
    from action_tracker.localization.registry.repository import LocalizationRegistry
    from action_tracker.services.hashing import localization_source_hash
    path=tmp_path/'registry.sqlite3'; registry=LocalizationRegistry(path)
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO products(canonical_id,official_sku,status) VALUES('c1001','1001','CURRENT')")
    old={'sku':'1001','name_es':'Producto','cat1_es':'','cat2_es':'','spec_es':'','desc_es':'','details_es':'Cantidad: 2 piezas'}
    registry.ingest_records([old],source_run_id='initial',observed_at='2026-10-12')
    new={**old,'details_es':'Cantidad: 3 piezas'}; other={**old,'sku':'2002'}
    reviews=[{'sku':'1001','field':'details','source_hash':localization_source_hash(new)},
             {'sku':'1001','field':'name','source_hash':localization_source_hash(new)}]
    missing=missing_review_source_records(path,{'1001':new,'2002':other},reviews)
    assert missing==[new]
    registry.ingest_records(missing,source_run_id='official-detail-fix',observed_at='2026-10-12')
    assert missing_review_source_records(path,{'1001':new},reviews)==[]
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM translation_source_versions WHERE official_sku='2002'").fetchone()[0]==0
        assert db.execute('SELECT COUNT(*) FROM localization_patches').fetchone()[0]==0

def test_ordinary_uppercase_comparison_keys_and_translated_sport():
    from action_tracker.localization.qa import _is_ordinary_spanish_uppercase_token
    assert _is_ordinary_spanish_uppercase_token('PANTALLA','pantalla')
    assert not _is_ordinary_spanish_uppercase_token('USB-C','usb-c')
    source='Tipo de juego: AIR HOCKEY DE MESA'
    assert 'PROTECTED_TOKEN_CHANGED' not in codes(source,'游戏类型：桌上气垫曲棍球')
    assert {'PROTECTED_TOKEN_CHANGED','PROTECTED_TOKEN_MISSING'} & codes(source,'游戏类型：足球')

def test_reviewed_model_evidence_is_source_target_and_field_bound():
    from action_tracker.localization.review_evidence import reviewed_facts_for_field
    from action_tracker.services.hashing import localization_source_hash,localization_field_source_hash
    from action_tracker.localization.hashes import value_hash
    record={'sku':'1001','name_es':'Turbo Fire Dartblaster XSHOT','cat1_es':'','cat2_es':'','spec_es':'','desc_es':'','details_es':'','name_zh':'Turbo Fire软弹枪'}
    fact={'semantic_type':'MODEL','source_text':'Turbo Fire','value':'Turbo Fire','source_field':'name_es','evidence':'fixture:official'}
    record['zh_reviewed_semantic_evidence']={'name':{'source_hash':localization_source_hash(record),'target_hash':value_hash(record['name_zh']),'facts':[fact]}}
    record['zh_field_provenance']={'name':{'value':record['name_zh'],'review_status':'APPROVED','freshness_status':'CURRENT','source_hash':localization_field_source_hash(record,'name')}}
    assert len(reviewed_facts_for_field(record,'name'))==1
    for key,value in [('name_zh','其他商品'),('name_es','Otro modelo')]:
        changed={**record,key:value};assert not reviewed_facts_for_field(changed,'name')
    record['zh_field_provenance']['name']['freshness_status']='STALE'
    assert not reviewed_facts_for_field(record,'name')

def test_platform_plural_and_compact_rate_remain_own_field_bound():
    from action_tracker.localization.qa import guard_translation
    assert guard_translation(SourceFacts(sku='1001',desc_es='5 Gbps; Compatible con Chromebooks'),{'description':'5Gbps，兼容Chromebook'},('description',))['status']=='PASS'
    assert guard_translation(SourceFacts(sku='1001',desc_es='Producto'),{'description':'5Gbps，兼容Chromebook'},('description',))['status']=='FAIL'

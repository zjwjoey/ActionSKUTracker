"""CI_SAFE: own-field fidelity and bounded deterministic repairs."""
import hashlib,json
import pytest
from action_tracker.localization.fidelity import *
from action_tracker.localization.contracts import SourceFacts,SemanticFact
from action_tracker.localization.semantic import parse_semantic_facts
from action_tracker.localization.qa import guard_translation
from action_tracker.exporting.dictionary_join import _repair_export_details
from action_tracker.exporting.repair_rules import repair_content_with_context,repair_spec,repair_title_with_context
from action_tracker.services.hashing import localization_field_source_hash
from action_tracker.localization.source_literal import VERSION

def test_body_brand_facts_are_local_not_name_fallback():
    s=SourceFacts(sku='1001',name_es='Disney taza',desc_es='Una taza',details_es='Tipo: Disney taza')
    facts=parse_semantic_facts(s,known_brands={'Disney'})
    assert not any(f.semantic_type=='BRAND' and f.source_field=='desc_es' for f in facts)
    assert any(f.semantic_type=='BRAND' and f.source_field=='details_es' for f in facts)
    assert restore_body_identities('Una taza','杯子',{'Disney'})=='杯子'
    assert restore_body_identities('Una taza Disney','杯子',{'Disney'})=='杯子；原文品牌/系列：Disney'
    assert restore_body_identities('Una taza Disney','迪士尼杯子',{'Disney'})=='迪士尼杯子'

def test_brand_review_does_not_treat_ordinary_words_as_brands():
    assert source_identities('una sola vez; efecto perla; marcadores jumbo; fabulosa loción; juego de make-over',{'Sola','Perla','Jumbo','Fabulosa','Make-Over'})==()
    assert source_identities('braguitas de Second Skin',{'Second Skin'})==('Second Skin',)

def test_brand_omission_and_wrong_identity_block_qa():
    s=SourceFacts(sku='1001',desc_es='Chocolate Milka')
    f=parse_semantic_facts(s,known_brands={'Milka'})
    assert guard_translation(s,{'description':'巧克力'},('description',),semantic_facts=f)['status']=='FAIL'
    assert guard_translation(s,{'description':'美极巧克力'},('description',),semantic_facts=f)['status']=='FAIL'
    assert guard_translation(s,{'description':'Milka巧克力'},('description',),semantic_facts=f)['status']=='PASS'
    assert guard_translation(s,{'description':'妙卡巧克力'},('description',),semantic_facts=f)['status']=='PASS'

def test_chinese_brand_aliases_are_removed_only_for_own_name():
    assert strip_name_identity_aliases('Blush Drops Bonjolie','邦乔莉腮红滴剂',{'bonjolie'})=='腮红滴剂'
    assert strip_name_identity_aliases('Vaso','迪士尼杯子',{'Disney'})=='迪士尼杯子'
    s=SourceFacts(sku='1001',name_es='Vaso Disney')
    assert guard_translation(s,{'name':'迪士尼杯子'},('name',),semantic_facts=parse_semantic_facts(s,known_brands={'Disney'}))['status']=='FAIL'

def test_complete_ip_alias_does_not_append_partial_uppercase_token():
    assert repair_content_with_context('可选汪汪队图案','PAW Patrol',excluded_tokens={'Paw Patrol'})=='可选汪汪队图案'
    assert repair_content_with_context('可选汪汪队图案；PAW','PAW Patrol',excluded_tokens={'Paw Patrol'})=='可选汪汪队图案'

@pytest.mark.parametrize('source,target',[
    ('Color: Azul, Púrpura, Rojo, Blanco, Amarillo','颜色：蓝色、红色、白色'),
    ('Color: Gris topo','颜色：驼色'),
    ('Material: Poliéster, Dolomita','材质：聚酯纤维、方解石'),
    ('Material: Polirresina, Plástico','材质：塑料'),
])
def test_attribute_missing_is_detected_and_restored_locally(source,target):
    assert detail_attribute_issues(source,target)
    fixed=repair_detail_attributes(source,target)
    assert not detail_attribute_issues(source,fixed)
    assert repair_detail_attributes(source,fixed)==fixed

def test_compound_materials_and_roles_are_not_false_conflicts():
    assert not detail_attribute_issues('Material: Madera MDF, Tereftalato de polietileno','材质：中密度纤维板、PET')
    assert not detail_attribute_issues('Material: base: Plástico; Material: Poliéster','基材：塑料；材质：涤纶')
    assert not detail_attribute_issues('Color: Gris topo','颜色：驼灰色')
    assert repair_detail_attributes('Material: Desconocido, Plástico','材质：塑料')=='材质：塑料'

@pytest.mark.parametrize('name,source,target,expected',[
    ('Chicles Mentos','90 unidades','90件','90粒'),
    ('Chicles Mentos','Paquete de 5','5件','5包'),
    ('Pilas AA','4 unidades','4件','4节'),
    ('Bastoncillos','400 unidades','400件','400支'),
    ('Braguitas','2 uds.','2件','2条'),
    ('Servilletas','20 unidades','20件','20张'),
    ('Pilas AA','4 unidades','9件','9件'),
])
def test_classifier_uses_own_source_count(name,source,target,expected):
    assert specification_classifier(source,name,target)==expected
    assert specification_classifier(source,name,expected)==expected

def test_laundry_powder_does_not_require_dishwashing_liquid():
    s=SourceFacts(sku='1001',desc_es='detergente en polvo para tu colada')
    facts=parse_semantic_facts(s)
    assert any(f.value=='洗衣粉' for f in facts)
    assert not any(f.value=='洗洁精' for f in facts)
    assert guard_translation(s,{'description':'洗衣粉'},('description',),semantic_facts=facts)['status']=='PASS'
    assert guard_translation(s,{'description':'洗洁精'},('description',),semantic_facts=facts)['status']=='FAIL'

def test_literal_broken_key_keeps_boolean_and_needs_bound_authorization(tmp_path):
    s=SourceFacts(sku='1001',details_es='Cantidad: 4 piezas; Incluye oído: No; Número del artículo: 1001')
    target=_repair_export_details('数量：4件；是否带耳：否；商品编号：1001',s.details_es)
    marker='来源异常：官网字段Incluye oído=No'
    assert marker in target and '是否来源异常' not in target
    assert detail_attribute_issues(s.details_es,target)==[]
    h=localization_field_source_hash(s.as_record(),'details')
    doc={'contract':VERSION,'scope':'TODAY_ON_SALE_ONLY','authorization_ref':'test:owner','authorization_text':'preserve literal value','fields':[dict(sku='1001',field='details',field_source_hash=h,source_value='No',marker=marker)]}
    file=tmp_path/'owner.json';file.write_text(json.dumps(doc),encoding='utf-8')
    fact=SemanticFact('DETAIL_KEY','No','No',marker,'details_es',json.dumps({'authorization_file':str(file),'sha256':hashlib.sha256(file.read_bytes()).hexdigest()}),fact_source_hash=h,coverage_status=VERSION)
    assert guard_translation(s,{'details':target},('details',),semantic_facts=(fact,))['status']=='PASS'
    assert guard_translation(s,{'details':target.replace('=No','=Sí')},('details',),semantic_facts=(fact,))['status']=='FAIL'

def test_pipe_parser_preserves_value_pipes_and_duplicate_keys():
    from action_tracker.localization.normalization.structured_details import parse_structured_details
    items=parse_structured_details('Material: A | B | Color: Blanco | Color: Blanco')
    assert [(i.key,i.value) for i in items]==[('Material','A | B'),('Color','Blanco'),('Color','Blanco')]

@pytest.mark.parametrize('field,source,target',[
 ('description','Con goma\nFSC - madera y papel sostenibles\nHaz tus dibujos con lápices HB.', '带橡皮；FSC可持续木材和纸张；用HB铅笔画画。'),
 ('description','Incluye tres longitudes diferentes. Incluye tres longitudes.', '包含3种不同长度；包含3种长度。'),
 ('description','Incluye un accesorio para el secador y dos accesorios onduladores. Con dos niveles y dos metros.', '包含一个吹风机附件和两个卷发附件；两档设置、两米线。'),
 ('spec','Tallas S - XL | 2 uds.', '尺码S-XL｜2件'),
 ('details','Material: Madera MDF, Papel, Hierro', '材质：MDF中密度纤维板、纸、铁'),
])
def test_reviewed_context_contracts_accept_equivalent_own_field(field,source,target):
    keys={'description':'desc_es','spec':'spec_es','details':'details_es'}
    s=SourceFacts(sku='1001',**{keys[field]:source})
    facts=parse_semantic_facts(s)
    result=guard_translation(s,{field:target},(field,),semantic_facts=facts)
    assert result['status']=='PASS',result


def test_spelled_count_and_size_range_guards_still_reject_changes():
    for field,source,target in [('description','Incluye tres longitudes','包含4种长度'),('spec','Tallas S - XL','尺码S-XXL')]:
        s=SourceFacts(sku='1001',**{('desc_es' if field=='description' else 'spec_es'):source})
        assert guard_translation(s,{field:target},(field,),semantic_facts=parse_semantic_facts(s))['status']=='FAIL'

@pytest.mark.parametrize('source,target',[
 ('Material: Papel; Sustancia: Papel','材质：纸；材质：纸'),
 ('Material: Acero inoxidable; Material: del asa Silicona','材质：不锈钢；把手材质：硅胶'),
 ('Material: base Metal','基材：金属'),
 ('Material: Plástico; Material: de la lente: Plástico; Material: estructura: Plástico','材质：塑料；材质：镜片：塑料；材质：镜架：塑料'),
])
def test_distinct_material_roles_are_preserved_without_count_false_positive(source,target):
    assert not detail_attribute_issues(source,target)


def test_qualified_material_loss_still_blocks():
    assert detail_attribute_issues('Material: del asa Silicona','把手材质：玻璃')
    assert detail_attribute_issues('Material: de la cubierta Vidrio','材质：玻璃')


def test_ordinary_display_allowlist_is_not_brand_evidence():
    from action_tracker.exporting.repair_report import audit_repaired_rows
    record={'sku':'1001','desc_es':'Con aroma'}
    row={'编号':'1001','描述':'有香气'}
    report=audit_repaired_rows([record],[row],allowed_tokens={'aroma'})
    assert not any(f['code']=='SEMANTIC_FACT_DROPPED' for f in report['findings'])


def test_double_layer_is_a_numeric_fact():
    from action_tracker.localization.qa import _numbers
    assert _numbers('双层结构')['2']==1


def test_newer_approved_primary_supersedes_old_override_only_with_valid_binding():
    from action_tracker.exporting.repair_overrides import apply_override,field_source_hash
    record={'sku':'1001','desc_es':'Una toalla Capetown','desc_zh':'Capetown毛巾'}
    h=field_source_hash(record,'desc_zh')
    row={'source_hash':h,'replacement':'毛巾','approved_at':'2026-10-06T00:00:00+00:00'}
    record['zh_field_provenance']={'description':{'value':'Capetown毛巾','review_status':'APPROVED','freshness_status':'CURRENT','source_hash':h,'approved_at':'2026-10-12T00:00:00+00:00','approved_by':'service:current-localization-review'}}
    assert apply_override(sku='1001',field='desc_zh',value='Capetown毛巾',record=record,overrides={('1001','desc_zh'):row})[2]=='SUPERSEDED_BY_PRIMARY'
    record['zh_field_provenance']['description']['source_hash']='wrong'
    assert apply_override(sku='1001',field='desc_zh',value='Capetown毛巾',record=record,overrides={('1001','desc_zh'):row})[2]=='APPLIED'


def test_title_export_actually_applies_chinese_brand_alias_policy():
    assert repair_title_with_context('贝蒂牌热水袋',"Bolsa de agua caliente Betty's",excluded_tokens={"Betty's"})=='热水袋'
    assert repair_title_with_context('高露洁牙刷','Cepillos de dientes Colgate',excluded_tokens={'Colgate'})=='牙刷'
    assert repair_title_with_context('Oreo巧克力｜MMMAX','Milka MMMAX Oreo',excluded_tokens={'Milka','Oreo'})=='巧克力｜MMMAX'


def test_brand_omission_keeps_explicit_cola_product_and_removes_dangling_possessive():
    assert repair_title_with_context('可口可乐零','Coca-Cola Zero',excluded_tokens={'Coca-Cola'})=='零度可乐'
    assert repair_title_with_context('贝蒂的保暖毛绒玩具',"Peluche cálido Betty's",excluded_tokens={"Betty's"})=='保暖毛绒玩具'


def test_flattened_official_anomaly_key_remains_literal_and_lossless():
    from action_tracker.localization.normalization.structured_details import parse_structured_details
    raw='Apto para el lavavajillas Sí; Incluye oído Sí; Tipo de taza / vaso Taza (no desechable)'
    pairs=parse_structured_details(raw)
    assert [(p.key,p.value) for p in pairs]==[('Apto para el lavavajillas','Sí'),('Incluye oído','Sí'),('Tipo de taza / vaso','Taza (no desechable)')]
    assert pairs[1].source_text=='Incluye oído Sí'
    assert detail_attribute_issues(raw,'可用洗碗机清洗：是；带把手：是；杯子类型：杯子（非一次性）')

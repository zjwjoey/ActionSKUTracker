"""CI_SAFE: finite Owner evidence does not waive ordinary fact QA."""
import hashlib,json
import pytest
from dataclasses import replace
from action_tracker.localization.contracts import SourceFacts,SemanticFact
from action_tracker.localization.qa import guard_translation
from action_tracker.localization.semantic import parse_semantic_facts
from action_tracker.services.hashing import localization_field_source_hash
from action_tracker.localization.source_literal import VERSION

def fixture(tmp_path,field='details'):
    source=SourceFacts(sku='1001',details_es='Cantidad: 4 piezas; Sustancia: Válido; Número del artículo: 1001',desc_es='Suaves e inoloras')
    value='Válido' if field=='details' else 'inoloras'
    marker='来源异常：官网字段Sustancia=Válido' if field=='details' else '来源异常：官网原词inoloras'
    field_hash=localization_field_source_hash(source.as_record(),field)
    doc={'contract':VERSION,'scope':'TODAY_ON_SALE_ONLY','authorization_ref':'test:explicit-owner','authorization_text':'preserve official literals without guessing','fields':[{'sku':'1001','field':field,'field_source_hash':field_hash,'source_value':value,'marker':marker}]}
    file=tmp_path/'owner.json';file.write_text(json.dumps(doc,ensure_ascii=False),encoding='utf-8')
    fact=SemanticFact('DETAIL_KEY' if field=='details' else 'DESCRIPTION_FACT',value,value,marker,
        'details_es' if field=='details' else 'desc_es',json.dumps({'authorization_file':str(file),'sha256':hashlib.sha256(file.read_bytes()).hexdigest()}),fact_source_hash=field_hash,coverage_status=VERSION)
    target='数量：4件；'+marker+'；商品编号：1001' if field=='details' else '柔软；'+marker
    return source,target,fact,file

def check(source,target,fact=None,field='details'):
    return guard_translation(source,{field:target},(field,),semantic_facts=(fact,) if fact else ())

def test_literal_needs_finite_owner_artifact_and_keeps_warning(tmp_path):
    s,t,f,p=fixture(tmp_path)
    assert check(s,t)['status']=='FAIL'
    good=check(s,t,f);assert good['status']=='PASS' and len(good['declared_source_anomalies'])==1
    assert s.details_es=='Cantidad: 4 piezas; Sustancia: Válido; Número del artículo: 1001'

@pytest.mark.parametrize('mutation',['hash','sku','field','missing_file','changed_file','guess','number','order','duplicate'])
def test_source_target_scope_and_all_other_guards_fail_closed(tmp_path,mutation):
    s,t,f,p=fixture(tmp_path)
    if mutation=='hash':f=replace(f,fact_source_hash='wrong')
    if mutation=='sku':s=replace(s,sku='2002')
    if mutation=='field':f=replace(f,source_field='desc_es')
    if mutation=='missing_file':p.unlink()
    if mutation=='changed_file':p.write_text('{}')
    if mutation=='guess':t+='；产品形态：凝胶'
    if mutation=='number':t=t.replace('4件','9件')
    if mutation=='order':t='来源异常：官网字段Sustancia=Válido；数量：4件；商品编号：1001'
    if mutation=='duplicate':t+='；来源异常：官网字段Sustancia=Válido'
    assert check(s,t,f)['status']=='FAIL'

def test_authorization_cannot_mask_ordinary_prose(tmp_path):
    s,t,f,p=fixture(tmp_path,'description')
    assert check(s,t,f,'description')['status']=='PASS'
    assert check(s,t+' con material',f,'description')['status']=='FAIL'
    assert check(replace(s,desc_es='Otro texto'),t,f,'description')['status']=='FAIL'

def test_wc_detergent_is_not_dishwashing_liquid_and_no_cross_field_waiver():
    s=SourceFacts(sku='1001',details_es='Tipo de detergente de limpieza de WC: Producto protector')
    facts=parse_semantic_facts(s)
    assert any(f.value=='洁厕剂' for f in facts)
    assert not any(f.source_text=='detergente' and f.value=='洗洁精' for f in facts)
    other=SourceFacts(sku='1001',name_es='Detergente de limpieza de WC',desc_es='detergente')
    assert any(f.source_field=='desc_es' and f.value=='洗洁精' for f in parse_semantic_facts(other))

def test_own_source_byte_unit_alias_does_not_borrow_another_field():
    s=SourceFacts(sku='1001',desc_es='5 gigabytes por segundo')
    assert guard_translation(s,{'description':'5 GB/秒'},('description',))['status']=='PASS'
    s=SourceFacts(sku='1001',desc_es='5 Gbps',details_es='5 gigabytes por segundo')
    assert guard_translation(s,{'description':'5 GB/秒'},('description',))['status']=='FAIL'

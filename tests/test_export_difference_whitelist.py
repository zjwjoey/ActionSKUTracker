"""CI_SAFE: exact field bindings, synthetic review metadata; no production IO."""
from datetime import datetime, timezone, timedelta
import json

import pytest

from action_tracker.exporting.difference_whitelist import (
    CONTRACT, DifferenceWhitelist, DifferenceWhitelistError, value_hash, whitelist_from_config,
)
from action_tracker.exporting.repair_overrides import field_source_hash, prepare_reviewed_candidates
from action_tracker.exporting.repair_report import audit_repaired_rows


def record():
    return {'sku':'2506346','name_es':'Plástico protector TCX','name_zh':'防护塑料膜',
            'spec_es':'25 m x 55 cm','spec_zh':'25m×55cm'}


def entry(r=None, **updates):
    r=r or record()
    now=datetime.now(timezone.utc)
    item={'id':'fixture-review','sku':r['sku'],'field':'name_zh','action':'NO_BRAND_TITLE',
        'status':'CONFIRMED','classification':'PROGRAM_FALSE_POSITIVE',
        'source_hash':field_source_hash(r,'name_zh'),'current_hash':value_hash(r['name_zh']),
        'reviewed_hash':value_hash(r['name_zh']),'reviewed_by':'CODEX:synthetic-test',
        'reviewed_at':(now-timedelta(minutes=1)).isoformat(),'expires_at':(now+timedelta(days=30)).isoformat(),
        'brand_span':'TCX','brand_tokens':['TCX'],'reason':'fixture: no-brand title policy',
        'evidence':['https://example.test/synthetic-review-not-production']}
    return {**item,**updates}


def audit(r, registry):
    return audit_repaired_rows([r],[{'编号':r['sku'],'标题':r['name_zh'],
        '规格':r.get('spec_zh'),'描述':r.get('desc_zh'),'产品详情':r.get('details_zh')}],
        difference_whitelist=registry)


def test_exact_confirmed_title_policy_only_suppresses_reviewed_brand():
    r=record(); white=DifferenceWhitelist([entry(r)])
    assert any(f['code']=='PROTECTED_TOKEN_MISSING' for f in audit(r,None)['findings'])
    out=audit(r,white)
    assert not any(f['field']=='name_zh' for f in out['findings'])
    assert out['reviewed_title_brand_exceptions'][0]['entry_id']=='fixture-review'
    assert r['name_zh']=='防护塑料膜'


@pytest.mark.parametrize('field,value', [('status','PENDING'),('source_hash','0'*64),
    ('current_hash','0'*64),('reviewed_hash','0'*64),('reason',''),('evidence',[]),
    ('reviewed_by',''),('expires_at','2020-01-01T00:00:00+00:00'),
    ('expires_at','2099-01-01T00:00:00+00:00'),('reviewed_at','2099-01-01T00:00:00+00:00'),
    ('classification','SOURCE_PROBLEM'),('brand_span','OtherBrand'),('brand_tokens',['OTHER'])])
def test_missing_stale_pending_or_changed_review_cannot_waive_qa(field,value):
    r=record(); white=DifferenceWhitelist([entry(r,**{field:value})])
    assert any(f['field']=='name_zh' for f in audit(r,white)['findings'])


@pytest.mark.parametrize('mutation', ['source','target','sku','model','numeric','body','source_quality'])
def test_future_changes_other_fields_and_hard_facts_remain_blocked(mutation):
    r=record(); white=DifferenceWhitelist([entry(r)])
    if mutation=='source':r['name_es']='Plástico TCX nuevo'
    elif mutation=='target':r['name_zh']='塑料膜（修改）'
    elif mutation=='sku':r['sku']='9999999'
    elif mutation=='model':r.update(spec_es='USB-C A4 100W',spec_zh='充电器')
    elif mutation=='numeric':r.update(spec_es='5 unidades',spec_zh='2件')
    elif mutation=='body':r.update(desc_es='Cable USB-C',desc_zh='连接线')
    else:r.update(details_es='Sustancia: Válido',details_zh='物态：液态')
    assert audit(r,white)['findings']


@pytest.mark.parametrize('token',['USB','AA','AAA','LED','FSC','A4','TCX2'])
def test_model_and_technical_identity_cannot_be_declared_brand(token):
    r={**record(),'name_es':'Producto '+token}
    white=DifferenceWhitelist([entry(r,brand_span=token,brand_tokens=[token])])
    assert white.title_brand_match(r,r['name_zh'],'PROTECTED_TOKEN_MISSING',{'token_type':'TECH','value':token}) is None


def test_complete_brand_span_not_individual_word_is_required():
    r={**record(),'name_es':'Batidora TMC Home'}
    white=DifferenceWhitelist([entry(r,brand_span='TMC Home',brand_tokens=['TMC'])])
    assert white.title_brand_match(r,r['name_zh'],'PROTECTED_TOKEN_MISSING',{'token_type':'TECH','value':'TMC'})
    r['name_es']='Batidora TMC'
    assert white.title_brand_match(r,r['name_zh'],'PROTECTED_TOKEN_MISSING',{'token_type':'TECH','value':'TMC'}) is None


def test_valid_but_wrong_cat1_is_blocked_by_official_mapping_not_whitelisted():
    from action_tracker.dictionary import normalize_category_key
    r={**record(),'cat1_es':'Jardín'}
    result=audit_repaired_rows([r],[{'编号':r['sku'],'标题':r['name_zh'],'规格':r['spec_zh'],'分类1':'DIY五金'}],
        difference_whitelist=DifferenceWhitelist([entry(r)]),
        category_mapping={normalize_category_key('Jardín'):'园艺户外'})
    assert any(f['code']=='CATEGORY_SOURCE_MAPPING_MISMATCH' for f in result['findings'])
    assert not result['release_ready']


def test_export_spec_cannot_import_numbers_and_colors_from_other_fields():
    r={**record(),'spec_es':'76x191x25 cm','spec_zh':'76×191×25cm｜蓝色｜136kg',
       'details_es':'Color: Azul; Peso máximo: 136 kg'}
    out=audit(r,DifferenceWhitelist([entry(r)]))
    codes={f['code'] for f in out['findings'] if f['field']=='spec_zh'}
    assert {'NUMERIC_ADDED','FIELD_SOURCE_FACT_ADDED'} <= codes
    r['spec_zh']='76×191×25cm'
    assert not any(f['field']=='spec_zh' for f in audit(r,DifferenceWhitelist([entry(r)]))['findings'])


def test_loader_defaults_off_and_requires_explicit_owned_registry(tmp_path):
    assert not whitelist_from_config({}).entries
    with pytest.raises(DifferenceWhitelistError):whitelist_from_config({'export_difference_whitelist':{'enabled':'true'}})
    with pytest.raises(DifferenceWhitelistError):whitelist_from_config({'export_difference_whitelist':{'enabled':True}})
    path=tmp_path/'registry.json'; path.write_text(json.dumps({'contract':CONTRACT,'entries':[entry()]}),'utf8')
    assert whitelist_from_config({'project_root':tmp_path,'export_difference_whitelist':{'enabled':True,'path':path}}).tracked_skus=={'2506346'}
    with pytest.raises(DifferenceWhitelistError):DifferenceWhitelist([entry(),entry()])


def test_acknowledged_comparison_does_not_create_approval_or_blanket_sku_exception():
    r=record(); columns={'标题':'name_zh','分类1':'cat1_zh','分类2':'cat2_zh','规格':'spec_zh','描述':'desc_zh','产品详情':'details_zh'}
    current={'编号':r['sku'],**{col:r.get(field,'') for col,field in columns.items()}}
    reviewed={**current,'规格':'25m x 55cm'}
    item=entry(r,field='spec_zh',action='ACK_DIFFERENCE',classification='EQUIVALENT_FORMAT',
        source_hash=field_source_hash(r,'spec_zh'),current_hash=value_hash(r['spec_zh']),reviewed_hash=value_hash(reviewed['规格']))
    white=DifferenceWhitelist([item])
    result=prepare_reviewed_candidates([r],[current],[reviewed],[r],evidence_ref='fixture',difference_whitelist=white)
    assert not result['candidates'] and len(result['acknowledged_differences'])==1
    assert result['acknowledged_differences'][0]['does_not_waive_qa_or_approval']
    reviewed['标题']='另一名称'
    result=prepare_reviewed_candidates([r],[current],[reviewed],[r],evidence_ref='fixture',difference_whitelist=white)
    assert len(result['candidates'])==1 and result['candidates'][0]['status']=='PENDING'
    assert not result['candidates'][0]['approved_by']


@pytest.mark.parametrize('es,bad,good', [
    ('Efecto deseado: Dentadura blanca','预期效果：白假牙','预期效果：牙齿美白'),
    ('Tipo de asa: Agarre en frío','提手类型：冷敷','把手类型：凉触把手'),
    ('Sabor: Fresa, Naranja, Limón','口味：草莓、橙色、柠檬','口味：草莓、橙子、柠檬'),
    ('Apto para tipo de animal (de compañía): Perro, Gato, Ave','适用宠物类型：狗、猫、禽肉','适用宠物类型：狗、猫、禽类'),
])
def test_real_contextual_mistranslations_are_flagged_without_rewriting(es,bad,good):
    from action_tracker.localization.qa import _detail_contextual_translation_findings
    assert _detail_contextual_translation_findings(es,bad)
    assert not _detail_contextual_translation_findings(es,good)
    assert not _detail_contextual_translation_findings('Color: Naranja','颜色：橙色')
    assert not _detail_contextual_translation_findings('Efecto deseado: Dentadura blanca','适用牙列：假牙')
    assert not _detail_contextual_translation_findings('Tipo de asa: Otro','提手类型：冷敷')


@pytest.mark.parametrize('es,zh', [('32 GB','32GB'),('128 GB','128GB'),('SPF 20 | 100 ml','SPF20｜100ml'),('1600 PPP | 1,25 m','1600DPI｜1.25米'),('Full HD','Full HD'),('Epson EcoTank 101/102/103/104 | 4 unidades','兼容Epson EcoTank 101/102/103/104，4件装')])
def test_real_spec_token_equivalences_are_source_bound(es,zh):
    r={**record(),'spec_es':es,'spec_zh':zh}
    assert not any(f['field']=='spec_zh' for f in audit(r,None)['findings'])


@pytest.mark.parametrize('token,source,target', [
    ('GB','32 GB','32Gb'),('GB','32 GB','32GBX'),('GB','32 GB','64GB'),
    ('SPF','SPF 20','SPF200'),('PPP','1600 PPP','800DPI'),('PPP','Modelo PPP','DPI')])
def test_numeric_spacing_equivalence_does_not_allow_changed_units_or_models(token,source,target):
    from action_tracker.exporting.repair_report import _token_equivalence_allows
    assert not _token_equivalence_allows('PROTECTED_TOKEN_MISSING',{'value':token,'token_type':'TECH'},source,target)


def test_literal_phrases_and_translated_3d_do_not_leak_to_other_source_fields():
    from action_tracker.localization.qa import _source_bound_display_tokens, _is_allowed_translated_tech_token
    assert not _source_bound_display_tokens('Pantalla','Full HD')
    assert 'full' not in {s.casefold() for s in _source_bound_display_tokens('Full','Full')}
    assert _is_allowed_translated_tech_token('Pegatinas 3D','3D','立体贴纸')
    for source,target in [('Modelo 3D','立体'),('Pegatinas X3D','立体'),('Pegatinas','立体'),('Pegatinas 3D','非立体贴纸')]:
        assert not _is_allowed_translated_tech_token(source,'3D',target)


def test_puff_furniture_value_is_translatable_but_a_model_is_not():
    from action_tracker.localization.qa import _is_allowed_translated_tech_token
    assert _is_allowed_translated_tech_token('Tipo de puf / silla de descanso: PUFF','PUFF','软墩/休闲椅类型：软墩')
    assert _is_allowed_translated_tech_token('Tipo de puf / silla de descanso: PUFF','PUFF','坐垫/休闲椅类型：蒲团')
    assert _is_allowed_translated_tech_token('Tipo: PUFF','PUFF','类型：蒲团')
    assert not _is_allowed_translated_tech_token('Modelo: PUFF','PUFF','软墩')


def test_whitelist_cli_is_read_only_without_loading_runtime(tmp_path, monkeypatch, capsys):
    from action_tracker import cli, config
    def forbidden(*args,**kwargs):raise AssertionError('Must not initialize production paths')
    monkeypatch.setattr(config,'load_settings',forbidden)
    path=tmp_path/'registry.json';path.write_text(json.dumps({'contract':CONTRACT,'entries':[entry()]}),'utf8')
    assert cli.main(['export-difference-whitelist','--registry',str(path),'--sku','2506346'])==0
    result=json.loads(capsys.readouterr().out)
    assert result['read_only'] and not result['whole_sku_bypass']
    assert len(result['entries'])==1

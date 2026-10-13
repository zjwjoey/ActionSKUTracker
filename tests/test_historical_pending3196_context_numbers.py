"""CI_SAFE: real sources and isolated negative cases; no model/DB writes."""
import json
from pathlib import Path
import pytest
from action_tracker.localization.contracts import SourceFacts
from action_tracker.localization.engine import LocalizationEngine
from action_tracker.localization.qa import _numbers, guard_translation
from action_tracker.localization.semantic import parse_semantic_facts

ROWS={r['sku']:r for r in json.loads((Path(__file__).parent/'fixtures' /
       'historical_pending3196_context_numbers_20261011.json').read_text(encoding='utf-8'))}

def check(row, target):
    source=SourceFacts.from_record({'sku':row['sku'],**row['context']})
    facts=LocalizationEngine().resolve(source.as_record()).semantic_facts
    return guard_translation(source, {'description':target}, ('description',), semantic_facts=facts)

def test_real_stationery_item_is_an_eraser_without_invented_material():
    row=ROWS['3217243'];assert row['source']==row['evidence']['text']
    assert len(row['evidence']['file_hash'])==64
    source=SourceFacts.from_record({'sku':row['sku'],**row['context']})
    for facts in (parse_semantic_facts(source), LocalizationEngine().generation_semantic_facts(source)):
        assert any(f.source_text=='goma' and f.canonical_value=='橡皮擦' for f in facts)
        assert not any(f.source_text=='goma' and f.semantic_type=='MATERIAL' for f in facts)
    assert 'SEMANTIC_FACT_DROPPED' not in [f['rule_id'] for f in check(row,row['candidate'])['findings']]

@pytest.mark.parametrize('bad', ['橡胶','橡皮筋','不含橡皮',''])
def test_eraser_cannot_be_replaced_with_rubber_bands_or_omitted(bad):
    row=ROWS['3217243']
    target=row['candidate'].replace('橡皮',bad)
    assert any(f['rule_id']=='SEMANTIC_FACT_DROPPED' for f in check(row,target)['findings'])

def test_separate_explicit_rubber_material_is_still_required():
    source=SourceFacts.from_record({'sku':'isolated','desc_es':'Contiene lápices, goma, regla. Material: Goma'})
    facts=parse_semantic_facts(source)
    assert any(f.source_text=='goma' and f.canonical_value=='橡皮擦' for f in facts)
    assert any(f.source_text.lower()=='goma' and f.canonical_value=='橡胶' for f in facts)
    result=guard_translation(source,{'description':'含铅笔、橡皮、尺子。'},('description',),semantic_facts=facts)
    assert result['status']=='FAIL'

@pytest.mark.parametrize('own', ['Material: Goma','Lápices de goma y regla','Goma'])
def test_own_material_or_ambiguous_word_does_not_borrow_stationery_context(own):
    source=SourceFacts.from_record({'sku':'isolated','name_es':'Estuche con lápices, goma, regla', 'desc_es':own})
    facts=parse_semantic_facts(source)
    assert not any(f.source_field=='desc_es' and f.canonical_value=='橡皮擦' for f in facts)
    assert any(f.source_field=='desc_es' and f.canonical_value=='橡胶' for f in facts)

def test_real_cable_durability_count_keeps_exact_numeric_value():
    row=ROWS['3009538'];assert row['source']==row['evidence']['text']
    assert _numbers('5万次')==_numbers('50 000 veces')
    result=check(row,row['candidate'])
    assert result['status']=='PASS',result['findings']

@pytest.mark.parametrize('bad', ['4万次','50万次','5千次','5次'])
def test_changed_durability_counts_fail(bad):
    row=ROWS['3009538'];result=check(row,row['candidate'].replace('5万次',bad))
    assert result['status']=='FAIL'
    assert any(f['rule_id']=='NUMERIC_DROPPED' for f in result['findings'])

@pytest.mark.parametrize('text,expected', [
    ('0.5万次',{'5000':1}), ('1.5万张',{'15000':1}),
    ('Model5万次',{'5':1}), ('5万X',{'5':1}), ('5万',{'5':1}),
])
def test_count_scaling_is_exact_and_does_not_rewrite_identifiers(text,expected):
    assert dict(_numbers(text))==expected

def test_count_in_other_field_does_not_authorize_added_scaled_count():
    source=SourceFacts.from_record({'sku':'isolated','desc_es':'Cable USB-C', 'spec_es':'50 000 veces'})
    result=guard_translation(source,{'description':'USB-C电缆，可弯曲5万次'},('description',))
    assert any(f['rule_id']=='NUMERIC_ADDED' for f in result['findings'])

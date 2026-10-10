"""Unresolved physical state never receives a guessed gold translation."""
import json
from pathlib import Path
import pytest
from action_tracker.localization.contracts import SourceFacts
from action_tracker.localization.qa import guard_translation

ROWS=json.loads((Path(__file__).parent/'fixtures/historical_details_invalid_source_state_20261010.json').read_text('utf8'))

def check(source,target):
    return guard_translation(SourceFacts(sku='1001',details_es=source),{'details':target},('details',))

@pytest.mark.parametrize('row',ROWS,ids=lambda r:r['sku'])
def test_real_unresolved_source_blocks_existing_effect_or_guessed_state(row):
    assert row['decision']=='REVIEW_REQUIRED' and row['formal_approval'] is False
    assert row['source_evidence']['selected']['text']==row['source']
    assert len(row['source_evidence']['selected']['file_hash'])==64
    assert 'target' not in row
    assert any(f['rule_id']=='SOURCE_DETAILS_TYPED_VALUE_INVALID' for f in check(row['source'],row['before'])['findings'])

@pytest.mark.parametrize('value',['Válido','valido',' VÁLIDO '])
def test_invalid_value_is_source_review_even_if_provider_guesses_liquid(value):
    assert check(str({'Sustancia':value}), "{'物态':'液体'}")['status']=='FAIL'

@pytest.mark.parametrize('source_value,target_value',[('Líquido/liquidez','液体/流动性'),('Crema','乳霜'),('Polvo','粉末')])
def test_valid_physical_state_is_not_blocked(source_value,target_value):
    assert check(str({'Sustancia':source_value}),str({'物态':target_value}))['status']=='PASS'

def test_source_is_not_rewritten_to_official_empty():
    facts=SourceFacts(sku='1001',details_es="{'Sustancia':'Válido'}")
    guard_translation(facts,{'details':"{'物态':'有效'}"},('details',))
    assert facts.details_es=="{'Sustancia':'Válido'}"

def test_invalid_details_do_not_block_another_requested_field():
    facts=SourceFacts(sku='1001',details_es="{'Sustancia':'Válido'}",desc_es='Producto')
    assert guard_translation(facts,{'description':'商品'},('description',))['status']=='PASS'

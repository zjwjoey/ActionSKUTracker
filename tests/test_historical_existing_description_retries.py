"""Explicit historical evidence and QA scope, not approval records."""
import json
from pathlib import Path
import pytest
from action_tracker.localization.contracts import SourceFacts
from action_tracker.localization.semantic import parse_semantic_facts
from action_tracker.localization.qa import guard_translation
CASES=json.loads((Path(__file__).parent/'fixtures/historical_existing_description_retries_20261010.json').read_text('utf8'))
def facts(record):return parse_semantic_facts(SourceFacts.from_record(record))
def check(record,target):return guard_translation(SourceFacts.from_record(record),{'description':target},('description',),semantic_facts=facts(record))
@pytest.mark.parametrize('row',CASES,ids=lambda x:x['sku'])
def test_real_previous_blocked_correction(row):
 assert row['source_evidence']['selected']['text']==row['source']
 assert len(row['source_evidence']['selected']['file_hash'])==64
 assert row['before_module_guard']['status']=='FAIL' and row['formal_approval'] is False
 mapping={'name':'name_es','cat1':'cat1_es','cat2':'cat2_es','spec':'spec_es','description':'desc_es','details':'details_es'}
 assert check({mapping[k]:v for k,v in row['context'].items()},row['target'])['status']=='PASS'
def test_peelable_candy_is_not_rubber_band_semantic_fact():
 f=facts({'desc_es':'Gomas pelables: disfruta de la gominola del interior'})
 assert any(x.source_text=='gomas pelables' and x.value=='软糖' for x in f)
 assert not any(x.value=='橡皮筋' for x in f)
 assert check({'desc_es':'Gomas pelables: disfruta de la gominola del interior'},'可剥皮橡皮筋')['status']=='FAIL'
@pytest.mark.parametrize('record',[{'desc_es':'gomas pelables','name_es':'gominolas'}, {'desc_es':'gomas pelables para fabricar'}, {'desc_es':'gomas de pelo'}])
def test_candy_sense_requires_both_own_source_markers(record):
 assert not any(x.source_text=='gomas pelables' for x in facts(record))
 assert check(record,'可剥皮软糖')['status']=='FAIL'
def test_separate_rubber_band_survives_candy_disambiguation():
 f=facts({'desc_es':'gomas pelables: gominola; también gomas para sujetar'})
 assert any(x.value=='软糖' for x in f)
 assert any(x.value=='橡皮筋' for x in f)
def test_dictionary_cannot_reintroduce_wrong_broad_candy_sense():
 f=parse_semantic_facts(SourceFacts.from_record({'desc_es':'gomas pelables con gominola'}),dictionaries={'product_types':{'gomas':'橡皮筋'}})
 assert not any(x.value=='橡皮筋' for x in f)
@pytest.mark.parametrize('record,target',[( {'desc_es':'Producto','name_es':'Play-Doh Create & Celebrate'}, 'Play-Doh Create & Celebrate彩泥'), ({'desc_es':'Play-Doh Create & Celebrate'},'Play-Doh Create彩泥')])
def test_brand_must_be_complete_in_own_field(record,target):
 assert check(record,target)['status']=='FAIL'

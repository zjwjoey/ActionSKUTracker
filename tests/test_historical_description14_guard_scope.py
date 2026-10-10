"""Real reviewed own-source facts; fixtures never confer approval."""
import json
from pathlib import Path
import pytest
from action_tracker.localization.contracts import SourceFacts
from action_tracker.localization.qa import guard_translation
from action_tracker.localization.semantic import parse_semantic_facts
CASES=json.loads((Path(__file__).parent/'fixtures/historical_description14_guard_scope_20261010.json').read_text('utf8'))
def check(record,target):
 source=SourceFacts.from_record(record)
 return guard_translation(source,{'description':target},('description',),semantic_facts=parse_semantic_facts(source))
@pytest.mark.parametrize('row',CASES,ids=lambda r:r['sku'])
def test_real_description14_scope(row):
 assert row['source_evidence']['selected']['text']==row['source']
 assert len(row['source_evidence']['selected']['file_hash'])==64
 assert row['before_module_guard']['status']=='FAIL' and row['formal_approval'] is False
 mapping={'name':'name_es','cat1':'cat1_es','cat2':'cat2_es','spec':'spec_es','description':'desc_es','details':'details_es'}
 assert check({mapping[k]:v for k,v in row['context'].items()},row['target'])['status']=='PASS'
@pytest.mark.parametrize('source,target',[('dos pendientes','2只耳环'),('tres pequeñas cajas','3只小盒'),('ocho colores','8种颜色')])
def test_spelled_count_own_field(source,target):
 assert check({'desc_es':source},target)['status']=='PASS'
 assert check({'desc_es':'Producto','spec_es':source},target)['status']=='FAIL'
 assert check({'desc_es':source},target.replace('2','4').replace('3','5').replace('8','9'))['status']=='FAIL'
@pytest.mark.parametrize('token',['Jawbreaker','i-Scrub','Olus','Snacks of the World','Stretcherz Stretch Squad mini'])
def test_token_exception_cannot_borrow_other_source(token):
 assert check({'desc_es':'Producto','name_es':token},token+'产品')['status']=='FAIL'
@pytest.mark.parametrize('source,target',[('Producto','AI摄像头'),('Producto','USB-C线缆'),('Iluminación','LED照明'),('Snacks of the World','the零食'),('Stretcherz Stretch Squad mini','mini人偶')])
def test_incomplete_or_added_technical_facts_stay_blocked(source,target):
 assert check({'desc_es':source},target)['status']=='FAIL'
def test_ai_translation_requires_exact_source_acronym():
 assert check({'desc_es':'La IA reconoce personas'},'AI识别人')['status']=='PASS'
 assert check({'desc_es':'La IAX reconoce personas'},'AI识别人')['status']=='FAIL'

@pytest.mark.parametrize('source,target',[('cuatro colores','四种颜色'),('dos colores','两种协调色彩')])
def test_non_singular_chinese_color_count(source,target):
 assert check({'desc_es':source},target)['status']=='PASS'
 assert check({'desc_es':source},target.replace('四','三').replace('两','三'))['status']=='FAIL'
def test_generic_chinese_kind_does_not_become_explicit_one():
 from action_tracker.localization.qa import _numbers
 assert not _numbers('一种实用设计')
def test_repeated_source_quantity_accepts_repeated_chinese_kind():
 assert check({'desc_es':'4 colores y cuatro colores'},'4种颜色，四种颜色')['status']=='PASS'

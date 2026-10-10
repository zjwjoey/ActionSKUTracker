"""CI_SAFE: real own-field noun senses and spelled control-position counts."""
import json
from pathlib import Path
import pytest
from action_tracker.localization.contracts import SourceFacts
from action_tracker.localization.qa import guard_translation
from action_tracker.localization.semantic import parse_semantic_facts

ROWS=json.loads((Path(__file__).parent/'fixtures/historical_description17_guard_cases_20261010.json').read_text('utf8'))

def check(source,target,name=''):
    facts=SourceFacts(sku='1001',desc_es=source,name_es=name)
    return guard_translation(facts,{'description':target},('description',),semantic_facts=parse_semantic_facts(facts))

@pytest.mark.parametrize('row',ROWS,ids=lambda row:row['sku'])
def test_real_candidate_guard_with_own_source(row):
    assert row['source_evidence']['selected']['text']==row['source']
    assert len(row['source_evidence']['selected']['file_hash'])==64
    assert row['formal_approval'] is False
    assert row['before_module_guard']['status']=='FAIL'
    assert check(row['source'],row['target'])['status']=='PASS'

@pytest.mark.parametrize('source,target',[
    ('Goma de borrar','橡皮'),
    ('Barra de pantalla con opciones de iluminación','屏幕挂灯'),
    ('Mascarilla que se coloca como un paño sobre la cara','植物纤维面膜布'),
    ('Tiene cuatro posiciones para controlar el caudal de agua','有4个档位控制水流'),
])
def test_complete_own_field_phrase(source,target):
    assert check(source,target)['status']=='PASS'

@pytest.mark.parametrize('source,target',[
    ('Goma','橡皮'),
    ('Iluminación','屏幕挂灯'),
    ('Opciones de iluminación','屏幕挂灯'),
    ('Paño','面膜'),
    ('Mascarilla y paño','面膜'),
    ('Tiene posiciones para controlar el caudal de agua','有4个档位控制水流'),
    ('Tiene treinta y cuatro posiciones','有4个档位'),
])
def test_incomplete_or_compound_source_stays_blocked(source,target):
    assert check(source,target)['status']=='FAIL'

@pytest.mark.parametrize('source,target,other',[
    ('Iluminación','屏幕挂灯','Barra de pantalla con opciones de iluminación'),
    ('Paño','面膜','Mascarilla que se coloca como un paño sobre la cara'),
    ('Tiene posiciones','有4个档位','Tiene cuatro posiciones'),
])
def test_other_field_cannot_supply_qualifier(source,target,other):
    assert check(source,target,other)['status']=='FAIL'

@pytest.mark.parametrize('target',['有3个档位控制水流','有5个档位控制水流','有档位控制水流'])
def test_spelled_position_quantity_must_be_preserved(target):
    assert check('Tiene cuatro posiciones para controlar el caudal de agua',target)['status']=='FAIL'

def test_alias_does_not_drop_usb_or_numeric_source_facts():
    assert check('Barra de pantalla con opciones de iluminación y USB-A, 3 tipos de luz','屏幕挂灯')['status']=='FAIL'

def test_repeated_positions_do_not_collapse_distinct_counted_mentions():
    assert check('Cuatro posiciones y cuatro posiciones','4个档位')['status']=='FAIL'

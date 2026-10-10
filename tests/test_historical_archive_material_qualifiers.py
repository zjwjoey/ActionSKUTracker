"""CI_SAFE: archive facts distinguish material appearance and bamboo fibre."""
import json
from pathlib import Path
import pytest
from action_tracker.localization.contracts import SourceFacts
from action_tracker.localization.qa import guard_translation
from action_tracker.localization.semantic import parse_semantic_facts

ROWS=json.loads((Path(__file__).parent/'fixtures/historical_archive_material_qualifiers_20261010.json').read_text('utf8'))

def check(source,target,field='name',**context):
    key={'name':'name_es','description':'desc_es'}[field]
    facts=SourceFacts(sku='1001',**{key:source,**context})
    dictionaries={'terms':[{'term_es':'madera','term_zh':'木质','term_type':'MATERIAL'}, {'term_es':'bambú','term_zh':'竹制','term_type':'MATERIAL'}]}
    return guard_translation(facts,{field:target},(field,),semantic_facts=parse_semantic_facts(facts,dictionaries=dictionaries))

@pytest.mark.parametrize('row',ROWS,ids=lambda r:r['sku'])
def test_real_source_qualified_material(row):
    assert row['source_evidence']['selected']['text']==row['source']
    assert len(row['source_evidence']['selected']['file_hash'])==64
    result=check(row['source'],row['reviewed_value'],row['field'])
    assert result['status']=='PASS'

@pytest.mark.parametrize('target',['木质时钟','木制时钟','实木时钟'])
def test_wood_appearance_cannot_approve_real_wood(target):
    assert check('Reloj con aspecto de madera',target)['status']=='FAIL'

def test_appearance_is_not_borrowed_from_other_field():
    assert check('Reloj de madera','木纹时钟',desc_es='Aspecto de madera')['status']=='FAIL'
    assert check('Reloj de madera','木质时钟')['status']=='PASS'

def test_fibre_qualifier_is_not_borrowed_or_dropped():
    assert check('Cesta de bambú','竹纤维篮',desc_es='Fibras de bambú')['status']=='FAIL'
    assert check('Hechos de fibras de bambú sostenible','竹制',field='description')['status']=='FAIL'
    assert check('Hechos de fibras de bambú sostenible','采用竹纤维',field='description')['status']=='PASS'

def test_mixed_material_does_not_waive_real_wood():
    assert check('Reloj con aspecto de madera y marco de madera','木纹时钟')['status']=='FAIL'

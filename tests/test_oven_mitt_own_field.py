"""CI_SAFE: oven mitt sense and explicit same-set references only."""
import pytest
from action_tracker.localization.contracts import SourceFacts,SemanticFact
from action_tracker.localization.qa import guard_translation

def check(source,target,**context):
    facts=(SemanticFact('PRODUCT_TYPE','manoplas','沐浴手套',source_field='desc_es'),)
    return guard_translation(SourceFacts.from_record({**context,'desc_es':source}),{'description':target},('description',),semantic_facts=facts)['overall_ready']

def test_complete_oven_phrase():
    assert check('Manoplas para horno','烤箱隔热手套')

def test_repeated_same_numbered_set_reference():
    assert check('Juego de 2 manoplas para horno. El juego está compuesto por 2 manoplas con estampado floral.','2只装烤箱手套；套装含2只隔热手套，带花卉图案。')

@pytest.mark.parametrize('source',[
    'Manoplas','Manoplas para baño','Manoplas de invierno',
    'Manoplas para horno; otras manoplas para baño',
    'Juego de 2 manoplas para horno; otro juego de 2 manoplas',
    'Juego de 2 manoplas para horno; el juego está compuesto por 3 manoplas',
    'No son manoplas para horno','Sin manoplas para horno'])
def test_unqualified_unrelated_or_negated_uses_do_not_gain_oven_sense(source):
    assert not check(source,'烤箱隔热手套')

def test_other_field_cannot_supply_qualification():
    assert not check('Manoplas','烤箱隔热手套',name_es='Manoplas para horno')

@pytest.mark.parametrize('target',['沐浴手套','不含烤箱手套','烤箱手套USB-C','烤箱手套3只'])
def test_wrong_sense_negation_added_model_and_added_number_stay_blocked(target):
    assert not check('Manoplas para horno',target)

"""Real source regression plus fail-closed contrasts for the quantity gate."""
from collections import Counter
import pytest
from action_tracker.localization.qa import _numbers, guard_translation
from action_tracker.localization.contracts import SourceFacts

SOURCE = ('Contiene un bloc de puntuación con 100 hojas\n'
          'Funda de terciopelo en caja y vaso\n'
          'Incluye 5 dados y un lápiz')
TARGET = '含100张纸的计分本；盒子和骰盅配天鹅绒套；含5颗骰子和1支铅笔'

def check(source, target, **context):
    facts = SourceFacts.from_record({'desc_es': source, **context})
    return guard_translation(facts, {'description': target}, ('description',))

def test_real_dice_game_preserves_each_explicit_quantity():
    assert _numbers(SOURCE) == Counter({'100': 1, '5': 1, '1': 1})
    assert check(SOURCE, TARGET)['overall_ready']

@pytest.mark.parametrize('source', [
    'Incluye 7 dados y un lápiz',
    'Especificaciones; Incluye 7 dados y un lápiz',
    'INCLUYE 7 DADOS Y UN LAPIZ',
])
def test_complete_inclusion_phrase_is_not_sku_specific(source):
    assert _numbers(source) == Counter({'7': 1, '1': 1})

@pytest.mark.parametrize('source', [
    'Un producto práctico',
    'Con un lápiz',
    'No incluye 5 dados y un lápiz',
    'Juego llamado Incluye 5 dados y un lápiz',
    'Incluye 5 dados y un lápizUSB',
    'Incluye 5 dados y treinta y un lápiz',
    'Incluye 5 dados y un portaminas',
])
def test_other_article_brand_negative_and_compound_phrases_grant_no_one(source):
    assert '1' not in _numbers(source)

@pytest.mark.parametrize('target', [
    TARGET.replace('5颗', '6颗'),
    TARGET.replace('1支', '2支'),
    TARGET + '；附1块橡皮',
    TARGET + '；功率20瓦',
])
def test_changed_or_extra_quantities_remain_blocked(target):
    assert not check(SOURCE, target)['overall_ready']

def test_context_cannot_authorize_a_count_missing_from_description():
    result = check('Bloc de puntuación', '计分本，含1支铅笔',
                   name_es='Incluye 5 dados y un lápiz')
    assert not result['overall_ready']
    assert any(f['rule_id']=='NUMERIC_ADDED' for f in result['findings'])

def test_source_supported_pencil_count_must_not_silently_disappear():
    result = check(SOURCE, '含100张纸的计分本及5颗骰子')
    assert any(f['rule_id']=='NUMERIC_DROPPED' for f in result['findings'])

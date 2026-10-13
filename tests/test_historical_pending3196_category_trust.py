"""CI_SAFE: real source/category evidence; temp dictionaries and no providers."""
import csv
import json
import runpy
from pathlib import Path

import pytest

from action_tracker.dictionary import CATEGORY_DICTIONARY_HEADERS, normalize_category_key
from action_tracker.localization.engine import LocalizationEngine
from action_tracker.localization.knowledge import KnowledgeLoader

ROOT = Path(__file__).resolve().parents[1]
CASES = json.loads((ROOT / 'tests/fixtures/historical_pending3196_category_trust_20261011.json').read_text(encoding='utf-8-sig'))


def load_rows(tmp_path, rows):
    with (tmp_path / 'category_dictionary.csv').open('w', encoding='utf-8-sig', newline='') as fh:
        writer = csv.DictWriter(fh, fieldnames=CATEGORY_DICTIONARY_HEADERS)
        writer.writeheader()
        writer.writerows(rows)
    return KnowledgeLoader(tmp_path).load()


def category_gate():
    return runpy.run_path(str(ROOT / 'scripts/run_historical_localization_pilot.py'))['reviewed_category_mapping_status']


def test_real_parent_confirmation_does_not_approve_child_or_delegate_apply(tmp_path):
    held = next(c for c in CASES if not c['expected_mapping_available'])
    knowledge = load_rows(tmp_path, [held['dictionary_evidence']['row']])
    context = held['context']
    assert knowledge['cat1_map'][normalize_category_key(context['cat1_es'])] == '园艺户外'
    assert (normalize_category_key(context['cat1_es']), normalize_category_key(context['cat2_es'])) not in knowledge['cat2_map']
    plan = LocalizationEngine(knowledge=knowledge).resolve({'sku':held['sku'], **context})
    assert plan.fields['cat2_zh'].status == 'REVIEW_REQUIRED'
    # Fluent existing Chinese and independent semantic PASS are not a
    # substitute for approval of this source category pair.
    review = {'field':'cat2','after':held['current_chinese'],'semantic_status':'PASS','review_model':'CODEX'}
    assert category_gate()(review, context, knowledge) == 'CATEGORY_MAPPING_REVIEW_REQUIRED'


def test_real_approved_pair_remains_available_and_target_is_bound(tmp_path):
    approved = next(c for c in CASES if c['expected_mapping_available'])
    knowledge = load_rows(tmp_path, [approved['dictionary_evidence']['row']])
    context = approved['context'];target = approved['dictionary_evidence']['row']['cat2_zh']
    gate = category_gate()
    assert gate({'field':'cat2','after':target},context,knowledge) == 'READY'
    assert gate({'field':'cat2','after':'另一个类别'},context,knowledge) == 'CATEGORY_MAPPING_TARGET_MISMATCH_REVIEW_REQUIRED'
    assert gate({'field':'cat2','after':target},{**context,'cat1_es':'Jardín'},knowledge) == 'CATEGORY_MAPPING_REVIEW_REQUIRED'
    assert gate({'field':'description','after':'文字'},context,knowledge) == 'READY'


@pytest.mark.parametrize('status', ['PENDING','AI_CANDIDATE','UNREVIEWED','REJECTED','APPROVED','', 'NEEDS_REVIEW'])
def test_unapproved_states_cannot_create_mapping_or_shadow_approved_row(tmp_path,status):
    approved = next(c for c in CASES if c['expected_mapping_available'])['dictionary_evidence']['row']
    unapproved = {**approved,'review_status':status,'cat1_zh':'错误父类','cat2_zh':'错误子类'}
    knowledge = load_rows(tmp_path,[approved,unapproved])
    key = (normalize_category_key(approved['cat1_es']),normalize_category_key(approved['cat2_es']))
    assert knowledge['cat2_map'][key] == approved['cat2_zh']
    assert knowledge['cat1_map'][key[0]] == approved['cat1_zh']
    knowledge = load_rows(tmp_path,[unapproved])
    assert knowledge['cat1_map'] == {} and knowledge['cat2_map'] == {}


@pytest.mark.parametrize('status',['HUMAN_REVIEWED','LOCKED','SEED_REVIEWED'])
def test_existing_canonical_knowledge_trust_states_are_preserved(tmp_path,status):
    row = {**CASES[1]['dictionary_evidence']['row'],'review_status':status.lower()+' '}
    knowledge = load_rows(tmp_path,[row])
    key = (normalize_category_key(row['cat1_es']),normalize_category_key(row['cat2_es']))
    assert knowledge['cat2_map'][key] == row['cat2_zh']


def test_context_terminology_cannot_bypass_unapproved_pair_in_daily_planner(tmp_path):
    row = {**CASES[0]['dictionary_evidence']['row'],'cat1_es':'Bricolaje','cat2_es':'Complementos de pintura',
           'cat1_zh':'DIY五金','cat2_zh':'涂料配件','review_status':'CAT1_CONFIRMED'}
    record={'sku':'boundary','name_es':'Soporte de rodillo','cat1_es':row['cat1_es'],'cat2_es':row['cat2_es']}
    plan=LocalizationEngine(knowledge=load_rows(tmp_path,[row])).resolve(record)
    assert plan.fields['cat2_zh'].status == 'REVIEW_REQUIRED'
    row['review_status']='HUMAN_REVIEWED'
    plan=LocalizationEngine(knowledge=load_rows(tmp_path,[row])).resolve(record)
    assert plan.fields['cat2_zh'].value == '涂料配件'
    assert plan.fields['cat2_zh'].status == 'READY'


def test_conflicting_approved_pair_still_fails_closed(tmp_path):
    row=CASES[1]['dictionary_evidence']['row']
    with pytest.raises(ValueError,match='CATEGORY_DICTIONARY_CONFLICT'):
        load_rows(tmp_path,[row,{**row,'cat2_zh':'不同的已批准映射'}])

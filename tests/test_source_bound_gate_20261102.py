"""Real reviewed source bindings remain scoped during export and QA."""
import copy
import csv
import json
import runpy
from pathlib import Path

from action_tracker.dictionary import CATEGORY_DICTIONARY_HEADERS
from action_tracker.exporting.repair_report import audit_repaired_rows
from action_tracker.localization.knowledge import KnowledgeLoader
from test_current102_qa import CASES, approved_record

ROOT = Path(__file__).resolve().parents[1]


def test_turbo_fire_approved_model_survives_export_but_stale_evidence_does_not():
    case = next(c for c in CASES if c['review']['sku'] == '3225710')
    record = approved_record(case)
    row = {'编号': '3225710', '标题': record['name_zh']}
    assert not [f for f in audit_repaired_rows([record], [row])['findings'] if f['field'] == 'name_zh']
    record['_approved_name_semantic_review']['review']['field_source_hash'] = 'stale'
    findings = audit_repaired_rows([record], [row])['findings']
    assert any(f['field'] == 'name_zh' and f['code'] in {'SPANISH_RESIDUAL', 'PROTECTED_TOKEN_MISSING'} for f in findings)


def test_baby_category_candidate_is_bound_to_the_exact_parent_and_target(tmp_path):
    row = {'cat1_es': 'Cuidado personal', 'cat2_es': 'Cuidados para el bebé', 'cat1_code': 'C06',
           'cat1_zh': '个人美容', 'cat2_zh': '婴儿护理', 'review_status': 'SEED_REVIEWED',
           'notes': '隔离种子；用户授权代理审核，不表示人工逐项审批'}
    with (tmp_path / 'category_dictionary.csv').open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=CATEGORY_DICTIONARY_HEADERS); writer.writeheader(); writer.writerow(row)
    knowledge = KnowledgeLoader(tmp_path).load()
    gate = runpy.run_path(str(ROOT / 'scripts/run_historical_localization_pilot.py'))['reviewed_category_mapping_status']
    context = {'cat1_es': row['cat1_es'], 'cat2_es': row['cat2_es']}
    assert gate({'field': 'cat2', 'after': '婴儿护理'}, context, knowledge) == 'READY'
    assert gate({'field': 'cat2', 'after': '儿童餐垫'}, context, knowledge) == 'CATEGORY_MAPPING_TARGET_MISMATCH_REVIEW_REQUIRED'
    assert gate({'field': 'cat2', 'after': '婴儿护理'}, {**context, 'cat1_es': 'Cocina'}, knowledge) == 'CATEGORY_MAPPING_REVIEW_REQUIRED'
    row['review_status'] = 'OWNER_PROXY_PENDING_PROMOTION'
    with (tmp_path / 'category_dictionary.csv').open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=CATEGORY_DICTIONARY_HEADERS); writer.writeheader(); writer.writerow(row)
    assert gate({'field': 'cat2', 'after': '婴儿护理'}, context, KnowledgeLoader(tmp_path).load()) == 'CATEGORY_MAPPING_REVIEW_REQUIRED'

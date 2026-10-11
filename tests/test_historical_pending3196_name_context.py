"""CI_SAFE: real same-SKU context disambiguation, no API or production writes."""
import json
from pathlib import Path

import pytest

from action_tracker.localization.contracts import SourceFacts
from action_tracker.localization.engine import LocalizationEngine
from action_tracker.localization.knowledge import KnowledgeLoader
from action_tracker.localization.pipeline import translate_candidate
from action_tracker.localization.providers.base import FakeTranslationProvider
from action_tracker.localization.qa import guard_translation
from action_tracker.localization.semantic import parse_semantic_facts

ROOT=Path(__file__).resolve().parents[1]
ROWS=json.loads((ROOT/'tests/fixtures/historical_pending3196_name_context_20261011.json').read_text('utf-8-sig'))


def real_eraser():
    row=next(r for r in ROWS if r['sku']=='3223681')
    return {'sku':row['sku'],**row['context']}


def engine():
    return LocalizationEngine(knowledge=KnowledgeLoader(ROOT/'data/dictionary').load())


def test_real_eraser_uses_context_meaning_and_keeps_own_3d_without_importing_count():
    record=real_eraser();e=engine();plan=e.resolve(record)
    assert plan.fields['name_zh'].value=='3D橡皮擦'
    assert plan.fields['name_zh'].status=='READY'
    facts=[f for f in plan.semantic_facts if f.source_field=='name_es']
    assert any(f.semantic_type=='PRODUCT_TYPE' and f.value=='橡皮擦' for f in facts)
    assert not any(f.value=='橡皮筋' for f in facts)
    assert guard_translation(SourceFacts.from_record(record),{'name':'3D橡皮擦'},('name',),semantic_facts=plan.semantic_facts)['status']=='PASS'
    for bad in ('3D橡皮筋','橡皮擦','3D橡皮擦3件装'):
        assert guard_translation(SourceFacts.from_record(record),{'name':bad},('name',),semantic_facts=plan.semantic_facts)['status']=='FAIL'


def test_default_provider_hint_is_correct_and_does_not_leak_pack_quantity():
    class RecordingProvider(FakeTranslationProvider):
        def translate(self,request):self.request=request;return super().translate(request)
    provider=RecordingProvider(mapping={'name':'3D橡皮擦'})
    candidate=translate_candidate(real_eraser(),('name',),provider,engine=engine())
    assert candidate.qa['status']=='PASS'
    assert any(t['source'].casefold()=='gomas' and t['target']=='橡皮擦' for t in provider.request.terms)
    assert not any(t['source'].casefold()=='gomas' and t['target']=='橡皮筋' for t in provider.request.terms)


@pytest.mark.parametrize('head',[
    '', 'No incluye gomas de borrar', 'Sin gomas de borrar',
    'También puedes comprar gomas de borrar', 'Usa gomas de borrar para limpiar',
])
def test_category_or_unrelated_negative_context_cannot_certify_eraser(head):
    record={**real_eraser(),'desc_es':head}
    facts=parse_semantic_facts(SourceFacts.from_record(record))
    assert not any(f.source_field=='name_es' and f.value=='橡皮擦' for f in facts)
    assert engine().resolve(record).fields['name_zh'].status == 'REVIEW_REQUIRED'


def test_explicit_elastic_title_is_not_overridden_and_separate_material_survives():
    record={**real_eraser(),'name_es':'Gomas elásticas 3D','details_es':'Material: Goma'}
    facts=parse_semantic_facts(SourceFacts.from_record(record))
    assert not any(f.source_field=='name_es' and f.value=='橡皮擦' for f in facts)
    assert any(f.source_field=='details_es' and f.semantic_type=='MATERIAL' and f.value=='橡胶' for f in facts)


def test_unknown_real_identity_stays_review_required_without_guessed_gold():
    row=next(r for r in ROWS if r['sku']=='3214808')
    plan=engine().resolve({'sku':row['sku'],**row['context']})
    assert plan.fields['name_zh'].status=='REVIEW_REQUIRED'
    assert '彩笔' not in plan.fields['name_zh'].value


@pytest.mark.parametrize('title',['Papel Spectrum A4','Producto desconocido 3D','Producto desconocido USB-C'])
def test_technical_token_alone_is_not_a_complete_product_name(title):
    plan=LocalizationEngine().resolve({'sku':'boundary','name_es':title})
    assert plan.fields['name_zh'].status=='REVIEW_REQUIRED'
    assert 'NAME_PRODUCT_IDENTITY_REVIEW' in plan.fields['name_zh'].review_reasons


def test_reviewed_existing_name_is_still_reused_without_rewriting():
    plan=LocalizationEngine(knowledge={'product_by_sku':{'approved':{'name_zh':'已审核的商品名'}}}).resolve({'sku':'approved','name_es':'Papel Spectrum A4'})
    assert plan.fields['name_zh'].value=='已审核的商品名'
    assert plan.fields['name_zh'].status=='READY'

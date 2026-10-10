"""CI_SAFE: QA vocabulary does not publish experimental generation policy."""
import json
from pathlib import Path
import pytest
from action_tracker.localization.contracts import SourceFacts
from action_tracker.localization.engine import LocalizationEngine
from action_tracker.localization.pipeline import translate_candidate
from action_tracker.localization.providers.base import FakeTranslationProvider
from action_tracker.localization.qa import guard_translation

ROWS=json.loads((Path(__file__).parent/'fixtures/historical_product_noun_errors_20261010.json').read_text('utf8'))

@pytest.mark.parametrize('row',ROWS[:6],ids=lambda r:r['sku'])
def test_default_generation_excludes_new_terms_but_qa_retains_them(row):
    record={'sku':row['sku'],'name_es':row['source']}
    source=SourceFacts.from_record(record)
    stable=LocalizationEngine()
    assert not any(f.source_text==row['source_term'] for f in stable.generation_semantic_facts(source))
    assert any(f.source_text==row['source_term'] for f in stable.resolve(record).semantic_facts)
    experimental=LocalizationEngine(experimental_historical_candidates=True)
    assert any(f.source_text==row['source_term'] for f in experimental.generation_semantic_facts(source))

class RecordingProvider(FakeTranslationProvider):
    def translate(self,request):
        self.request=request
        return super().translate(request)

@pytest.mark.parametrize('experimental',[False,True])
def test_provider_terms_and_context_use_generation_scope(experimental):
    row=ROWS[0];record={'sku':row['sku'],'name_es':row['source']}
    provider=RecordingProvider(mapping={'name':'记号笔'})
    engine=LocalizationEngine(experimental_historical_candidates=experimental)
    translate_candidate(record,('name',),provider,engine=engine)
    term_present=any(t['source']==row['source_term'] for t in provider.request.terms)
    assert term_present is experimental
    assert bool(provider.request.context['product_type']) is experimental

def test_enhanced_semantic_qa_blocks_bad_candidate_in_default_mode():
    row=ROWS[0];record={'sku':row['sku'],'name_es':row['source']}
    engine=LocalizationEngine()
    qa=guard_translation(SourceFacts.from_record(record),{'name':'记事本'},('name',),
                         semantic_facts=engine.resolve(record).semantic_facts)
    assert any(f['rule_id']=='SEMANTIC_FACT_DROPPED' for f in qa['findings'])

def test_runtime_flag_is_explicit_boolean_and_disabled_by_default():
    assert LocalizationEngine().experimental_historical_candidates is False
    with pytest.raises(ValueError,match='BOOLEAN_REQUIRED'):
        LocalizationEngine(experimental_historical_candidates='false')

def test_size_label_rule_is_experimental_without_losing_protected_models():
    record={'sku':'isolation','spec_es':'Tallas 98-140 | USB-C | XL'}
    stable=LocalizationEngine().resolve(record).fields['spec_zh'].value
    experimental=LocalizationEngine(experimental_historical_candidates=True).resolve(record).fields['spec_zh'].value
    assert 'Tallas' in stable and '尺码' not in stable
    assert '尺码' in experimental and 'Tallas' not in experimental
    assert 'USB-C' in stable and 'USB-C' in experimental and 'XL' in experimental

def test_provider_retry_preserves_generation_scope_and_enhanced_qa():
    from action_tracker.localization.repair import repair_field
    row=ROWS[0];record={'sku':row['sku'],'name_es':row['source']}
    engine=LocalizationEngine();provider=RecordingProvider(mapping={'name':'记事本'})
    repaired=repair_field(record,'name','记事本',repair_reason='isolated-policy-test',
        provider=provider,semantic_facts=engine.resolve(record).semantic_facts,
        generation_semantic_facts=engine.generation_semantic_facts(SourceFacts.from_record(record)))
    assert not any(t['source']==row['source_term'] for t in provider.request.terms)
    assert repaired.qa['status']=='FAIL'

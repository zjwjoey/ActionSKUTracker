"""CI_SAFE: offline cache identity tests with a real historical source fixture."""
import hashlib,json
from dataclasses import asdict
from pathlib import Path
import pytest
from action_tracker.localization.pipeline import translate_pending_requests
from action_tracker.localization.hashes import value_hash
from action_tracker.localization.providers.base import TranslationRequest,TranslationResponse
from action_tracker.localization.providers.qwen_mt import QwenMTProvider
from action_tracker.localization.providers import qwen_mt

ROW=next(r for r in json.loads((Path(__file__).parent/'fixtures/historical_description17_guard_cases_20261010.json').read_text('utf8')) if r['sku']=='3204159')
def request():
    return TranslationRequest(ROW['sku'],{'desc_es':ROW['source']},('description',),value_hash(ROW['source']),context=ROW['context'])
class OfflineProvider(QwenMTProvider):
    def translate(self,req):
        self.calls=getattr(self,'calls',0)+1
        return TranslationResponse({'description':ROW['target']},self.provider,self.model,req.source_hash,'offline-test','offline-test','offline-test')
def provider():
    return OfflineProvider('https://offline.invalid/compatible-mode/v1',rate_limit_per_second=0)
def legacy(path,complete=True):
    p=provider(); result=translate_pending_requests((request(),),p,path)
    plan={'provider':p.provider,'model':p.model,'requests':[asdict(request())]}
    old={'plan':plan,'plan_hash':hashlib.sha256(json.dumps(plan,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest(),'responses':result['responses'] if complete else []}
    path.write_text(json.dumps(old,ensure_ascii=False),encoding='utf8')
    return p

@pytest.mark.parametrize('setting,value',[('base_url','https://different.invalid/compatible-mode/v1'),('include_optional_options',False)])
def test_changed_adapter_rejects_without_calls_or_checkpoint_writes(tmp_path,setting,value):
    p=provider();path=tmp_path/'cache.json';translate_pending_requests((request(),),p,path);before=path.read_bytes()
    setattr(p,setting,value)
    with pytest.raises(ValueError,match='PLAN_CHANGED'):translate_pending_requests((request(),),p,path)
    assert p.calls==1 and path.read_bytes()==before

def test_prompt_content_is_bound(tmp_path,monkeypatch):
    p=provider();path=tmp_path/'cache.json';translate_pending_requests((request(),),p,path)
    monkeypatch.setattr(qwen_mt,'_ECOMMERCE_DOMAIN_PROMPT','Changed future translation instruction')
    with pytest.raises(ValueError,match='PLAN_CHANGED'):translate_pending_requests((request(),),p,path)
    assert p.calls==1

def test_same_contract_resumes_with_no_call_and_no_approval(tmp_path):
    p=provider();path=tmp_path/'cache.json';translate_pending_requests((request(),),p,path)
    second=provider();second.timeout=999;second.max_retries=9;second.api_key_env='NEW_KEY_ENV';second._optional_options_disabled=True
    r=translate_pending_requests((request(),),second,path)
    assert getattr(second,'calls',0)==0 and r['reused_responses']==1
    assert r['cache_configuration_status']=='BOUND_ADAPTER_IDENTITY'
    assert r['responses'][0]['decision']=='PENDING_SEMANTIC_REVIEW' and r['production_writes'] is False
    saved=path.read_text('utf8');assert 'NEW_KEY_ENV' not in saved and 'https://offline.invalid' not in saved

def test_complete_legacy_reused_readonly_and_explicitly_unverified(tmp_path):
    path=tmp_path/'legacy.json';p=legacy(path);before=path.read_bytes();p.base_url='https://different.invalid/api/v1'
    r=translate_pending_requests((request(),),p,path)
    assert r['provider_calls']==0 and r['reused_responses']==1 and p.calls==1
    assert r['cache_configuration_status']=='LEGACY_CONFIGURATION_UNVERIFIED'
    assert path.read_bytes()==before and r['responses'][0]['semantic_status']=='PENDING'

def test_incomplete_legacy_requires_config_evidence_without_resending(tmp_path):
    path=tmp_path/'legacy.json';p=legacy(path,False);before=path.read_bytes()
    with pytest.raises(ValueError,match='LEGACY_CONFIG_REVIEW_REQUIRED'):translate_pending_requests((request(),),p,path)
    assert path.read_bytes()==before and p.calls==1

@pytest.mark.parametrize('change',[{'source':'wrong source'},{'after':''},{'model':'other model'}])
def test_corrupt_saved_response_is_not_reused(tmp_path,change):
    path=tmp_path/'cache.json';p=provider();translate_pending_requests((request(),),p,path)
    state=json.loads(path.read_text('utf8'));state['responses'][0].update(change);path.write_text(json.dumps(state),encoding='utf8')
    with pytest.raises(ValueError,match='INVALID_CHECKPOINT'):translate_pending_requests((request(),),p,path)
    assert p.calls==1

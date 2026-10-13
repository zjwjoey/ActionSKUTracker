from copy import deepcopy
from types import SimpleNamespace
import pytest

from action_tracker.database.connection import connect
from action_tracker.database.production import CommitBundle, ProductionWriter, ProductionDatabaseError
from action_tracker.database.price_correction import apply_verified_original_price_absence
from action_tracker.monitor.listing import _to_light
from action_tracker.orchestrator.daily import _light_dict, _merge_light
from action_tracker.products.updater import plan_updates
from action_tracker.services.price_evidence import merge_observed_prices


def test_listing_complete_absence_clears_old_original_and_discount():
    lp = _to_light(dict(sku='1001',whole='2',fractional='50',original='',discount='',price_observation_complete=True))
    record=dict(current_price=2.5,original_price=3.0,discount=-0.17)
    _merge_light(record,_light_dict(lp))
    assert record['original_price'] is None and record['discount'] is None
    assert record['current_price']==2.5


@pytest.mark.parametrize('proof',[None,False,'true'])
def test_unknown_or_legacy_absence_does_not_clear(proof):
    raw=dict(sku='1001',whole='2',fractional='50',original='',discount='',price_observation_complete=proof)
    record=dict(current_price=2.5,original_price=3.0,discount=-0.17)
    _merge_light(record,_light_dict(_to_light(raw)))
    assert record['original_price']==3.0 and record['discount']==-0.17


def test_complete_price_evidence_cannot_use_unit_price_fallback():
    lp=_to_light(dict(sku='1001',unit_price='1,50 €/kg',original='',discount='',price_observation_complete=True))
    assert lp.price_observation_complete is False
    assert lp.current_price is None
    record=dict(current_price=2.5,original_price=3.0)
    _merge_light(record,_light_dict(lp))
    assert record['original_price']==3.0


def test_newer_detail_price_is_not_overwritten_by_listing():
    record=dict(current_price=2.5,original_price=3.0,discount=-0.17,_price_observation_complete=True)
    light=dict(current_price=2.5,original_price=None,discount=None,_price_observation_complete=True)
    _merge_light(record,light,skip_raw_tags=True)
    assert record['original_price']==3.0


@pytest.mark.parametrize('missing',['original','discount'])
def test_incomplete_raw_payload_is_not_evidence_of_absence(missing):
    raw=dict(sku='1001',whole='2',fractional='50',original='',discount='',price_observation_complete=True)
    del raw[missing]
    assert _to_light(raw).price_observation_complete is False


def test_detail_original_price_cannot_replace_a_missing_selling_price():
    from action_tracker.products.parser import _normalize_detail
    row=_normalize_detail({'sku':'1001','current_price':'','original_price':'3,00 €'},'https://www.action.com/es-es/p/1001/fixture/')
    assert row['current_price'] is None and row['_price_observation_complete'] is False


def test_optional_price_change_is_planned_without_detail_fetch():
    base=dict(current_price=2.5,original_price=3.0,discount=-0.17,image_url='x',desc_es='ok',
              spec_es='100 ml',details_es='Color: Rojo',cat2_es='B')
    light=dict(current_price=2.5,original_price=None,discount=None,image_url='x',_price_observation_complete=True)
    plans=plan_updates({'1001':SimpleNamespace(status='ACTIVE',canonical_id='ACT0001001')},{'1001':base},{'1001':light})
    assert plans[0]['reason']=='PRICE_METADATA_CHANGE' and plans[0]['need_detail'] is False


def test_missing_legacy_discount_is_unknown_not_a_repeated_metadata_change():
    base=dict(current_price=2.5,original_price=3.0,image_url='x',desc_es='ok',raw_tags='',last_seen='2026-10-12',
              spec_es='100 ml',details_es='Color: Rojo',cat2_es='B')
    light=dict(current_price=2.5,original_price=3.0,discount=-0.17,image_url='x',_price_observation_complete=True)
    plans=plan_updates({'1001':SimpleNamespace(status='ACTIVE',canonical_id='ACT0001001')},{'1001':base},{'1001':light},detail_refresh_days=100000)
    assert plans==[]


def test_optional_price_update_does_not_hide_missing_detail_work():
    base = dict(current_price=2.5, original_price=3.0, discount=-0.17,
                image_url='x', desc_es='', details_es='Color: Rojo',
                spec_es='100 ml', cat2_es='B')
    light = dict(current_price=2.5, original_price=None, discount=None,
                 image_url='x', _price_observation_complete=True)
    plans = plan_updates({'1001': SimpleNamespace(status='ACTIVE', canonical_id='ACT0001001')},
                         {'1001': base}, {'1001': light})
    assert plans[0]['need_detail'] is True
    assert plans[0]['reason'] == 'MISSING_FIELD'


def test_optional_price_update_does_not_hide_pending_detail_retry():
    base = dict(current_price=2.5, original_price=3.0, discount=-0.17,
                image_url='x', desc_es='ok', details_es='Color: Rojo',
                spec_es='100 ml', cat2_es='B', detail_status='PENDING')
    light = dict(current_price=2.5, original_price=None, discount=None,
                 image_url='x', _price_observation_complete=True)
    plans = plan_updates({'1001': SimpleNamespace(status='ACTIVE', canonical_id='ACT0001001')},
                         {'1001': base}, {'1001': light})
    assert plans[0]['need_detail'] is True
    assert plans[0]['reason'] == 'DETAIL_PENDING'


def seed(tmp_path):
    path=tmp_path/'primary.db'
    url='https://www.action.com/es-es/p/1001/fixture/'
    head=ProductionWriter(path,role='PRIMARY').commit(CommitBundle(run_id='seed',observation_date='2026-10-12',qa_state='PASS',current_products=({'sku':'1001','name_es':'Producto','current_price':2.5,'original_price':3.0,'product_url':url,'status':'CURRENT'},)))
    item=dict(sku='1001',method='OFFICIAL_BROWSER_PRICE_REGION',region=True,url=url,articles=['1001'],whole='2',fraction='50',original_count=0,discount_count=0,original='',discount='',expected_original_price=3.0,observed_at='2026-10-12T03:00:00Z')
    return path,head,item


def test_price_correction_is_atomic_idempotent_and_preserves_history(tmp_path):
    path,head,item=seed(tmp_path)
    kwargs=dict(items=[item],expected_base_commit_id=head,business_date='2026-10-12')
    assert apply_verified_original_price_absence(path,dry_run=True,**kwargs)['ready_fields']==1
    with connect(path) as db:assert db.execute('SELECT original_price FROM products').fetchone()[0]==3.0
    result=apply_verified_original_price_absence(path,**kwargs)
    assert result['applied_fields']==1
    assert apply_verified_original_price_absence(path,**kwargs)['status']=='NOOP'
    with connect(path) as db:
        assert tuple(db.execute('SELECT current_price,original_price,status FROM products').fetchone())==(2.5,None,'CURRENT')
        assert db.execute('SELECT COUNT(*) FROM price_history').fetchone()[0]==0
        assert db.execute("SELECT COUNT(*) FROM event_history WHERE event_type='ORIGINAL_PRICE_CORRECTION'").fetchone()[0]==1


@pytest.mark.parametrize('patch',[{'region':False},{'original_count':None},{'original_count':1},{'discount_count':1},{'articles':['999']},{'whole':'9'},{'observed_at':'2026-10-11T03:00:00Z'},{'expected_original_price':4.0}])
def test_uncertain_or_changed_price_cannot_be_cleared(tmp_path,patch):
    path,head,item=seed(tmp_path);item.update(patch)
    with pytest.raises((ProductionDatabaseError,ValueError)):
        apply_verified_original_price_absence(path,items=[item],expected_base_commit_id=head,business_date='2026-10-12')
    with connect(path) as db:
        assert db.execute('SELECT original_price FROM products').fetchone()[0]==3.0
        assert db.execute('SELECT COUNT(*) FROM commit_batches').fetchone()[0]==1


def test_base_change_and_duplicate_evidence_are_blocked(tmp_path):
    path,head,item=seed(tmp_path)
    for kwargs in [dict(items=[item],expected_base_commit_id='wrong'),dict(items=[item,deepcopy(item)],expected_base_commit_id=head)]:
        with pytest.raises(ProductionDatabaseError):
            apply_verified_original_price_absence(path,business_date='2026-10-12',**kwargs)

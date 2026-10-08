"""Offline scenario replay using actual frozen source evidence, never PRIMARY."""
from __future__ import annotations
from dataclasses import replace
import hashlib
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from audit_post_release_integrity import REPORT,save,ro


def tables(path):
    names=('products','product_localizations','localization_fields','lifecycle_state','observations',
           'price_history','event_history','commit_batches','translation_queue','translation_source_versions')
    with ro(path) as db:
        return {t:{'count':db.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0],
                   'hash':hashlib.sha256(json.dumps([tuple(r) for r in db.execute(f'SELECT * FROM {t} ORDER BY rowid')],
                         ensure_ascii=False,default=str).encode()).hexdigest()} for t in names}


def replay():
    from action_tracker.database.production import ProductionWriter,ProductionDatabaseError
    from action_tracker.database.repository import ProductionRepository
    from action_tracker.database.integration import build_daily_bundle
    from action_tracker.monitor.sku_monitor import run_sku_monitor
    from action_tracker.orchestrator.daily import _build_current_records,_build_lifecycle_events
    from action_tracker.products.updater import retain_historical_facts
    from action_tracker.state import apply_state_transition
    from action_tracker.services.change import compute_changes
    from action_tracker.localization.registry.repository import LocalizationRegistry
    from action_tracker.services.hashing import localization_source_hash
    historical=json.loads((REPORT/'historical_evidence.json').read_text(encoding='utf-8'))['3207872']
    version=next(r for r in reversed(historical['product_fact_versions'])
                 if json.loads(r['normalized_fact_json']).get('name_es'))
    original=json.loads(version['normalized_fact_json'])
    original.update(sku='3207872',canonical_id='ACT3207872',status='CURRENT')
    import uuid
    path=REPORT/('offline_daily_replay_'+uuid.uuid4().hex[:8]+'.db')
    if path.exists(): raise ValueError('REPLAY_DATABASE_NOT_OVERWRITTEN')
    writer=ProductionWriter(path,role='PRIMARY');repo=ProductionRepository(path)
    registry=LocalizationRegistry(path,role='PRIMARY')
    observations=[]
    first_projection=None
    # Date shifts are simulation dates; source remains the recorded snapshot.
    # Controlled changes are fixture values, never proposed as official facts.
    for day in range(1,9):
        date=f'2026-09-{day:02}';run=f'offline-day{day}'
        before=tables(path);base=repo.load_product_baseline();current=repo.load_current_products();known=repo.load_known_skus()
        present=day not in (2,3,4,8)
        status,today=run_sku_monitor(['3207872'] if present else [],{},current,known,
            sitemap_valid=day!=8,category_coverage={'Cocina':day!=8},business_date=date)
        updated={};events=[];prices=[]
        if present:
            observed=original if day==1 else {'product_url':original['product_url']}
            if day>=6: observed.update(spec_es=original['spec_es']+' | fixture cambio',current_price=3.49)
            verified={k:'DETAIL_CURRENT_RUN' for k,v in observed.items() if v not in (None,'')}
            rec=retain_historical_facts(base.get('3207872',{}),observed,
                reappeared=status['3207872'].status=='REAPPEARED',verified_fields=verified)
            rec.update(sku='3207872',canonical_id='ACT3207872')
            updated['3207872']=rec
        records=_build_current_records(base,updated,status,today,date)
        transition=apply_state_transition(known,status,date,run)
        if present:
            delta=compute_changes('3207872','ACT3207872',base.get('3207872'),records['3207872'],date,.01,999,run)
            events=delta.content_events;prices=delta.price_events
        events+=_build_lifecycle_events(status,date,run)
        bundle=build_daily_bundle(run_id=run,observation_date=date,qa_state='PASS',today_records=records,
            baseline=base,statuses=status,known=known,transition=transition,today_set=today,
            observation_complete=day!=8,price_events=prices,event_events=events,review_rows=[],run_record={})
        # These are transaction fixtures, not collection authorization. Actual
        # daily still requires its persisted Collection Integrity evidence.
        bundle=replace(bundle,requires_collection_integrity=False,base_commit_id=repo.current_head() or '')
        commit=writer.commit(bundle)
        if day==1: first_projection=repo.load_current_products()['3207872']
        registry.ingest_records(records.values(),source_run_id=run,observed_at=date)
        if day==1:
            # Synthetic approved revisions exercise reuse. They stay in this
            # disposable simulation and are not content decisions for PRIMARY.
            aggregate=localization_source_hash(records['3207872'])
            for field in ('name','cat1','cat2','spec','description','details'):
                revision=registry.record_revision_for_sku('3207872',field,'fixture:'+field,
                    source_hash=aggregate,provider='FIXTURE_ONLY',repair_reason='OFFLINE_REGRESSION',qa_status='PASS')
                assert registry.approve_revision(revision,actor='human:fixture')
        after=tables(path)
        assert writer.commit(bundle)==commit
        registry.ingest_records(records.values(),source_run_id=run,observed_at=date)
        repeated=tables(path)
        assert after==repeated
        if day==5:
            assert repo.load_current_products()['3207872']['current_price'] is None
            assert repo.load_current_products()['3207872']['details_es']==first_projection['details_es']
        if day==7:
            assert after['translation_queue']['count']==before['translation_queue']['count']
            assert after['price_history']['count']==before['price_history']['count']
            assert after['event_history']['count']==before['event_history']['count']
        if day==8:
            assert status['3207872'].status=='UNKNOWN'
            assert repo.load_known_skus()['3207872']['missing_count']==known['3207872']['missing_count']
        observations.append(dict(day=day,date=date,status=status['3207872'].status,
            before=before,after=after,resume_unchanged=after==repeated,
            added_prices=after['price_history']['count']-before['price_history']['count'],
            added_events=after['event_history']['count']-before['event_history']['count'],
            added_queue=after['translation_queue']['count']-before['translation_queue']['count']))
    # Reject a bundle prepared against an older head, with no partial writes.
    previous_head=bundle.base_commit_id;before=tables(path)
    try:
        writer.commit(replace(bundle,run_id='stale-head',base_commit_id=previous_head))
        raise AssertionError('stale head accepted')
    except ProductionDatabaseError as exc:
        assert str(exc)=='BASELINE_CHANGED_BEFORE_COMMIT'
    assert before==tables(path)
    with ro(path) as db:
        first=db.execute("SELECT COUNT(*) FROM event_history WHERE event_type='FIRST_SEEN'").fetchone()[0]
        returns=db.execute("SELECT COUNT(*) FROM event_history WHERE event_type='REAPPEARED'").fetchone()[0]
        assert first==returns==1
        integrity=db.execute('PRAGMA integrity_check').fetchone()[0]
        foreign=[tuple(r) for r in db.execute('PRAGMA foreign_key_check')]
    registry.close()
    result={'status':'PASS','mode':'OFFLINE_TRANSACTION_AND_REGISTRY_REPLAY','simulation_db':str(path),'real_snapshot_run':version['run_id'],
        'source_fact_hash':version['normalized_fact_hash'],'snapshot_sku':'3207872','scenarios':observations,
        'head_change_rejection':'PASS','resume_idempotency':'PASS','first_seen_events':first,'reappeared_events':returns,
        'integrity':integrity,'foreign_keys':foreign,'real_collector_calls':0,'qwen_calls':0,
        'production_approvals_created':0,'fixture_approval_actor':'human:fixture',
        'collection_gate_authorization':False,'future_real_daily_observation':'NOT_PERFORMED',
        'registry_failure_recovery_coverage':'tests/test_repository_closure.py:test_registry_recovery_is_scoped_idempotent_and_preserves_facts'}
    save('10_daily_run_idempotency_report.json',result)
    return result


if __name__=='__main__':
    print(json.dumps({'status':replay()['status']}))

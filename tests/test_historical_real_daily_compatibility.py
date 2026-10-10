"""Six daily requirements through the live Registry/Resolver contract, offline."""
import json
from pathlib import Path

from action_tracker.database.connection import connect
from action_tracker.localization.contracts import SourceFacts
from action_tracker.localization.engine import LocalizationEngine
from action_tracker.localization.qa import guard_translation
from action_tracker.localization.registry.repository import LocalizationRegistry
from action_tracker.localization.resolver import TranslationResolver
from action_tracker.services.hashing import localization_source_hash

CASE = json.loads((Path(__file__).parent / 'fixtures' /
                   'historical_daily_compatibility_20261010.json').read_text(encoding='utf8'))


class NoCallsProvider:
    provider = 'test-call-forbidden'
    model = 'offline'
    def translate(self, request):
        raise AssertionError('Valid approved historical Chinese must not call a provider')


def setup(tmp_path, *, approved=False):
    path = tmp_path / 'daily-registry.sqlite'
    registry = LocalizationRegistry(path)
    sku = CASE['sku']
    with connect(path) as db:
        db.execute('INSERT INTO products(canonical_id,official_sku,status) VALUES(?,?,?)', ('real-case', sku, 'HISTORICAL'))
    record = {'sku':sku, **{unit['source_key']:unit['source'] for unit in CASE['units']}}
    source_fields={key:value for key,value in record.items() if key!='sku'}
    source_id = registry.register_source(sku, source_fields, localization_source_hash(record), observed_at='2026-10-10', source_quality_status='VALID')
    if approved:
        with connect(path) as db:
            units = dict(db.execute('SELECT field_name,unit_id FROM translation_units WHERE source_version_id=?', (source_id,)))
        for unit in CASE['units']:
            qa = guard_translation(SourceFacts.from_record(record), {unit['field']:unit['target']}, (unit['field'],))
            assert qa['status']=='PASS'
            revision = registry.record_revision(unit_id=units[unit['source_key']],target_text=unit['target'],
                provider='real-applied-fixture',model='CODEX',source_hash=localization_source_hash(record),
                request_hash='offline-fixture',response_hash='offline-fixture',
                qa_status='PASS',canonical_qa_status='NOT_REQUIRED',provenance=unit['approval_provenance'])
            assert registry.approve_revision(revision,actor='human:offline-fixture')
    return path,registry,record


def test_real_new_sku_partial_sources_queue_only_missing_available_fields(tmp_path):
    path,registry,record=setup(tmp_path)
    result=registry.ingest_records([record],source_run_id='new',observed_at='2026-10-10')
    assert result['queue_insert_attempts']==3
    with connect(path) as db:
        assert {r[0] for r in db.execute('SELECT field_name FROM translation_units')}=={'cat1_es','cat2_es','spec_es'}
        assert db.execute('SELECT COUNT(*) FROM translation_queue').fetchone()[0]==3


def test_real_existing_approved_spec_reuses_without_provider(tmp_path):
    _,registry,record=setup(tmp_path,approved=True)
    resolver=TranslationResolver(registry=registry,engine=LocalizationEngine(),provider=NoCallsProvider())
    result=resolver.resolve_field(record,'spec',allow_provider=True)
    assert result.approved and result.value==next(u['target'] for u in CASE['units'] if u['field']=='spec')
    assert registry.ingest_records([record],source_run_id='existing',observed_at='2026-10-10')['queue_insert_attempts']==0


def test_real_partial_source_change_queues_only_changed_field(tmp_path):
    path,registry,record=setup(tmp_path,approved=True)
    changed={**record,'spec_es':'6x5,5x5 cm | diferentes variantes'} # Explicit hypothetical source change.
    result=registry.ingest_records([changed],source_run_id='changed',observed_at='2026-10-11')
    assert result['queue_insert_attempts']==1
    with connect(path) as db:
        latest=db.execute('SELECT source_version_id FROM translation_source_versions WHERE source_hash=?',(localization_source_hash(changed),)).fetchone()[0]
        fields={r[0]:(r[1],r[2]) for r in db.execute('SELECT u.field_name,r.target_text,r.review_status FROM translation_units u LEFT JOIN translation_revisions r ON r.revision_id=u.current_revision_id WHERE u.source_version_id=?',(latest,))}
    assert fields['spec_es']==(None,None)
    assert fields['cat1_es'][1]=='APPROVED' and fields['cat2_es'][1]=='APPROVED'


def test_real_returning_historical_sku_keeps_valid_chinese(tmp_path):
    path,registry,record=setup(tmp_path,approved=True)
    with connect(path) as db:
        before=db.execute('SELECT COUNT(*) FROM translation_revisions').fetchone()[0]
        db.execute("UPDATE products SET status='CURRENT' WHERE official_sku=?",(CASE['sku'],))
    assert registry.ingest_records([{**record,'status':'CURRENT'}],source_run_id='return',observed_at='2026-10-11')['queue_insert_attempts']==0
    with connect(path) as db:
        assert db.execute('SELECT COUNT(*) FROM translation_revisions').fetchone()[0]==before


def test_real_same_day_resume_does_not_duplicate_queue_or_approved_revisions(tmp_path):
    path,registry,record=setup(tmp_path,approved=True)
    with connect(path) as db:
        before=db.execute('SELECT COUNT(*) FROM translation_revisions').fetchone()[0]
    for _ in range(3):
        assert registry.ingest_records([record],source_run_id='same-day',observed_at='2026-10-10')['queue_insert_attempts']==0
    with connect(path) as db:
        assert db.execute('SELECT COUNT(*) FROM translation_revisions').fetchone()[0]==before
        assert db.execute('SELECT COUNT(*) FROM translation_provider_calls').fetchone()[0]==0


def test_real_unavailable_source_stays_null_and_has_no_translation_unit(tmp_path):
    path,registry,record=setup(tmp_path)
    unavailable={**record,'spec_es':None,'name':'旧业务中文显示回退值'}
    registry.ingest_records([unavailable],source_run_id='unavailable',observed_at='2026-10-11')
    with connect(path) as db:
        latest=db.execute('SELECT source_version_id,source_fields_json FROM translation_source_versions WHERE source_hash=?',(localization_source_hash(unavailable),)).fetchone()
        assert json.loads(latest[1])['spec_es'] is None
        assert json.loads(latest[1])['name_es'] is None
        assert not db.execute("SELECT 1 FROM translation_units WHERE source_version_id=? AND field_name='spec_es'",(latest[0],)).fetchone()
        assert not db.execute("SELECT 1 FROM translation_units WHERE source_version_id=? AND field_name='name_es'",(latest[0],)).fetchone()
        assert not db.execute('SELECT 1 FROM translation_provider_calls').fetchone()

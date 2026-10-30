"""Retained source lineage uses isolated SQLite and existing writer contracts."""
import json

import pytest

from action_tracker.database.connection import connect
from action_tracker.database.integration import _select_trusted_fields
from action_tracker.database.production import CommitBundle, ProductionWriter
from action_tracker.database.repository import ProductionRepository, _read_fact_lineage
from action_tracker.localization.hashes import value_hash


@pytest.fixture(params=['source_fact_versions', 'product_fact_versions'])
def lineage_database(tmp_path, request):
    path = tmp_path / 'lineage.db'
    if request.param == 'product_fact_versions':
        with connect(path) as db:
            db.execute('''CREATE TABLE product_fact_versions (
                fact_id TEXT PRIMARY KEY, official_sku TEXT NOT NULL, run_id TEXT NOT NULL,
                raw_fact_json TEXT NOT NULL, normalized_fact_json TEXT NOT NULL,
                raw_fact_hash TEXT NOT NULL, normalized_fact_hash TEXT NOT NULL,
                raw_fact_available INTEGER NOT NULL, normalization_version TEXT NOT NULL,
                created_at TEXT NOT NULL, UNIQUE(official_sku,run_id))''')
    writer = ProductionWriter(path, role='SHADOW')
    bundle = CommitBundle(run_id='source', observation_date='2026-10-25', qa_state='PASS',
        current_products=({'sku': '1001', 'canonical_id': 'ACT0001001', 'status': 'CURRENT',
                           'name_es': 'Producto', 'current_price': 2.5},),
        localization_updates=({'sku': '1001', 'language': 'es', 'name': 'Producto', 'cat1': 'Hogar'},),
        source_fact_versions=({'sku': '1001', 'run_id': 'source', 'source_name': 'listing',
                              'observed_at': '2026-10-25', 'facts': {
                                  'name': {'raw': 'Producto', 'normalized': 'Producto'},
                                  'cat1': {'raw': 'Hogar', 'normalized': 'Hogar'}}},),
        observations=({'sku': '1001', 'run_id': 'source', 'observation_date': '2026-10-25',
                       'presence_state': 'PRESENT', 'current_price': 2.5},),
        run_record={'fact_field_provenance': {'1001': {
            'cat1_es': {'state': 'CURRENT_VERIFIED'}, 'current_price': {'state': 'CURRENT_VERIFIED'}}}})
    source_commit = writer.commit(bundle)
    return path, writer, request.param, source_commit


def test_metadata_head_and_retained_snapshot_do_not_refresh_source(lineage_database):
    path, writer, table, source_commit = lineage_database
    before = ProductionRepository(path).load_product_baseline()['1001']['field_source_lineage']
    writer.commit(CommitBundle(run_id='retained', observation_date='2026-10-26', qa_state='PASS',
        source_fact_versions=({'sku': '1001', 'run_id': 'retained', 'source_name': 'listing',
            'observed_at': '2026-10-26', 'facts': {'cat1': {'raw': None, 'normalized': 'Hogar'},
                                                'name': {'raw': 'Producto', 'normalized': 'Producto'}}},),
        run_record={'fact_field_provenance': {'1001': {'cat1_es': {'state': 'HISTORY_RETAINED'}}}}))
    head = writer.commit(CommitBundle(run_id='metadata', observation_date='2026-10-27', qa_state='PASS',
                                    run_record={'operation': 'LOCALIZATION_PATCH_APPLY'}))
    baseline = ProductionRepository(path).load_product_baseline()['1001']
    cat = baseline['field_source_lineage']['cat1_es']
    assert cat == before['cat1_es']
    assert cat['status'] == 'VERIFIED' and cat['source_table'] == table
    assert cat['field_source_run_id'] == 'source'
    assert cat['field_source_commit_id'] == source_commit
    assert cat['field_source_observed_at'] == '2026-10-25'
    assert cat['field_value_hash'] == value_hash('Hogar')
    assert cat['field_raw_hash'] == value_hash('Hogar')
    assert baseline['baseline_head_commit_id'] == head
    assert baseline['baseline_head_run_id'] == 'metadata'
    issue = {'level': 'FIELD_REVIEW', 'sku': '1001', 'field_name': 'cat1_es',
             'reason': 'CATEGORY_SOURCE_MISSING', 'source_hash': value_hash('')}
    selected, receipts, _ = _select_trusted_fields({'1001': {'sku': '1001', 'cat1_es': ''}},
        {'1001': baseline}, [issue], run_id='next', observation_date='2026-10-28')
    assert selected['1001']['cat1_es'] == 'Hogar'
    assert receipts[0]['baseline_head_commit_id'] == head
    assert receipts[0]['field_source'] == cat
    assert selected['1001']['fact_field_provenance']['cat1_es']['retained_from_run_id'] == 'source'
    assert baseline['field_source_lineage']['current_price']['field_source_run_id'] == 'source'
    assert baseline['field_source_lineage']['unit_price']['status'] == 'UNKNOWN'
    assert baseline['field_source_lineage']['original_price']['status'] == 'UNKNOWN'


@pytest.mark.parametrize('fault', ['hash', 'value', 'timestamp'])
def test_uncertain_lineage_is_not_invented(lineage_database, fault):
    path, _, table, _ = lineage_database
    with connect(path) as db:
        if fault == 'hash':
            column = 'normalized_fact_hash' if table == 'product_fact_versions' else 'normalized_hash'
            db.execute(f"UPDATE {table} SET {column}='wrong'")
        elif fault == 'value':
            db.execute("UPDATE product_localizations SET cat1='Cocina' WHERE language='es'")
        else:
            db.execute('DELETE FROM observations')
            if table == 'source_fact_versions':
                db.execute('UPDATE source_fact_versions SET observed_at=NULL')
    baseline = ProductionRepository(path).load_product_baseline()['1001']
    result = baseline['field_source_lineage']['cat1_es']
    if fault == 'timestamp':
        assert result['status'] == 'VERIFIED'
        assert result['field_source_observed_at'] is None
        assert result['field_source_time_precision'] == 'UNKNOWN'
        assert result['field_source_business_date'] == '2026-10-25'
    else:
        assert result['status'] == 'UNKNOWN'
        assert 'field_source_run_id' not in result
    assert baseline['cat1_es'] == ('Cocina' if fault == 'value' else 'Hogar')


def test_legacy_baseline_does_not_claim_global_head_as_field_origin():
    selected, receipts, _ = _select_trusted_fields({'1001': {'sku': '1001', 'cat1_es': ''}},
        {'1001': {'cat1_es': 'Hogar', 'source_run_id': 'metadata', 'source_commit_id': 'head'}},
        [{'level': 'FIELD_REVIEW', 'sku': '1001', 'field_name': 'cat1_es',
          'source_hash': value_hash('')}], run_id='next', observation_date='2026-10-28')
    assert receipts[0]['field_source']['status'] == 'UNKNOWN'
    assert receipts[0]['baseline_head_commit_id'] == 'head'
    assert selected['1001']['fact_field_provenance']['cat1_es']['retained_from_run_id'] is None


def test_reviewed_source_is_not_promoted_to_verified_lineage(lineage_database):
    path, _, _, _ = lineage_database
    with connect(path) as db:
        db.execute('UPDATE run_evidence SET evidence_json=? WHERE run_id=?',
                   (json.dumps({'qa_issues': [{'level': 'FIELD_REVIEW', 'sku': '1001',
                                              'field_name': 'cat1_es'}]}), 'source'))
    result = ProductionRepository(path).load_product_baseline()['1001']
    assert result['cat1_es'] == 'Hogar'
    assert result['field_source_lineage']['cat1_es']['status'] == 'UNKNOWN'


def test_lineage_reads_are_batched(lineage_database):
    path, _, _, _ = lineage_database
    row = [None] * 48
    row[1], row[2], row[16], row[17], row[44] = '1001', 'Producto', 'Producto', 'Hogar', 2.5
    with connect(path) as db:
        queries = []
        db.set_trace_callback(queries.append)
        _read_fact_lineage(db, [row])
        one_count = len(queries)
        queries.clear()
        many = []
        for index in range(100):
            copy = list(row); copy[1] = str(1001 + index); many.append(copy)
        result = _read_fact_lineage(db, many)
        assert len(queries) == one_count
        assert len(result) == 100
        assert result['1002']['cat1_es']['status'] == 'UNKNOWN'


def _body_source_commit(writer, *, run, day, layer, modern=False, text='Texto de detalle'):
    values = {'name': 'Producto', 'cat1': 'Hogar', 'cat2': 'Limpieza', 'spec': '2 piezas',
              'description': text, 'details': 'Material: Plástico'}
    report = ({'fact_field_provenance': {'1001': {'desc_es': {
        'state': 'CURRENT_VERIFIED', 'source': 'DETAIL_CURRENT_RUN'}}}} if modern else {})
    return writer.commit(CommitBundle(run_id=run, observation_date=day, qa_state='PASS',
        localization_updates=({'sku': '1001', 'language': 'es', **values},),
        source_fact_versions=({'sku': '1001', 'run_id': run, 'observed_at': day,
                              'source_name': layer, 'facts': {key: {'raw': value, 'normalized': value}
                                                            for key, value in values.items()}},),
        observations=({'sku': '1001', 'run_id': run, 'observation_date': day,
                       'presence_state': 'PRESENT', 'observation_complete': True,
                       'current_price': 2.5},), run_record=report))


def test_legacy_listing_copied_body_resolves_to_earlier_detail(lineage_database):
    path, writer, _, _ = lineage_database
    old = _body_source_commit(writer, run='real-detail', day='2026-10-26', layer='detail')
    _body_source_commit(writer, run='listing-copy', day='2026-10-27', layer='listing')
    baseline = ProductionRepository(path).load_product_baseline()['1001']
    for field in ('desc_es', 'details_es', 'cat2_es'):
        lineage = baseline['field_source_lineage'][field]
        assert lineage['status'] == 'VERIFIED'
        assert lineage['field_source_run_id'] == 'real-detail'
        assert lineage['field_source_commit_id'] == old
        assert lineage['field_source_observed_at'] == '2026-10-26'
    for field in ('name_es', 'cat1_es', 'spec_es'):
        assert baseline['field_source_lineage'][field]['field_source_run_id'] == 'listing-copy'


def test_modern_field_evidence_is_not_limited_by_legacy_snapshot_layer(lineage_database):
    path, writer, _, _ = lineage_database
    _body_source_commit(writer, run='old-detail', day='2026-10-26', layer='detail')
    _body_source_commit(writer, run='new-explicit', day='2026-10-27', layer='listing',
                        modern=True, text='Texto nuevo explícito')
    result = ProductionRepository(path).load_product_baseline()['1001']['field_source_lineage']['desc_es']
    assert result['status'] == 'VERIFIED' and result['field_source_run_id'] == 'new-explicit'


@pytest.mark.parametrize('mode', ['recovery', 'path_time', 'wrong_hash', 'correction'])
def test_existing_recovery_evidence_remains_a_field_source(lineage_database, mode):
    from action_tracker.services.hashing import localization_field_source_hash
    path, writer, _, _ = lineage_database
    field = 'details' if mode == 'correction' else 'description'
    key = 'details_es' if field == 'details' else 'desc_es'
    text = 'Material estructura: Plástico' if field == 'details' else 'Texto de archivo'
    source = {'text': text, 'field_hash': localization_field_source_hash({key: text}, field),
              'reference': 'synthetic-archive.xlsx::datos', 'file_hash': 'a' * 64,
              'observed_at': 'F:/snapshot/2026-01-09/source' if mode == 'path_time' else '2026-01-09'}
    if mode == 'wrong_hash':
        source['field_hash'] = '0' * 64
    evidence = {'sku': '1001', 'field': field, 'raw': text, 'source': source}
    if mode == 'correction':
        evidence.update(before='Material: estructura: Plástico', after=text)
    else:
        evidence.update(status='VERIFIED', normalized=text)
    operation = 'HISTORICAL_NORMALIZATION_CORRECTION' if mode == 'correction' else 'HISTORICAL_SOURCE_RECOVERY'
    writer.commit(CommitBundle(run_id='restore', observation_date='2026-10-29', qa_state='PASS',
        localization_updates=({'sku': '1001', 'language': 'es', 'name': 'Producto',
                              'cat1': 'Hogar', field: text},),
        run_record={'operation': operation, 'field_evidence': [evidence]}))
    baseline = ProductionRepository(path).load_product_baseline()['1001']
    lineage = baseline['field_source_lineage'][key]
    assert baseline[key] == text
    if mode == 'wrong_hash':
        assert lineage['status'] == 'UNKNOWN'
    else:
        assert lineage['status'] == 'VERIFIED' and lineage['source_table'] == 'run_evidence'
        assert lineage['field_source_run_id'] == 'restore'
        assert lineage['field_value_hash'] == value_hash(text)
        assert lineage['source_artifact_hash'] == 'a' * 64
        assert lineage['source_reference'] == source['reference']
        assert lineage['field_source_observed_at'] == (None if mode == 'path_time' else '2026-01-09')
        assert lineage['field_source_time_precision'] == ('UNKNOWN' if mode == 'path_time' else 'DAY')

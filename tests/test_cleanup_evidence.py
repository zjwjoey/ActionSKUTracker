"""CI_SAFE: evidence parsing and immutable temporary database checks only."""
import json
import sqlite3
from pathlib import Path
from datetime import datetime,timezone

import pytest
from action_tracker.database.schema import migrate_v2
from action_tracker.operations.cleanup_evidence import (REQUIRED_REFERENCE_DOMAINS,extract_backup_records,
    extract_path_references,reference_conclusion,task_completion,inspect_database,hash_pair,legacy_certificate,path_key,
    normalize_restore_proof)
from action_tracker.operations.storage_lifecycle import cleanup_preview
from action_tracker.operations.storage_foundation import StorageError


def test_backup_hash_is_bound_only_to_its_own_object():
    doc={'jobs':[{'backup':'F:/backup/a.sqlite3','restore':'F:/backup/a.restore-check.sqlite3',
         'sha256':'a'*64,'integrity':'ok','foreign_key_violations':[],'logical_parity':True},
         {'backup':'F:/backup/b.sqlite3'}, {'sha256':'b'*64}]}
    rows=extract_backup_records(doc,source='F:/evidence/report.json',source_sha256='c'*64)
    assert len(rows)==1 and rows[0]['backup_sha256']=='a'*64
    assert rows[0]['historical_restore_rehearsed'] and len(rows[0]['evidence_id'])==64


def test_literal_and_embedded_reference_normalization_never_matches_basename():
    targets={path_key('F:/Backup/One.sqlite3')}
    rows=extract_path_references({'exact':'f:\\backup\\one.sqlite3',
        'cmd':'restore "F:/Backup/One.sqlite3" now','different':'F:/else/One.sqlite3',
        'relative':'One.sqlite3','bad':'abc\0def'},targets=targets)
    assert len(rows)==2


def test_no_hits_in_partial_search_never_becomes_zero_references():
    a=reference_conclusion([],{'complete':False,'completed_domains':['configuration']})
    assert a['reference_count'] is None and not a['references_verified']
    full={'complete':True,'completed_domains':sorted(REQUIRED_REFERENCE_DOMAINS),
        'unresolved_dynamic_references':0,'excluded_required_sources':0}
    assert reference_conclusion([],full)['reference_count']==0
    assert reference_conclusion([{'path':'x'}],full)['reference_count']==1
    assert not reference_conclusion([],{**full,'excluded_required_sources':1})['references_verified']


def test_qa_pass_and_other_task_are_not_terminal_ownership():
    assert task_completion({'run_id':'r','status':'PASS'},run_id='r',database_path='F:/old.db')['task_status']=='UNKNOWN'
    d={'run_id':'r','state':'SUCCESS','finished_at':'2026-10-14T00:00:00+00:00',
       'database_path':'F:/old.db','resume_allowed':False}
    assert task_completion(d,run_id='r',database_path='F:/old.db')['task_status']=='COMPLETED'
    assert task_completion(d,run_id='other',database_path='F:/old.db')['task_status']=='UNKNOWN'
    assert task_completion(d,run_id='r',database_path='F:/different.db')['task_status']=='UNKNOWN'
    assert task_completion({**d,'finished_at':'not-a-time'},run_id='r',database_path='F:/old.db')['task_status']=='UNKNOWN'


def test_immutable_old_database_check_never_changes_wal_or_shm(tmp_path):
    source=tmp_path/'source.db';migrate_v2(source,role='PRIMARY')
    writer=sqlite3.connect(source)
    try:
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute('CREATE TABLE marker(value TEXT)');writer.commit()
        wal=Path(str(source)+'-wal');shm=Path(str(source)+'-shm')
        before=(wal.read_bytes(),shm.read_bytes())
        result=inspect_database(source,full_integrity=True)
        assert result['status']=='REVIEW_REQUIRED' and 'WAL' in result['reason']
        assert before==(wal.read_bytes(),shm.read_bytes())
    finally:writer.close()
    result=inspect_database(source,full_integrity=True)
    assert result['current_integrity']=='PASS' and result['current_foreign_keys']=='PASS'


def test_same_content_different_duties_still_blocks(tmp_path):
    source=tmp_path/'a.db';migrate_v2(source,role='PRIMARY')
    other=tmp_path/'b.db';other.write_bytes(source.read_bytes())
    pair=hash_pair(source,other,budget_bytes=8*1024**2)
    assert pair['content_equal'] and pair['unique_business_data_relative_to_backup'] is False
    record=legacy_certificate(inspect_database(source,full_integrity=True),
        task={'task_status':'UNKNOWN'},references=reference_conclusion([],{'complete':False}))
    record.update(sha256=pair['candidate_sha256'],identity_verified=True,
        unique_business_data=False,current_identity_check='PASS')
    result=cleanup_preview([record])
    assert not result['whitelist'] and result['released_bytes']==0
    assert 'RETENTION_POLICY_NOT_VERIFIED' in result['assets'][0]['blockers']
    assert source.is_file() and other.is_file()


def test_live_database_alias_is_identified_without_sql_connection(tmp_path):
    primary=tmp_path/'primary.db';primary.write_bytes(b'not a database; must never open')
    alias=tmp_path/'alias.db';alias.hardlink_to(primary)
    result=inspect_database(alias,full_integrity=True,protected_paths=[primary])
    assert result['status']=='RETAIN_REQUIRED' and result['production_dependency']
    assert result['current_integrity']=='NOT_CHECKED' and primary.read_bytes().startswith(b'not a database')


def test_hash_budget_and_repeat_are_safe(tmp_path):
    a=tmp_path/'a';b=tmp_path/'b';a.write_bytes(b'fact');b.write_bytes(b'fact')
    with pytest.raises(StorageError,match='EVIDENCE_HASH_BUDGET_EXCEEDED'):hash_pair(a,b,budget_bytes=1)
    first=hash_pair(a,b,budget_bytes=8);second=hash_pair(a,b,budget_bytes=8)
    assert first['candidate_sha256']==second['candidate_sha256'] and a.read_bytes()==b'fact'


def test_restore_proof_normalization_is_bound_to_both_actual_database_checks(tmp_path):
    a=tmp_path/'a.db';b=tmp_path/'b.db';migrate_v2(a,role='PRIMARY');b.write_bytes(a.read_bytes())
    pair=hash_pair(b,a,budget_bytes=8*1024**2)
    pair.update(backup_path=str(a),candidate_path=str(b))
    original=inspect_database(a,full_integrity=True);restored=inspect_database(b,full_integrity=True)
    proof=normalize_restore_proof(pair,original,restored)
    assert proof['status']=='PASS' and proof['backup_sha256']==pair['backup_sha256'] and proof['new_database_copies_created']==0
    assert normalize_restore_proof(pair,original,{**restored,'current_foreign_keys':'FAIL'})['status']=='REVIEW_REQUIRED'
    assert normalize_restore_proof({**pair,'content_equal':False},original,restored)['status']=='REVIEW_REQUIRED'


@pytest.mark.parametrize('field,value,blocker',[
    ('current_integrity','FAIL','DATABASE_INTEGRITY_NOT_VERIFIED'),
    ('task_terminal_verified',False,'TASK_OWNERSHIP_TERMINAL_UNKNOWN'),
    ('production_dependency',True,'PRODUCTION_DEPENDENCY_UNKNOWN_OR_PRESENT'),
    ('retention_policy_verified',False,'RETENTION_POLICY_NOT_VERIFIED'),
    ('independent_rollback_obligation',True,'INDEPENDENT_RECOVERY_OBLIGATION_UNKNOWN_OR_PRESENT')])
def test_new_legacy_gates_cannot_be_bypassed_by_old_boolean_fields(tmp_path,field,value,blocker):
    source=tmp_path/'candidate.txt';source.write_bytes(b'fact')
    other=tmp_path/'alternative.txt';other.write_bytes(b'fact')
    from action_tracker.operations.storage_foundation import sha256_file
    sha=sha256_file(source);proof=tmp_path/'proof.json'
    proof.write_text(json.dumps({'status':'PASS','backup_sha256':sha}))
    record={'path':str(source),'size_bytes':4,'sha256':sha,'identity_verified':True,'unique_business_data':False,
        'current_identity_check':'PASS','task_status':'COMPLETED','recovery_required':False,
        'reference_count':0,'references_verified':True,'retain_until':'2020-01-01T00:00:00+00:00',
        'alternative_recovery':{'path':str(other),'sha256':sha,'status':'PASS','evidence_path':str(proof)},
        'evidence_contract':'LEGACY_CLEANUP_EVIDENCE_V1','file_id':'fixture-id','current_integrity':'PASS',
        'current_foreign_keys':'PASS','task_terminal_verified':True,'production_dependency':False,
        'production_dependency_verified':True,'retention_policy_verified':True,
        'recovery_release_verified':True,'independent_rollback_obligation':False}
    assert cleanup_preview([record])['whitelist']
    result=cleanup_preview([{**record,field:value}])
    assert not result['whitelist'] and blocker in result['assets'][0]['blockers']


def test_task_completion_uses_latest_event_not_document_order():
    finished={'run_id':'r','state':'SUCCESS','finished_at':'2020-01-01T01:00:00Z',
              'database_path':'F:/old.db','resume_allowed':False}
    resumed={'run_id':'r','state':'RUNNING','started_at':'2020-01-02T00:00:00Z',
             'database_path':'F:/old.db','resume_allowed':True}
    for events in ([finished,resumed],[resumed,finished]):
        result=task_completion(events,run_id='r',database_path='F:/old.db')
        assert result['task_status']=='RUNNING'
        assert result['matched_events']==2
    later={**finished,'finished_at':'2020-01-03T00:00:00Z'}
    result=task_completion([later,resumed,finished],run_id='r',database_path='F:/old.db')
    assert result['task_status']=='COMPLETED' and result['terminal_verified']


@pytest.mark.parametrize('change',[
    {'resume_allowed':True},{'resume_allowed':None},{'resume_allowed':'false'},
    {'finished_at':'2099-01-01T00:00:00Z'},{'finished_at':'2020-01-01'},
    {'finished_at':'2020-01-01T00:00:00'},
])
def test_terminal_requires_aware_past_time_and_explicit_resume_retirement(change):
    event={'run_id':'r','state':'SUCCESS','finished_at':'2020-01-01T00:00:00Z',
           'database_path':'F:/old.db','resume_allowed':False}
    assert task_completion({**event,**change},run_id='r',database_path='F:/old.db')['task_status']=='UNKNOWN'


def test_task_completion_rejects_ambiguous_latest_or_missing_event_time():
    event={'run_id':'r','state':'SUCCESS','finished_at':'2020-01-01T00:00:00Z',
           'database_path':'F:/old.db','resume_allowed':False}
    ambiguous={**event,'resume_allowed':True}
    assert task_completion([event,ambiguous],run_id='r',database_path='F:/old.db')['reason']=='CONFLICTING_LATEST_TASK_EVENTS'
    running={'run_id':'r','state':'RUNNING','database_path':'F:/old.db'}
    assert task_completion([event,running],run_id='r',database_path='F:/old.db')['task_status']=='UNKNOWN'


def test_certificate_cannot_rebind_terminal_proof_to_other_file():
    event={'run_id':'r','state':'SUCCESS','finished_at':'2020-01-01T00:00:00Z',
           'database_path':'F:/old.db','resume_allowed':False}
    task=task_completion(event,run_id='r',database_path='F:/old.db')
    assert legacy_certificate({'path':'F:/old.db'},task=task,references={})['task_terminal_verified']
    assert not legacy_certificate({'path':'F:/different.db'},task=task,references={})['task_terminal_verified']
    assert not legacy_certificate({'path':'F:/old.db'},task={
        'task_status':'COMPLETED','scope':'EXACT_RUN_AND_DATABASE'},references={})['task_terminal_verified']


def test_missing_restore_evidence_never_normalizes_pass():
    incomplete={'current_integrity':'PASS','current_foreign_keys':'PASS'}
    assert normalize_restore_proof({'content_equal':True},incomplete,incomplete)['status']=='REVIEW_REQUIRED'


@pytest.mark.parametrize('mutation',[
    'missing_hash','malformed_hash','wrong_path','missing_path','missing_schema',
    'unknown_schema','unknown_role','missing_commit_key','changed_signature','different_file_id',
    'same_file_id','future_verification','missing_method','non_verified_inspection',
    'incomplete_source_signature',
])
def test_restore_proof_rejects_unbound_or_incomplete_evidence(tmp_path,mutation):
    a=tmp_path/'original.db';b=tmp_path/'restored.db'
    migrate_v2(a,role='PRIMARY');b.write_bytes(a.read_bytes())
    pair=hash_pair(b,a,budget_bytes=8*1024**2)
    original=inspect_database(a,full_integrity=True)
    restored=inspect_database(b,full_integrity=True)
    assert normalize_restore_proof(pair,original,restored)['status']=='PASS'
    if mutation=='missing_hash':pair.pop('backup_sha256')
    elif mutation=='malformed_hash':pair.update(backup_sha256='x'*64,candidate_sha256='x'*64)
    elif mutation=='wrong_path':pair['backup_path']=str(tmp_path/'unrelated.db')
    elif mutation=='missing_path':pair.pop('candidate_path')
    elif mutation=='missing_schema':original['database_identity']={};restored['database_identity']={}
    elif mutation=='unknown_schema':
        original['database_identity']['metadata']['schema_version']='unknown'
        restored['database_identity']=original['database_identity']
    elif mutation=='unknown_role':
        original['database_identity']['metadata']['database_role']='unknown'
        restored['database_identity']=original['database_identity']
    elif mutation=='missing_commit_key':
        original['database_identity'].pop('commit_id');restored['database_identity']=original['database_identity']
    elif mutation=='changed_signature':pair['signatures']=[{},{}]
    elif mutation=='different_file_id':restored['file_id']='replacement'
    elif mutation=='same_file_id':
        restored['file_id']=original['file_id'];pair['file_ids']=[original['file_id']]*2
    elif mutation=='future_verification':pair['verified_at']='2099-01-01T00:00:00Z'
    elif mutation=='missing_method':pair.pop('method')
    elif mutation=='non_verified_inspection':restored['status']='REVIEW_REQUIRED'
    elif mutation=='incomplete_source_signature':
        original['source_signature']={'main':None};restored['source_signature']={'main':None}
        pair['signatures']=[restored['source_signature'],original['source_signature']]
    assert normalize_restore_proof(pair,original,restored)['status']=='REVIEW_REQUIRED'


def test_hash_pair_rejects_same_file_and_hardlink(tmp_path):
    a=tmp_path/'a';a.write_bytes(b'fixture')
    b=tmp_path/'b';b.hardlink_to(a)
    for alternative in (a,b):
        with pytest.raises(StorageError,match='ALTERNATIVE_RECOVERY_IS_CANDIDATE'):
            hash_pair(a,alternative,budget_bytes=100)

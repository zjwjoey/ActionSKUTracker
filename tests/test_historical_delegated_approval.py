import copy
import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from action_tracker.database.connection import connect
from action_tracker.database.production import CommitBundle, ProductionWriter, apply_approved_localization_patches
from action_tracker.knowledge.storage import KnowledgeStore
from action_tracker.localization.delegated_approval import ACTOR, VERSION
from action_tracker.localization.hashes import value_hash
from action_tracker.localization.registry.repository import LocalizationRegistry
from action_tracker.services.hashing import localization_source_hash


def fixture(tmp_path):
    path = tmp_path / "primary.db"
    facts = {"name_es": "Producto", "cat1_es": "Hogar", "cat2_es": "Cajas", "spec_es": "2 unidades", "desc_es": "Texto", "details_es": "Material: plástico"}
    head = ProductionWriter(path, role="PRIMARY").commit(CommitBundle(run_id="seed", observation_date="2026-10-09", qa_state="PASS",
        current_products=({"sku": "1001", "name_es": "Producto", "status": "HISTORICAL", "current_price": 2.5},),
        localization_updates=({"sku": "1001", "language": "es", **dict(zip(("name", "cat1", "cat2", "spec", "description", "details"), facts.values()))},
                              {"sku": "1001", "language": "zh", "name": "旧名"})))
    registry = LocalizationRegistry(path, role="PRIMARY")
    source = localization_source_hash(facts)
    registry.register_source("1001", facts, source, observed_at="2026-09-01")
    revision = registry.record_revision_for_sku("1001", "name", "商品", source_hash=source, provider="review", repair_reason="reviewed", qa_status="PASS")
    evidence = tmp_path / "review.json"; evidence.write_text('reviewed source and target', encoding="utf-8")
    grant = {"contract": VERSION, "actor": ACTOR, "authorization_ref": "test:owner-confirmation", "authorization_text": "delegate historical reviewed fields",
             "expires_at": (datetime.now(timezone.utc)+timedelta(days=1)).isoformat(),
             "reviews": {revision: {"sku": "1001", "field": "name", "source_hash": source,
                "target_hash": value_hash("商品"), "semantic_status": "PASS", "evidence_ref": str(evidence),
                "source_evidence_status": "VERIFIED",
                "evidence_sha256": hashlib.sha256(evidence.read_bytes()).hexdigest()}}}
    return path, registry, head, revision, grant


def test_delegated_approval_stage_apply_preserves_service_identity(tmp_path):
    path, registry, head, revision, grant = fixture(tmp_path)
    assert registry.approve_revision_delegated(revision, actor=ACTOR, grant=grant)["status"] == "APPROVED"
    assert registry.approve_revision_delegated(revision, actor=ACTOR, grant=grant)["status"] == "ALREADY_APPROVED"
    store = KnowledgeStore(path, role="PRIMARY")
    args = dict(expected_base_commit_id=head, actor=ACTOR, revision_ids=[revision], delegated_approval=grant)
    first = store.stage_approved_registry_patches(**args)
    assert store.stage_approved_registry_patches(**args)["patch_ids"] == first["patch_ids"]
    result = apply_approved_localization_patches(path, patch_ids=first["patch_ids"], expected_base_commit_id=head, actor="service:apply", delegated_approval=grant)
    assert result["applied_fields"] == 1
    with connect(path) as db:
        assert db.execute("SELECT approved_by FROM localization_fields WHERE official_sku='1001' AND language='zh' AND field_name='name'").fetchone()[0] == ACTOR
        assert db.execute("SELECT current_price,status FROM products WHERE official_sku='1001'").fetchone()[:] == (2.5, "HISTORICAL")
        assert db.execute("SELECT COUNT(*) FROM translation_revision_events WHERE event_type='OWNER_DELEGATED_APPROVED'").fetchone()[0] == 1


@pytest.mark.parametrize("mutation,error", [
    ("current", "HISTORICAL_SCOPE"), ("target", "REVIEW_BINDING"),
    ("qa", "QA_OR_FRESHNESS"), ("fresh", "QA_OR_FRESHNESS"),
    ("expired", "EXPIRED"), ("actor", "INVALID"), ("evidence", "EVIDENCE_HASH"),
    ("source", "CURRENT_SOURCE"), ("blocker", "OPEN_QA_BLOCKER"),
])
def test_delegation_fail_closed(tmp_path, mutation, error):
    path, registry, _, revision, grant = fixture(tmp_path)
    with connect(path) as db:
        if mutation == "current": db.execute("UPDATE products SET status='CURRENT'")
        if mutation == "target": grant["reviews"][revision]["target_hash"] = "wrong"
        if mutation == "qa": db.execute("UPDATE translation_revisions SET qa_status='FAIL'")
        if mutation == "fresh": db.execute("UPDATE translation_units SET freshness_status='STALE'")
        if mutation == "source": db.execute("UPDATE product_localizations SET name='Otro' WHERE language='es'")
    if mutation == "expired": grant["expires_at"] = "2000-01-01T00:00:00+00:00"
    if mutation == "actor": grant["actor"] = "service:*"
    if mutation == "evidence": grant["reviews"][revision]["evidence_sha256"] = "wrong"
    if mutation == "blocker": registry.record_finding(revision, rule_id="TEST", severity="BLOCKER", evidence={})
    with pytest.raises(PermissionError, match=error):
        registry.approve_revision_delegated(revision, actor=ACTOR, grant=grant)


def test_apply_rejects_changed_grant_without_modifying_chinese(tmp_path):
    path, registry, head, revision, grant = fixture(tmp_path)
    registry.approve_revision_delegated(revision, actor=ACTOR, grant=grant)
    staged = KnowledgeStore(path, role="PRIMARY").stage_approved_registry_patches(expected_base_commit_id=head, actor=ACTOR, revision_ids=[revision], delegated_approval=grant)
    altered = copy.deepcopy(grant); altered["authorization_ref"] = "different"
    with pytest.raises(Exception, match="DELEGATION_PATCH_BINDING_MISMATCH"):
        apply_approved_localization_patches(path, patch_ids=staged["patch_ids"], expected_base_commit_id=head, delegated_approval=altered)
    with connect(path) as db:
        assert db.execute("SELECT name FROM product_localizations WHERE language='zh'").fetchone()[0] == "旧名"


def test_pilot_noop_requires_approved_ready_projection(tmp_path):
    import runpy
    from pathlib import Path
    from action_tracker.database.repository import ProductionRepository
    ready = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/run_historical_localization_pilot.py"))["already_ready"]
    path, registry, head, revision, grant = fixture(tmp_path)
    record = ProductionRepository(path).load_current_export_records(include_non_current=True)[0]
    record["name_zh"] = "商品"
    assert not ready(record, path, "name", "商品")
    registry.approve_revision_delegated(revision, actor=ACTOR, grant=grant)
    staged = KnowledgeStore(path, role="PRIMARY").stage_approved_registry_patches(expected_base_commit_id=head, actor=ACTOR, revision_ids=[revision], delegated_approval=grant)
    apply_approved_localization_patches(path, patch_ids=staged["patch_ids"], expected_base_commit_id=head, delegated_approval=grant)
    record = ProductionRepository(path).load_current_export_records(include_non_current=True)[0]
    assert ready(record, path, "name", "商品")
    record["zh_field_provenance"]["name"]["freshness_status"] = "STALE"
    assert not ready(record, path, "name", "商品")


def test_retained_real_chinese_can_restore_binding_without_retranslation_or_text_change(tmp_path):
    import json, runpy
    from pathlib import Path
    from action_tracker.database.repository import ProductionRepository
    row = next(r for r in json.loads((Path(__file__).parent / "fixtures/historical_quality_review_20261010.json").read_text("utf8")) if r["sku"] == "3211913")
    facts = {"name_es": row["source"], "cat1_es": None, "cat2_es": None, "spec_es": None, "desc_es": None, "details_es": None}
    db = tmp_path / 'primary.db'
    head = ProductionWriter(db, role="PRIMARY").commit(CommitBundle(run_id="real-keep", observation_date="2026-10-10", qa_state="PASS",
        current_products=({"sku": row['sku'], "name_es": row['source'], "status": "HISTORICAL", "current_price": 1.25},),
        localization_updates=({"sku": row['sku'], "language": "es", "name": row['source']}, {"sku": row['sku'], "language": "zh", "name": row['target']})))
    registry = LocalizationRegistry(db, role='PRIMARY'); source_hash = localization_source_hash(facts)
    registry.register_source(row['sku'], facts, source_hash, observed_at='2026-10-10')
    rid = registry.record_revision_for_sku(row['sku'], 'name', row['target'], source_hash=source_hash, provider='independent_review', repair_reason='retained_real_text', qa_status='PASS')
    evidence = tmp_path / 'review.json'; evidence.write_text(json.dumps(row, ensure_ascii=False), 'utf8')
    grant = {'contract': VERSION, 'actor': ACTOR, 'authorization_ref': 'test:existing-owner-delegation', 'authorization_text': 'review and retain historical text',
        'expires_at': (datetime.now(timezone.utc)+timedelta(days=1)).isoformat(), 'reviews': {rid: {'sku': row['sku'], 'field': 'name', 'source_hash': source_hash,
        'target_hash': value_hash(row['target']), 'semantic_status': 'PASS', 'source_evidence_status': 'VERIFIED', 'evidence_ref': str(evidence), 'evidence_sha256': hashlib.sha256(evidence.read_bytes()).hexdigest()}}}
    ready = runpy.run_path(str(Path(__file__).resolve().parents[1]/'scripts/run_historical_localization_pilot.py'))['already_ready']
    record = ProductionRepository(db).load_current_export_records(include_non_current=True)[0]
    assert not ready(record, db, 'name', row['target'])
    registry.approve_revision_delegated(rid, actor=ACTOR, grant=grant)
    store = KnowledgeStore(db, role='PRIMARY'); args = dict(expected_base_commit_id=head, actor=ACTOR, revision_ids=[rid], delegated_approval=grant)
    assert not store.stage_approved_registry_patches(**args)['patch_ids']
    staged = store.stage_approved_registry_patches(**args, include_noop_rebinds=True)
    assert len(staged['patch_ids']) == 1
    assert store.stage_approved_registry_patches(**args, include_noop_rebinds=True)['patch_ids'] == staged['patch_ids']
    apply_approved_localization_patches(db, patch_ids=staged['patch_ids'], expected_base_commit_id=head, delegated_approval=grant)
    record = ProductionRepository(db).load_current_export_records(include_non_current=True)[0]
    assert record['name_zh'] == row['target'] and ready(record, db, 'name', row['target'])
    with connect(db) as cx:
        assert cx.execute("SELECT current_price,status FROM products WHERE official_sku=?", (row['sku'],)).fetchone()[:] == (1.25, 'HISTORICAL')


@pytest.mark.parametrize("field,target", [("name","商品"),("cat1","家居"),("cat2","箱子"),
    ("spec","2 件"),("description","文字"),("details","材质：塑料")])
def test_all_canonical_fields_delegated_apply_and_resume(tmp_path, field, target):
    import runpy
    from pathlib import Path
    from action_tracker.database.repository import ProductionRepository
    ready = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/run_historical_localization_pilot.py"))["already_ready"]
    path, registry, head, _, grant = fixture(tmp_path)
    review = copy.deepcopy(next(iter(grant["reviews"].values())))
    rid = registry.record_revision_for_sku("1001",field,target,source_hash=review["source_hash"],provider="review",repair_reason="reviewed",qa_status="PASS")
    review.update(field=field,target_hash=value_hash(target)); grant["reviews"]={rid:review}
    registry.approve_revision_delegated(rid,actor=ACTOR,grant=grant)
    staged=KnowledgeStore(path,role="PRIMARY").stage_approved_registry_patches(expected_base_commit_id=head,actor=ACTOR,revision_ids=[rid],delegated_approval=grant)
    apply_approved_localization_patches(path,patch_ids=staged["patch_ids"],expected_base_commit_id=head,delegated_approval=grant)
    record=ProductionRepository(path).load_current_export_records(include_non_current=True)[0]
    assert ready(record,path,field,target)
    registry.record_finding(rid,rule_id="NEW_BLOCKER",severity="BLOCKER",evidence={})
    assert not ready(record,path,field,target)


def test_historical_source_hydration_does_not_guess_missing_name(tmp_path):
    import runpy
    from pathlib import Path
    from action_tracker.database.repository import ProductionRepository
    hydrate=runpy.run_path(str(Path(__file__).resolve().parents[1]/"scripts/run_historical_localization_pilot.py"))["historical_records"]
    path,_,_,_,_=fixture(tmp_path)
    with connect(path) as db:
        db.execute("UPDATE product_localizations SET name=NULL WHERE language='es'")
        db.execute("UPDATE products SET name_es='Unverified business fallback'")
    record=hydrate(ProductionRepository(path),path)["1001"]
    assert record["name_es"] is None
    assert record["source_hash"]==localization_source_hash({key:record[key] for key in ("name_es","cat1_es","cat2_es","spec_es","desc_es","details_es")})


def test_historical_chinese_display_fallback_is_not_applied_localization(tmp_path):
    import runpy
    from pathlib import Path
    from action_tracker.database.repository import ProductionRepository
    from action_tracker.localization.history_audit import historical_freshness_status
    hydrate=runpy.run_path(str(Path(__file__).resolve().parents[1]/'scripts/run_historical_localization_pilot.py'))['historical_records']
    path,_,_,_,_=fixture(tmp_path)
    with connect(path) as db:
        db.execute("UPDATE product_localizations SET name=NULL WHERE language='zh'")
        db.execute("UPDATE products SET name_zh='旧业务中文'")
    repo=ProductionRepository(path)
    assert repo.load_current_export_records(include_non_current=True)[0]['name_zh']=='旧业务中文'
    assert hydrate(repo,path)['1001']['name_zh'] is None
    assert historical_freshness_status({},True)=='NO_FRESHNESS_STATUS'
    assert historical_freshness_status({},False)=='NO_LOCALIZATION'
    assert historical_freshness_status({'zh_freshness_status':'STALE'},True)=='STALE'
def test_provider_candidate_cannot_become_semantic_approval():
    import runpy
    from pathlib import Path
    gate = runpy.run_path(str(Path(__file__).resolve().parents[1] /
                             "scripts/run_historical_localization_pilot.py"))["reviewed_write_status"]
    candidate = {"decision": "CORRECTED", "semantic_status": "PASS", "provider": "qwen_mt"}
    assert gate(candidate) == "SEMANTIC_REVIEW_REQUIRED"
    reviewed = {**candidate, "review_model": "CODEX", "risk_level": "MEDIUM",
                "review_note": "Compared this field with the retained Spanish evidence."}
    assert gate(reviewed) == "READY"
    assert gate({**reviewed, "risk_level": "HIGH"}) == "OWNER_REVIEW_REQUIRED"
    assert gate({**reviewed, "semantic_status": "PENDING"}) == "SEMANTIC_REVIEW_REQUIRED"
    assert gate({**reviewed, "review_note": ""}) == "SEMANTIC_REVIEW_REQUIRED"


def test_reviewed_manifest_cannot_overwrite_a_later_chinese_correction():
    import runpy
    from pathlib import Path
    gate = runpy.run_path(str(Path(__file__).resolve().parents[1] /
                             "scripts/run_historical_localization_pilot.py"))["reviewed_target_status"]
    assert gate({"before": None}, None) == "READY"
    assert gate({"before": "旧西语残留"}, "旧西语残留") == "READY"
    assert gate({"before": "旧西语残留"}, "人工纠正值") == "TARGET_CHANGED_REVIEW_REQUIRED"
    assert gate({"before": None}, "人工纠正值") == "TARGET_CHANGED_REVIEW_REQUIRED"
    assert gate({}, None) == "TARGET_BASELINE_REVIEW_REQUIRED"

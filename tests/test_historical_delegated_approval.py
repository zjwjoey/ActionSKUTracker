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

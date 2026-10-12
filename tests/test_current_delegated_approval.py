import copy

import pytest

from test_historical_delegated_approval import fixture
from action_tracker.database.connection import connect
from action_tracker.database.production import apply_approved_localization_patches
from action_tracker.knowledge.storage import KnowledgeStore
from action_tracker.localization.delegated_approval import CURRENT_ACTOR, CURRENT_VERSION


def current_fixture(tmp_path):
    path, registry, head, revision, grant = fixture(tmp_path)
    with connect(path) as db:
        db.execute("UPDATE products SET status='CURRENT'")
        db.execute("INSERT OR REPLACE INTO observations(run_id,official_sku,observation_date,presence_state,listing_present,observation_complete) VALUES('seed','1001','2026-10-09','PRESENT',1,1)")
    grant.update(contract=CURRENT_VERSION, actor=CURRENT_ACTOR,
                 collection_run_id="seed", business_date="2026-10-09", current_skus=["1001"])
    return path, registry, head, revision, grant


def test_current_delegation_approval_and_apply_are_scoped_and_idempotent(tmp_path):
    path, registry, head, revision, grant = current_fixture(tmp_path)
    assert registry.approve_revision_delegated(revision, actor=CURRENT_ACTOR, grant=grant)["status"] == "APPROVED"
    assert registry.approve_revision_delegated(revision, actor=CURRENT_ACTOR, grant=grant)["status"] == "ALREADY_APPROVED"
    staged = KnowledgeStore(path, role="PRIMARY").stage_approved_registry_patches(
        expected_base_commit_id=head, actor=CURRENT_ACTOR, revision_ids=[revision], delegated_approval=grant)
    result = apply_approved_localization_patches(path, patch_ids=staged["patch_ids"],
        expected_base_commit_id=head, actor="service:current-localization-apply", delegated_approval=grant)
    assert result["applied_fields"] == 1
    with connect(path) as db:
        assert tuple(db.execute("SELECT current_price,status FROM products WHERE official_sku='1001'").fetchone()) == (2.5, "CURRENT")
        assert db.execute("SELECT name FROM product_localizations WHERE language='es'").fetchone()[0] == "Producto"
        assert db.execute("SELECT approved_by FROM localization_fields WHERE field_name='name' AND language='zh'").fetchone()[0] == CURRENT_ACTOR


@pytest.mark.parametrize("mutation", ["historical", "sku_scope", "date", "run", "not_present", "not_listing", "incomplete", "failed_qa", "dry_run"])
def test_current_delegation_rejects_invalid_observation_and_scope(tmp_path, mutation):
    path, registry, _, revision, grant = current_fixture(tmp_path)
    grant = copy.deepcopy(grant)
    with connect(path) as db:
        if mutation == "historical": db.execute("UPDATE products SET status='HISTORICAL'")
        if mutation == "sku_scope": grant["current_skus"] = ["9999"]
        if mutation == "date": grant["business_date"] = "2026-10-10"
        if mutation == "run": grant["collection_run_id"] = "missing"
        if mutation == "not_present": db.execute("UPDATE observations SET presence_state='ABSENT'")
        if mutation == "not_listing": db.execute("UPDATE observations SET listing_present=0")
        if mutation == "incomplete": db.execute("UPDATE observations SET observation_complete=0")
        if mutation == "failed_qa": db.execute("UPDATE runs SET qa_state='FAIL'")
        if mutation == "dry_run": db.execute("UPDATE runs SET dry_run=1")
    with pytest.raises(PermissionError, match="DELEGATION_CURRENT"):
        registry.approve_revision_delegated(revision, actor=CURRENT_ACTOR, grant=grant)


def test_current_grant_cannot_use_historical_identity(tmp_path):
    _, registry, _, revision, grant = current_fixture(tmp_path)
    with pytest.raises(PermissionError, match="DELEGATION_INVALID"):
        registry.approve_revision_delegated(revision, actor="service:historical-localization-review", grant=grant)


def test_delegated_freshness_restore_requires_matching_current_source(tmp_path):
    path, registry, _, revision, grant = current_fixture(tmp_path)
    with connect(path) as db:
        db.execute("UPDATE translation_units SET freshness_status='STALE'")
    with pytest.raises(PermissionError, match="QA_OR_FRESHNESS"):
        registry.approve_revision_delegated(revision, actor=CURRENT_ACTOR, grant=grant)
    assert registry.restore_unit_freshness_delegated(revision, actor=CURRENT_ACTOR, grant=grant)["status"] == "FRESH"
    assert registry.restore_unit_freshness_delegated(revision, actor=CURRENT_ACTOR, grant=grant)["status"] == "ALREADY_FRESH"
    with connect(path) as db:
        db.execute("UPDATE translation_units SET freshness_status='STALE'")
        db.execute("UPDATE product_localizations SET name='Otro' WHERE language='es'")
    with pytest.raises(PermissionError, match="CURRENT_SOURCE"):
        registry.restore_unit_freshness_delegated(revision, actor=CURRENT_ACTOR, grant=grant)

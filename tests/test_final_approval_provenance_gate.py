from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from action_tracker.database.category_backlog import CategoryBacklogError, decide_category_backlog, enqueue_category_missing
from action_tracker.database.connection import connect
from action_tracker.database.immutable_patches import append_patch_event, create_localization_patch, patch_status
from action_tracker.database.production import ProductionDatabaseError, apply_approved_localization_patches, apply_localization_correction
from action_tracker.database.schema import migrate_v2
from action_tracker.knowledge.storage import KnowledgeStore
from action_tracker.localization.release_gate import audit_research_release
from action_tracker.services.hashing import localization_source_hash


FIELDS = ("name", "cat1", "cat2", "spec", "description", "details")


def _primary(tmp_path: Path) -> tuple[Path, str]:
    path = tmp_path / "primary.sqlite"
    migrate_v2(path, role="PRIMARY")
    es = {
        "name_es": "Producto", "cat1_es": "Hogar", "cat2_es": "Cajas",
        "spec_es": "2 unidades", "desc_es": "Descripción", "details_es": "Número del artículo: 1001",
    }
    source = localization_source_hash(es)
    with connect(path) as db:
        db.execute("INSERT INTO products(canonical_id,official_sku,status) VALUES('ACT1001','1001','CURRENT')")
        db.execute(
            "INSERT INTO product_localizations(official_sku,language,name,cat1,cat2,spec,description,details,updated_at,source_hash) VALUES(?,?,?,?,?,?,?,?,?,?)",
            ("1001", "es", "Producto", "Hogar", "Cajas", "2 unidades", "Descripción", "Número del artículo: 1001", "now", source),
        )
        db.execute(
            "INSERT INTO product_localizations(official_sku,language,name,cat1,cat2,spec,description,details,updated_at,source_hash) VALUES(?,?,?,?,?,?,?,?,?,?)",
            ("1001", "zh", "旧名", "家居", "收纳", "2件", "旧描述", "旧详情", "now", source),
        )
        db.execute("INSERT INTO runs(run_id,run_date,status,qa_state,dry_run,started_at,ended_at,schema_version) VALUES('base','2026-09-09','COMMITTED','PASS',0,'now','now','2.0.0')")
        db.execute("INSERT INTO commit_batches(commit_id,run_id,bundle_hash,schema_version,started_at,committed_at,status) VALUES('C1','base','h','2.0.0','now','now','COMMITTED')")
    return path, source


def _patch(path: Path, source: str, *, patch_id: str = "p1", allowlist=("MANUAL",), approval_source="MANUAL") -> str:
    create_localization_patch(
        path, patch_id=patch_id, official_sku="1001", language="zh", field_name="name",
        old_value="旧名", new_value="新名", source_hash=source, source_allowlist=allowlist,
        created_by="human:creator", evidence={"field_name": "name", "base_commit_id": "C1"},
    )
    append_patch_event(
        path, patch_id=patch_id, event_type="PATCH_APPROVED", actor="human:reviewer-a",
        evidence={"field_name": "name", "base_commit_id": "C1", "source_name": approval_source},
    )
    return patch_id


def test_compatibility_prepare_cannot_auto_approve_or_apply(tmp_path: Path):
    path, source = _primary(tmp_path)
    result = apply_localization_correction(
        path, run_id="legacy", localizations_by_sku={"1001": {"name": "候选名"}}, source_hashes={"1001": source},
    )
    assert result["status"] == "PATCHES_CREATED"
    assert result["prepared_fields"] == 1
    assert patch_status(path, result["patch_ids"][0]) == "PATCH_CREATED"
    with connect(path) as db:
        assert db.execute("SELECT name FROM product_localizations WHERE official_sku='1001' AND language='zh'").fetchone()[0] == "旧名"
        assert db.execute("SELECT COUNT(*) FROM commit_batches").fetchone()[0] == 1


def _candidate(source: str, **extra):
    value = {
        "sku": "1001", "source_hash": source, "fields": {"name": "新名"},
        "provenance": "MANUAL", "validation_status": "PASS",
    }
    value.update(extra)
    return value


def test_knowledge_apply_fails_closed_for_missing_and_pending_approval(tmp_path: Path):
    path, source = _primary(tmp_path)
    store = KnowledgeStore(path, role="PRIMARY")
    record = {"sku": "1001", "name_es": "Producto", "cat1_es": "Hogar", "cat2_es": "Cajas", "spec_es": "2 unidades", "desc_es": "Descripción", "details_es": "Número del artículo: 1001"}
    with pytest.raises(PermissionError, match="CANDIDATE_APPROVAL_STATUS_MISSING"):
        store.apply_localizations([_candidate(source)], {"1001": record}, enabled=True, expected_base_commit_id="C1")
    with pytest.raises(PermissionError, match="CANDIDATE_NOT_APPROVED"):
        store.apply_localizations([_candidate(source, approval_status="PENDING")], {"1001": record}, enabled=True, expected_base_commit_id="C1")


def test_approval_actor_is_not_apply_actor(tmp_path: Path):
    path, source = _primary(tmp_path)
    _patch(path, source)
    result = apply_approved_localization_patches(path, patch_ids=["p1"], expected_base_commit_id="C1", actor="service:localization-apply")
    assert result["applied_fields"] == 1
    with connect(path) as db:
        approved_by = db.execute("SELECT approved_by FROM localization_fields WHERE official_sku='1001' AND field_name='name'").fetchone()[0]
        applied_actor = db.execute("SELECT actor FROM localization_patch_events WHERE patch_id='p1' AND event_type='PATCH_APPLIED'").fetchone()[0]
    assert approved_by == "human:reviewer-a"
    assert applied_actor == "service:localization-apply"


def test_production_apply_enforces_source_allowlist_and_rolls_back(tmp_path: Path):
    path, source = _primary(tmp_path)
    _patch(path, source, allowlist=("MANUAL",), approval_source="MODEL")
    with pytest.raises(ProductionDatabaseError, match="PATCH_SOURCE_NOT_ALLOWED"):
        apply_approved_localization_patches(path, patch_ids=["p1"], expected_base_commit_id="C1", actor="service:localization-apply")
    with connect(path) as db:
        assert db.execute("SELECT name FROM product_localizations WHERE official_sku='1001' AND language='zh'").fetchone()[0] == "旧名"
        assert db.execute("SELECT COUNT(*) FROM commit_batches").fetchone()[0] == 1


def test_production_apply_allowlist_success(tmp_path: Path):
    path, source = _primary(tmp_path)
    _patch(path, source)
    assert apply_approved_localization_patches(path, patch_ids=["p1"], expected_base_commit_id="C1", actor="service:localization-apply")["applied_fields"] == 1


def test_category_hash_is_required_and_fresh(tmp_path: Path):
    path = tmp_path / "category.sqlite"
    migrate_v2(path)
    with connect(path) as db:
        db.execute("INSERT INTO products(canonical_id,official_sku,status) VALUES('c1','1001','CURRENT')")
    enqueue_category_missing(path, queue_id="q1", official_sku="1001", cat1_es="Hogar", cat2_es="Muebles", source_hash="h1")
    with pytest.raises(CategoryBacklogError, match="CATEGORY_CURRENT_SOURCE_HASH_REQUIRED"):
        decide_category_backlog(path, queue_id="q1", decision="APPROVED", value="家具", actor="human:reviewer", evidence_url="https://www.action.com/es-es/p/1001/")
    with pytest.raises(CategoryBacklogError, match="CATEGORY_EVIDENCE_STALE"):
        decide_category_backlog(path, queue_id="q1", decision="APPROVED", value="家具", actor="human:reviewer", evidence_url="https://www.action.com/es-es/p/1001/", current_source_hash="old")
    assert decide_category_backlog(path, queue_id="q1", decision="APPROVED", value="家具", actor="human:reviewer", evidence_url="https://www.action.com/es-es/p/1001/", current_source_hash="h1") == "APPROVED"


def _release_row(**overrides):
    row = {
        "sku": "1001", "name_es": "Caja", "cat1_es": "Hogar", "cat2_es": "Cajas", "spec_es": "2 unidades", "desc_es": "Caja útil", "details_es": "Número del artículo: 1001",
        "name_zh": "收纳盒", "cat1_zh": "家居布置", "cat2_zh": "收纳用品", "spec_zh": "2件", "desc_zh": "实用收纳盒", "details_zh": "商品编号：1001",
        "zh_review_status": "VERIFIED", "zh_freshness_status": "CURRENT",
    }
    row.update(overrides)
    row["zh_source_hash"] = localization_source_hash(row)
    row.setdefault("zh_field_provenance", {field: {"review_status": "VERIFIED", "freshness_status": "CURRENT", "source_hash": row["zh_source_hash"]} for field in FIELDS})
    return row


def test_release_rejects_one_stale_field_hash_and_absent_stale_hash():
    row = _release_row()
    row["zh_field_provenance"]["description"]["source_hash"] = "old"
    result = audit_research_release([row])
    assert not result.ok
    assert "SOURCE_HASH_MISMATCH:1001:description" in result.issues
    row = _release_row(cat2_zh="")
    row["zh_field_provenance"]["cat2"]["review_status"] = "APPROVED_SOURCE_ABSENT"
    row["zh_field_provenance"]["cat2"]["source_hash"] = "old"
    assert "SOURCE_HASH_MISMATCH:1001:cat2" in audit_research_release([row]).issues


def test_release_field_hashes_all_current_pass():
    assert audit_research_release([_release_row()]).ok


def test_exception_source_binding_and_ttl_are_fail_closed():
    row = _release_row(cat2_zh="")
    now = datetime.now(timezone.utc)
    base = {
        "issue_id": "UNAPPROVED_ZH:1001:cat2_zh", "issue_type": "UNAPPROVED_ZH",
        "official_sku": "1001", "field_name": "cat2_zh", "source_hash": row["zh_source_hash"],
        "approved_by": "human:reviewer", "approved_at": now.isoformat(), "created_at": now.isoformat(),
        "expires_at": (now.date()).isoformat(), "reason": "officially absent", "evidence": "official page",
    }
    assert audit_research_release([row], explicit_exceptions=[base]).ok
    stale = dict(base, source_hash="old")
    assert not audit_research_release([row], explicit_exceptions=[stale]).ok
    long = dict(base, expires_at=(now.date()).replace(year=now.year + 1).isoformat())
    assert not audit_research_release([row], explicit_exceptions=[long]).ok


def test_ci_allowlist_covers_all_test_modules():
    root = Path(__file__).parent
    modules = {f"tests/{path.name}" for path in root.glob("test_*.py")}
    allowlisted = {
        line.strip() for line in (root / "ci_safe_tests.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert modules <= allowlisted

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from action_tracker.localization.repair_service import (
    RepairError,
    build_preview,
    policy_hash,
    value_hash,
)
from action_tracker.services.hashing import localization_field_source_hash
from action_tracker.database.connection import connect
from action_tracker.database.schema import migrate_v2
from action_tracker.localization.repair_service import apply_preview_to_database, rollback_database, verify_database_apply


def _record():
    return {
        "sku": "1001",
        "name_es": "Plancha F48",
        "cat1_es": "Hogar",
        "cat2_es": "Cuidado de la ropa",
        "spec_es": "F48",
        "desc_es": "Necesita pilas AAA.",
        "details_es": "Cubierta: Cubierta blanda; Material: Polipropileno (PP)",
        "name_zh": "F48 电熨斗",
        "cat1_zh": "家居",
        "cat2_zh": "衣物护理",
        "spec_zh": "F48",
        "desc_zh": "需使用AAA电池。",
        "details_zh": "封面：平装；材质：聚丙烯(聚丙烯)",
    }


def _row(record, field, **extra):
    row = {
        "sku": record["sku"], "field": field,
        "expected_source_hash": localization_field_source_hash(record, field),
        "expected_target_hash": value_hash(record[{"name": "name_zh", "details": "details_zh", "description": "desc_zh"}[field]]),
        "policy_manifest_hash": policy_hash("policy-v1"),
        "approved_by": "project-owner",
        "reason": "regression",
    }
    row.update(extra)
    return row


def test_preview_changes_only_one_field_and_preserves_detail_structure():
    record = _record()
    row = _row(record, "details", operation="REPLACE_DETAILS_PAIR", detail_pair_index=0,
               expected_target_key="封面", expected_target_value="平装", target_key="封面", target_value="软封面")
    rows = build_preview([record], [row], expected_policy_hash=policy_hash("policy-v1"))
    assert rows[0]["status"] == "WOULD_UPDATE"
    assert rows[0]["reviewed_value"] == "封面：软封面；材质：聚丙烯(聚丙烯)"


def test_detail_pair_patch_preserves_duplicate_keys_and_count():
    record = _record()
    record["details_zh"] = "封面：平装；封面：平装；材质：聚丙烯(聚丙烯)"
    row = _row(record, "details", operation="REPLACE_DETAILS_PAIR", detail_pair_index=1,
               expected_target_key="封面", expected_target_value="平装", target_key="封面", target_value="软封面")
    preview = build_preview([record], [row], expected_policy_hash=policy_hash("policy-v1"))
    assert preview[0]["status"] == "WOULD_UPDATE"
    assert preview[0]["reviewed_value"].count("封面：") == 2
    assert preview[0]["reviewed_value"] == "封面：平装；封面：软封面；材质：聚丙烯(聚丙烯)"


def test_preview_blocks_source_or_policy_drift():
    record = _record()
    row = _row(record, "name", reviewed_value="F48 电熨斗")
    row["expected_source_hash"] = "stale"
    assert build_preview([record], [row], expected_policy_hash=policy_hash("policy-v1"))[0]["status"] == "BLOCKED_SOURCE_CHANGED"
    row = _row(record, "name", reviewed_value="F48 电熨斗")
    row["policy_manifest_hash"] = "stale"
    assert build_preview([record], [row], expected_policy_hash=policy_hash("policy-v1"))[0]["status"] == "BLOCKED_POLICY_CHANGED"


def test_preview_requires_owner_approval():
    record = _record()
    row = _row(record, "name", reviewed_value="F48 电熨斗")
    row["approved_by"] = ""
    assert build_preview([record], [row], expected_policy_hash=policy_hash("policy-v1"))[0]["status"] == "BLOCKED_UNAPPROVED"


def test_preview_blocks_duplicate_repair_target_before_apply():
    record = _record()
    first = _row(record, "name", reviewed_value="F48 电熨斗（修复一）")
    second = _row(record, "name", reviewed_value="F48 电熨斗（修复二）")
    preview = build_preview([record], [first, second], expected_policy_hash=policy_hash("policy-v1"))
    assert [row["status"] for row in preview] == [
        "BLOCKED_DUPLICATE_TARGET", "BLOCKED_DUPLICATE_TARGET",
    ]
    assert {row["reason"] for row in preview} == {"DUPLICATE_REPAIR_TARGET"}


def test_database_apply_verify_and_rollback_are_field_scoped(tmp_path):
    db_path = tmp_path / "action.db"
    migrate_v2(db_path, role="PRIMARY")
    record = _record()
    with connect(db_path) as db:
        db.execute("INSERT INTO products(canonical_id,official_sku,name_es,status) VALUES(?,?,?,'CURRENT')", ("ACT1001", "1001", record["name_es"]))
        db.execute("INSERT INTO product_localizations(official_sku,language,name,cat1,cat2,spec,description,details,source,review_status,updated_at,last_commit_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", ("1001", "es", record["name_es"], record["cat1_es"], record["cat2_es"], record["spec_es"], record["desc_es"], record["details_es"], "source", "APPROVED", "2026-01-01T00:00:00+00:00", "seed"))
        db.execute("INSERT INTO product_localizations(official_sku,language,name,cat1,cat2,spec,description,details,source,review_status,updated_at,last_commit_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", ("1001", "zh", record["name_zh"], record["cat1_zh"], record["cat2_zh"], record["spec_zh"], record["desc_zh"], record["details_zh"], "seed", "PENDING", "2026-01-01T00:00:00+00:00", "seed"))
        for field, value in (("name", record["name_zh"]), ("details", record["details_zh"])):
            db.execute("INSERT INTO localization_fields(official_sku,language,field_name,value,source,review_status,source_hash,updated_at,applied_commit_id) VALUES(?,?,?,?,?,?,?,?,?)", ("1001", "zh", field, value, "seed", "PENDING", localization_field_source_hash(record, field), "2026-01-01T00:00:00+00:00", "seed"))
            db.execute("INSERT INTO localization_field_provenance(official_sku,language,field_name,value,source,review_status,source_hash,updated_at,applied_commit_id) VALUES(?,?,?,?,?,?,?,?,?)", ("1001", "zh", field, value, "seed", "PENDING", localization_field_source_hash(record, field), "2026-01-01T00:00:00+00:00", "seed"))
    policy = policy_hash("policy-v1")
    row = _row(record, "name", reviewed_value="F48 电熨斗（修复）")
    preview = build_preview([record], [row], expected_policy_hash=policy)
    assert apply_preview_to_database(db_path, preview, actor="project-owner", run_id="repair-1", expected_policy_hash=policy, dry_run=True)["formal_write"] is False
    result = apply_preview_to_database(db_path, preview, actor="project-owner", run_id="repair-1", expected_policy_hash=policy, dry_run=False)
    assert result["applied"] == 1
    assert verify_database_apply(db_path, preview)["verified"] is True
    with connect(db_path) as db:
        assert db.execute("SELECT details FROM product_localizations WHERE official_sku='1001' AND language='zh'").fetchone()[0] == record["details_zh"]
    assert rollback_database(db_path, "repair-1", actor="project-owner")["restored"] == 1

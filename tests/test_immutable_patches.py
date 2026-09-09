import sqlite3
from pathlib import Path

import pytest

from action_tracker.database.immutable_patches import (
    ImmutablePatchError,
    append_patch_event,
    create_localization_patch,
    patch_status,
    record_source_fact_versions,
    validate_patch_apply,
)
from action_tracker.database.schema import migrate_v2


def _db(tmp_path: Path) -> Path:
    path = tmp_path / "test.db"
    migrate_v2(path)
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO products(canonical_id,official_sku,status) VALUES('c1','1001','CURRENT')")
    return path


def test_raw_and_normalized_facts_are_append_only_and_idempotent(tmp_path):
    path = _db(tmp_path)
    facts = {"details": {"raw": "Material:: Plástico", "normalized": "Material: Plástico"}}
    assert record_source_fact_versions(path, run_id="r1", official_sku="1001", source_name="detail", facts=facts) == 1
    assert record_source_fact_versions(path, run_id="r1", official_sku="1001", source_name="detail", facts=facts) == 0
    with sqlite3.connect(path) as db:
        row = db.execute("SELECT raw_value,normalized_value FROM source_fact_versions").fetchone()
    assert row == ("Material:: Plástico", "Material: Plástico")


def test_patch_requires_one_field_and_immutable_transition(tmp_path):
    path = _db(tmp_path)
    create_localization_patch(
        path, patch_id="p1", official_sku="1001", language="zh", field_name="name",
        old_value="旧名", new_value="新名", source_hash="h1", source_allowlist=["MANUAL"], created_by="human",
    )
    assert patch_status(path, "p1") == "PATCH_CREATED"
    append_patch_event(path, patch_id="p1", event_type="PATCH_APPROVED", actor="reviewer", evidence={"ticket": "T1"})
    assert validate_patch_apply(path, patch_id="p1", current_source_hash="h1", source_name="MANUAL")["status"] == "APPROVED"
    append_patch_event(path, patch_id="p1", event_type="PATCH_APPLIED", actor="system", evidence={"commit": "c1"})
    assert patch_status(path, "p1") == "PATCH_APPLIED"
    append_patch_event(path, patch_id="p1", event_type="PATCH_REVOKED", actor="human:reviewer")
    assert patch_status(path, "p1") == "PATCH_REVOKED"


def test_apply_gate_rejects_changed_source_or_unapproved_source(tmp_path):
    path = _db(tmp_path)
    create_localization_patch(
        path, patch_id="p1", official_sku="1001", language="zh", field_name="name",
        old_value="旧名", new_value="新名", source_hash="h1", source_allowlist=["MANUAL"], created_by="human",
    )
    append_patch_event(path, patch_id="p1", event_type="PATCH_APPROVED", actor="reviewer", evidence={"field_name": "name"})
    with pytest.raises(ImmutablePatchError, match="PATCH_SOURCE_HASH_MISMATCH"):
        validate_patch_apply(path, patch_id="p1", current_source_hash="h2", source_name="MANUAL")
    with pytest.raises(ImmutablePatchError, match="PATCH_SOURCE_NOT_ALLOWED"):
        validate_patch_apply(path, patch_id="p1", current_source_hash="h1", source_name="MODEL")


def test_patch_rejects_unknown_field_and_missing_allowlist(tmp_path):
    path = _db(tmp_path)
    with pytest.raises(ImmutablePatchError, match="PATCH_FIELD_NOT_ALLOWED"):
        create_localization_patch(
            path, patch_id="p1", official_sku="1001", language="zh", field_name="price",
            old_value="1", new_value="2", source_hash="h1", source_allowlist=["MANUAL"], created_by="human",
        )
    with pytest.raises(ImmutablePatchError, match="PATCH_SOURCE_ALLOWLIST_MISSING"):
        create_localization_patch(
            path, patch_id="p2", official_sku="1001", language="zh", field_name="name",
            old_value="旧名", new_value="新名", source_hash="h1", source_allowlist=[], created_by="human",
        )

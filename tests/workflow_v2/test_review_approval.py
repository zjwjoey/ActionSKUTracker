from __future__ import annotations

from action_tracker.database.connection import connect
from action_tracker.localization.registry.repository import LocalizationRegistry
from action_tracker.workflow_v2.review_approval import apply_owner_decisions


def _review(revision_id: str, source_hash: str) -> dict[str, str]:
    return {"sku": "100", "field_name": "name_es", "revision_id": revision_id, "source_hash": source_hash, "reviewed_value": "新名称"}


def _seed(tmp_path):
    path = tmp_path / "registry.sqlite"
    registry = LocalizationRegistry(path)
    with connect(path) as db:
        db.execute("INSERT INTO products(canonical_id,official_sku,status) VALUES('c-100','100','CURRENT')")
    source_hash = "source-100"
    source_id = registry.register_source("100", {"name_es": "Nombre"}, source_hash, observed_at="2026-10-06")
    with connect(path) as db:
        unit_id = db.execute("SELECT unit_id FROM translation_units WHERE source_version_id=? AND field_name='name_es'", (source_id,)).fetchone()[0]
    revision_id = registry.record_revision(unit_id=unit_id, target_text="旧名称", provider="qwen", model="fixture", request_hash="rq", response_hash="rs", source_hash=source_hash, qa_status="PASS", canonical_qa_status="NOT_REQUIRED")
    return registry, revision_id, source_hash


def test_owner_review_approval_is_source_bound_and_idempotent(tmp_path):
    registry, revision_id, source_hash = _seed(tmp_path)
    decision = {"sku": "100", "field_name": "name_es", "owner_decision": "EDIT_AND_APPROVE", "target_text": "新名称", "source_hash": source_hash, "parent_revision_id": revision_id, "owner_note": "owner approved"}
    first = apply_owner_decisions(registry, [_review(revision_id, source_hash)], [decision], actor="human:owner")
    assert first["approved_count"] == 1
    assert first["actions"][0]["status"] == "APPROVED_EDIT"
    second = apply_owner_decisions(registry, [_review(revision_id, source_hash)], [decision], actor="human:owner")
    assert second["actions"][0]["status"] == "ALREADY_APPROVED"


def test_owner_review_rejects_source_binding_mismatch(tmp_path):
    registry, revision_id, source_hash = _seed(tmp_path)
    decision = {"sku": "100", "field_name": "name_es", "owner_decision": "APPROVE", "target_text": "旧名称", "source_hash": "other", "parent_revision_id": revision_id}
    try:
        apply_owner_decisions(registry, [_review(revision_id, source_hash)], [decision], actor="human:owner")
    except ValueError as exc:
        assert str(exc) == "OWNER_DECISION_SOURCE_BINDING_MISMATCH"
    else:
        raise AssertionError("binding mismatch must fail")


def test_owner_review_preflights_every_row_before_any_approval(tmp_path):
    registry, revision_id, source_hash = _seed(tmp_path)
    other_hash = "source-200"
    with connect(registry.path) as db:
        db.execute("INSERT INTO products(canonical_id,official_sku,status) VALUES('c-200','200','CURRENT')")
    source_id = registry.register_source("200", {"name_es": "Otro"}, other_hash, observed_at="2026-10-06")
    with connect(registry.path) as db:
        unit_id = db.execute("SELECT unit_id FROM translation_units WHERE source_version_id=?", (source_id,)).fetchone()[0]
    approved_id = registry.record_revision(unit_id=unit_id, target_text="已批准名称", provider="qwen", model="fixture", request_hash="rq", response_hash="rs", source_hash=other_hash, qa_status="PASS", canonical_qa_status="NOT_REQUIRED")
    assert registry.approve_revision(approved_id, actor="human:owner")
    first = {"sku": "100", "field_name": "name_es", "owner_decision": "APPROVE", "target_text": "旧名称", "source_hash": source_hash, "parent_revision_id": revision_id}
    second = {"sku": "200", "field_name": "name_es", "owner_decision": "EDIT_AND_APPROVE", "target_text": "冲突名称", "source_hash": other_hash, "parent_revision_id": approved_id}
    second_review = {"sku": "200", "field_name": "name_es", "revision_id": approved_id, "source_hash": other_hash, "reviewed_value": "冲突名称"}
    try:
        apply_owner_decisions(registry, [_review(revision_id, source_hash), second_review], [first, second], actor="human:owner")
    except ValueError as exc:
        assert str(exc) == "OWNER_DECISION_APPROVED_TARGET_CONFLICT"
    else:
        raise AssertionError("conflict must fail before any mutation")
    with connect(registry.path) as db:
        assert db.execute("SELECT review_status FROM translation_revisions WHERE revision_id=?", (revision_id,)).fetchone()[0] == "PENDING"

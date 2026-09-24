from action_tracker.services.hashing import field_source_hash
from action_tracker.stage6.legacy_provenance import adapt_legacy_record
from action_tracker.stage6.preview import preview_one


def _source():
    return {
        "name": "Producto", "cat1": "Hogar", "cat2": "Interior",
        "spec": "100 cm", "description": "Para casa", "details": "Material: Plástico",
    }


def _records():
    source = _source()
    hash_record = {
        "name_es": source["name"], "cat1_es": source["cat1"],
        "cat2_es": source["cat2"], "spec_es": source["spec"],
        "desc_es": source["description"], "details_es": source["details"],
    }
    field_hash = field_source_hash(hash_record, "description")
    revalidation = {
        "sku": "1001", "field": "description",
        "review_artifact": "runtime/review/reviewed_fields.csv",
        "review_decision": "KEEP", "reviewed_value": "用于家庭",
        "reviewed_at": "2026-09-21", "historical_source_text": "Para casa",
        "current_source_text": "Para casa", "historical_field_hash": field_hash,
        "current_field_hash": field_hash, "source_text_match": "YES",
        "source_hash_match": "YES", "revalidation_status": "REVALIDATED_CURRENT",
    }
    owner_row = {
        "review_id": "owner-1", "candidate_id": "candidate-1", "sku": "1001",
        "field": "description", "spanish_source": "Para casa",
        "reviewed_value": "用于家庭", "owner_decision": "ACCEPT",
        "owner_note": "Owner accepted", "master_value": "用于家庭",
        "sqlite_value": "用于家庭",
    }
    return source, revalidation, owner_row


def _preview(source, revalidation, owner_row, target=None):
    candidate, owner, provenance = adapt_legacy_record(
        revalidation, owner_row, artifact_kind="FIELD_REVIEW_CSV",
        artifact_sha256="review-sha256", owner_approval_artifact="owner.csv",
        owner_approval_sha256="owner-sha256", current_sqlite_value="用于家庭",
    )
    target = target or {"desc_zh": "用于家庭"}
    return preview_one(
        owner, candidate, source, target, policy_hash="current-policy",
        source_snapshot_path=None,
    )


def test_legacy_complete_evidence_allows_preview_without_fabricating_native_metadata():
    source, revalidation, owner = _records()
    row = _preview(source, revalidation, owner)
    assert row["provenance_type"] == "LEGACY_ARTIFACT"
    assert row["apply_action"] == "NO_CHANGE"
    assert row["conflict_reason"] == []


def test_legacy_candidate_records_missing_native_metadata_as_expected_absence():
    source, revalidation, owner = _records()
    candidate, _, provenance = adapt_legacy_record(revalidation, owner, artifact_kind="FIELD_REVIEW_CSV", artifact_sha256="review-sha256", owner_approval_artifact="owner.csv", owner_approval_sha256="owner-sha256")
    assert "contract_hash" not in candidate
    assert "guard_policy_hash" not in candidate
    assert "raw_model_output" not in candidate
    assert provenance.legacy_missing_fields
    assert provenance.evidence_absence_reason["raw_model_output"] == "NOT_RECORDED_BY_HISTORICAL_REVIEW_WORKFLOW"
    assert _preview(source, revalidation, owner)["apply_action"] == "NO_CHANGE"


def test_legacy_adapter_preserves_real_historical_metadata_without_upgrading_it_to_native():
    source, revalidation, owner = _records()
    _, _, provenance = adapt_legacy_record(
        revalidation, owner, artifact_kind="FIELD_REVIEW_CSV",
        artifact_sha256="review-sha256", owner_approval_artifact="owner.csv",
        owner_approval_sha256="owner-sha256", current_sqlite_value="用于家庭",
        historical_metadata={"legacy_review_model": "CODEX", "legacy_translator_model": "qwen-mt-flash"},
    )
    assert provenance.historical_metadata == {
        "legacy_review_model": "CODEX", "legacy_translator_model": "qwen-mt-flash",
    }
    assert "review_model_version" in provenance.legacy_missing_fields


def test_legacy_missing_historical_source_blocks():
    source, revalidation, owner = _records()
    revalidation["historical_source_text"] = ""
    row = _preview(source, revalidation, owner)
    assert row["apply_action"] == "BLOCKED_CONFLICT"
    assert "MISSING_HISTORICAL_SOURCE" in row["conflict_reason"]


def test_legacy_changed_source_blocks():
    source, revalidation, owner = _records()
    source["description"] = "En casa"
    row = _preview(source, revalidation, owner)
    assert row["apply_action"] == "BLOCKED_CONFLICT"
    assert "SOURCE_CHANGED" in row["conflict_reason"]
    assert "SOURCE_HASH_MISMATCH" in row["conflict_reason"]


def test_legacy_owner_hold_reject_and_missing_decision_block():
    for decision, expected in (("HOLD", "OWNER_HOLD"), ("REJECT", "OWNER_REJECTED"), ("", "OWNER_NOT_APPROVED")):
        source, revalidation, owner = _records()
        owner["owner_decision"] = decision
        row = _preview(source, revalidation, owner)
        assert row["apply_action"] == "BLOCKED_CONFLICT"
        assert expected in row["conflict_reason"]


def test_legacy_artifact_conflict_blocks():
    source, revalidation, owner = _records()
    revalidation["revalidation_status"] = "ARTIFACT_CONFLICT"
    row = _preview(source, revalidation, owner)
    assert row["apply_action"] == "BLOCKED_CONFLICT"
    assert "ARTIFACT_CONFLICT" in row["conflict_reason"]
    assert "SOURCE_NOT_REVALIDATED" in row["conflict_reason"]


def test_legacy_current_cross_field_source_conflict_blocks():
    source, revalidation, owner = _records()
    source["details"] = "Tamaño: 200 cm; Material: Plástico"
    row = _preview(source, revalidation, owner)
    assert row["apply_action"] == "BLOCKED_CONFLICT"
    assert "SOURCE_CONFLICT" in row["conflict_reason"]


def test_legacy_candidate_value_changed_after_owner_approval_blocks():
    source, revalidation, owner = _records()
    owner["reviewed_value"] = "另一个译文"
    row = _preview(source, revalidation, owner)
    assert row["apply_action"] == "BLOCKED_CONFLICT"
    assert "CANDIDATE_VALUE_MISMATCH" in row["conflict_reason"]


def test_legacy_master_baseline_change_blocks():
    source, revalidation, owner = _records()
    row = _preview(source, revalidation, owner, {"desc_zh": "后来改过的值"})
    assert row["apply_action"] == "BLOCKED_CONFLICT"
    assert "MASTER_BASELINE_CHANGED" in row["conflict_reason"]


def test_legacy_sqlite_baseline_change_blocks():
    source, revalidation, owner = _records()
    owner["sqlite_value"] = "Owner package SQLite baseline"
    candidate, preview_owner, provenance = adapt_legacy_record(
        revalidation, owner, artifact_kind="FIELD_REVIEW_CSV",
        artifact_sha256="review-sha256", owner_approval_artifact="owner.csv",
        owner_approval_sha256="owner-sha256", current_sqlite_value="new SQLite value",
    )
    result = preview_one(
        preview_owner, candidate, source, {"desc_zh": "用于家庭"},
        policy_hash="p", source_snapshot_path=None,
    )
    assert result["apply_action"] == "BLOCKED_CONFLICT"
    assert "SQLITE_BASELINE_CHANGED" in result["conflict_reason"]


def test_native_candidate_missing_modern_provenance_still_blocks():
    source, revalidation, owner_row = _records()
    candidate, owner, _ = adapt_legacy_record(revalidation, owner_row, artifact_kind="FIELD_REVIEW_CSV", artifact_sha256="review-sha256", owner_approval_artifact="owner.csv", owner_approval_sha256="owner-sha256")
    candidate["provenance_type"] = "NATIVE"
    candidate.pop("legacy_provenance")
    result = preview_one(owner, candidate, source, {"desc_zh": "用于家庭"}, policy_hash="p")
    assert result["apply_action"] == "BLOCKED_CONFLICT"
    assert "MISSING_PROVENANCE" in result["conflict_reason"]


def test_undeclared_provenance_never_enters_legacy_fallback():
    source, revalidation, owner_row = _records()
    candidate, owner, _ = adapt_legacy_record(revalidation, owner_row, artifact_kind="FIELD_REVIEW_CSV", artifact_sha256="review-sha256", owner_approval_artifact="owner.csv", owner_approval_sha256="owner-sha256")
    candidate.pop("provenance_type")
    result = preview_one(owner, candidate, source, {"desc_zh": "用于家庭"}, policy_hash="p")
    assert result["provenance_type"] == "NATIVE"
    assert result["apply_action"] == "BLOCKED_CONFLICT"
    assert "MISSING_PROVENANCE" in result["conflict_reason"]

from action_tracker.services.hashing import field_source_hash, localization_source_hash
from action_tracker.stage5.source_candidate_v2 import candidate_source_provenance
from action_tracker.stage6.preview import preview_one


def test_stage6_field_candidate_uses_own_source_hash():
    source = {"name": "Producto", "description": "Para casa", "details": "Material: Plástico"}
    candidate = _candidate(source)
    owner = {"sku": "1001", "field": "description", "candidate_id": "c1", "spanish_source": "Para casa", "owner_disposition": "ACCEPT_AS_IS", "owner_reviewer": "human:test", "owner_reviewed_at": "2026-09-22" , "owner_final_candidate": "用于家庭"}
    result = preview_one(owner, candidate, source, {"desc_zh": "旧"}, policy_hash="p1", source_snapshot_path="snapshot.json")
    assert result["apply_action"] == "WOULD_UPDATE"
    assert result["conflict_status"] == "NONE"


def test_stage6_unapproved_or_missing_provenance_is_blocked():
    result = preview_one({"sku": "1001", "field": "description"}, None, None, None, policy_hash="p1")
    assert result["apply_action"] == "BLOCKED_CONFLICT"
    assert "MISSING_PROVENANCE" in result["conflict_reason"]


def _owner(field="description", value="用于家庭"):
    return {
        "sku": "1001", "field": field, "candidate_id": "c1",
        "owner_disposition": "ACCEPT_AS_IS", "owner_reviewer": "human:test",
        "owner_reviewed_at": "2026-09-22", "owner_final_candidate": value,
    }


def _candidate(source, field="description", **extra):
    payload = {"candidate_id": "c1", "sku": "1001", "source_field": field,
               "provenance_type": "NATIVE", "parsed_candidate": "用于家庭",
               "contract_hash": "contract:1", "guard_policy_hash": "guard:1",
               "resolver_result": {"status": "READY"}, "raw_model_output": "用于家庭",
               "review_evidence_id": "review-evidence-1", "review_decision": "KEEP",
               "reviewed_at": "2026-09-22", "review_model_version": "review-model-v1",
               "review_policy_version": "review-policy-v1",
               "source_snapshot_path": "snapshot.json", "source_snapshot_run_id": "run-1",
               "source_snapshot_sha256": "snapshot-sha256",
               **candidate_source_provenance(source, field)}
    payload.update(extra)
    return payload


def test_stage6_empty_source_is_terminal_no_source_and_never_updates():
    source = {"name": "Producto", "description": "", "details": "Material: Plástico"}
    result = preview_one(_owner(), _candidate(source), source, {"desc_zh": "旧"}, policy_hash="p1", source_snapshot_path="snapshot.json")
    assert result["apply_action"] == "NO_SOURCE"
    assert result["conflict_status"] == "DO_NOT_APPLY"
    assert "NO_SOURCE" in result["conflict_reason"]


def test_stage6_missing_hash_scope_is_blocked_instead_of_legacy_default():
    source = {"name": "Producto", "description": "Para casa", "details": "Material: Plástico"}
    candidate = _candidate(source)
    candidate.pop("hash_scope")
    result = preview_one(_owner(), candidate, source, {"desc_zh": "旧"}, policy_hash="p1", source_snapshot_path="snapshot.json")
    assert result["apply_action"] == "BLOCKED_CONFLICT"
    assert "FIELD_HASH_REQUIRED" in result["conflict_reason"]


def test_stage6_legacy_overall_hash_cannot_bypass_native_field_provenance():
    source = {"name": "Producto", "description": "Para casa", "details": "Material: Plástico"}
    candidate = {"candidate_id": "c1", "sku": "1001", "source_field": "description",
                 "provenance_type": "NATIVE",
                 "source_spanish_value": "Para casa", "parsed_candidate": "用于家庭",
                 "hash_scope": "legacy_overall", "source_hash": localization_source_hash({
                     "name_es": "Producto", "cat1_es": "", "cat2_es": "", "spec_es": "",
                     "desc_es": "Para casa", "details_es": "Material: Plástico"}),
                 "contract_hash": "contract:1", "guard_policy_hash": "guard:1",
                 "resolver_result": {"status": "READY"}, "raw_model_output": "用于家庭",
                 "review_evidence_id": "review-evidence-1", "review_decision": "KEEP",
                 "reviewed_at": "2026-09-22", "review_model_version": "review-model-v1",
                 "review_policy_version": "review-policy-v1",
                 "source_snapshot_path": "snapshot.json", "source_snapshot_run_id": "run-1",
                 "source_snapshot_sha256": "snapshot-sha256"}
    blocked = preview_one(_owner(), candidate, source, {"desc_zh": "旧"}, policy_hash="p1", source_snapshot_path="snapshot.json")
    assert blocked["apply_action"] == "BLOCKED_CONFLICT"
    assert "FIELD_HASH_REQUIRED" in blocked["conflict_reason"]
    candidate["legacy_compatibility"] = True
    allowed = preview_one(_owner(), candidate, source, {"desc_zh": "旧"}, policy_hash="p1", source_snapshot_path="snapshot.json")
    assert allowed["apply_action"] == "BLOCKED_CONFLICT"
    assert "FIELD_HASH_REQUIRED" in allowed["conflict_reason"]


def test_stage6_source_field_mismatch_is_blocked():
    source = {"name": "Producto", "description": "Para casa", "details": "Material: Plástico"}
    candidate = _candidate(source, source_spanish_value="Otro texto")
    result = preview_one(_owner(), candidate, source, {"desc_zh": "旧"}, policy_hash="p1", source_snapshot_path="snapshot.json")
    assert result["apply_action"] == "BLOCKED_CONFLICT"
    assert "SOURCE_FIELD_MISMATCH" in result["conflict_reason"]


def test_stage6_partial_provenance_missing_target_fails_closed():
    source = {"name": "Producto", "description": "Para casa", "details": "Material: Plástico"}
    result = preview_one(_owner(), _candidate(source), source, None, policy_hash="p1", source_snapshot_path="snapshot.json")
    assert result["apply_action"] == "BLOCKED_CONFLICT"
    assert "MISSING_PROVENANCE" in result["conflict_reason"]


def test_stage6_partial_provenance_missing_snapshot_fails_closed():
    source = {"name": "Producto", "description": "Para casa", "details": "Material: Plástico"}
    result = preview_one(_owner(), _candidate(source), source, {"desc_zh": "旧"}, policy_hash="p1", source_snapshot_path=None)
    assert result["apply_action"] == "BLOCKED_CONFLICT"
    assert "MISSING_PROVENANCE" in result["conflict_reason"]

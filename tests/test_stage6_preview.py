from action_tracker.services.hashing import field_source_hash
from action_tracker.stage6.preview import preview_one


def test_stage6_field_candidate_uses_own_source_hash():
    source = {"name": "Producto", "description": "Para casa", "details": "Material: Plástico"}
    candidate = {
        "candidate_id": "c1", "sku": "1001", "source_field": "description",
        "source_spanish_value": "Para casa",
        "source_hash": field_source_hash({"name_es": "Producto", "desc_es": "Para casa", "details_es": "Material: Plástico"}, "description"),
        "parsed_candidate": "用于家庭", "hash_scope": "field",
    }
    owner = {"sku": "1001", "field": "description", "candidate_id": "c1", "spanish_source": "Para casa", "owner_disposition": "ACCEPT_AS_IS", "owner_reviewer": "human:test", "owner_reviewed_at": "2026-09-22" , "owner_final_candidate": "用于家庭"}
    result = preview_one(owner, candidate, source, {"desc_zh": "旧"}, policy_hash="p1", source_snapshot_path="snapshot.json")
    assert result["apply_action"] == "WOULD_UPDATE"
    assert result["conflict_status"] == "NONE"


def test_stage6_unapproved_or_missing_provenance_is_blocked():
    result = preview_one({"sku": "1001", "field": "description"}, None, None, None, policy_hash="p1")
    assert result["apply_action"] == "BLOCKED_CONFLICT"
    assert "MISSING_PROVENANCE" in result["conflict_reason"]

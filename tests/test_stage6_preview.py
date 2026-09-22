from __future__ import annotations

from action_tracker.services.hashing import field_source_hash
from action_tracker.stage5.source_candidate_v2 import source_hash
from action_tracker.stage6.preview import digest, preview_hash, preview_one


def _case():
    source = {
        "name": "Jarrón de vidrio", "cat1": "Vivienda", "cat2": "Decoración",
        "spec": "250 ml", "description": "Para flores", "details": "Número del artículo: 1234567",
    }
    target = {
        "sku": "1234567", "name_es": source["name"], "cat1_es": source["cat1"],
        "cat2_es": source["cat2"], "spec_es": source["spec"],
        "desc_es": source["description"], "details_es": source["details"],
        "name_zh": "旧花瓶",
    }
    candidate = {
        "candidate_id": "c1", "sku": "1234567", "source_field": "name",
        "source_spanish_value": source["name"], "source_hash": source_hash(source),
        "contract_hash": "contract", "guard_policy_hash": "guard",
        "resolver_result": {}, "raw_model_output": '{"name":"模型值"}',
        "parsed_candidate": "模型值",
    }
    owner = {
        "sku": "1234567", "field": "name", "candidate_id": "c1", "review_id": "r1",
        "spanish_source": source["name"], "owner_disposition": "ACCEPT_WITH_MINOR_EDIT",
        "owner_final_candidate": "玻璃花瓶", "owner_reviewer": "owner",
        "owner_reviewed_at": "2026-09-13T00:00:00Z",
    }
    return owner, candidate, source, target


def _preview(owner=None, candidate=None, source=None, target=None, **overrides):
    base = _case()
    return preview_one(
        base[0] if owner is None else owner,
        base[1] if candidate is None else candidate,
        base[2] if source is None else source,
        base[3] if target is None else target,
        policy_hash="policy-v1", source_snapshot_path="snapshot.csv", **overrides,
    )


def test_stage6_preview_uses_owner_minor_edit_and_preserves_target():
    owner, candidate, source, target = _case()
    row = _preview()
    assert row["apply_action"] == "WOULD_UPDATE"
    assert row["reviewed_value"] == "玻璃花瓶"
    assert row["reviewed_value"] != candidate["parsed_candidate"]
    assert row["expected_target_hash"] == digest("旧花瓶")
    assert target["name_zh"] == "旧花瓶"
    assert row["conflict_reason"] == []


def test_stage6_no_change_and_hash_order_are_deterministic():
    owner, candidate, source, target = _case()
    target["name_zh"] = owner["owner_final_candidate"]
    row = _preview(target=target)
    assert row["apply_action"] == "NO_CHANGE"
    assert digest(None) != digest("")
    assert preview_hash([row, _preview()]) == preview_hash([_preview(), row])


def test_stage6_blocks_source_target_review_and_policy_changes():
    owner, candidate, source, target = _case()
    changed_source = {**target, "name_es": "Otro jarrón"}
    assert "SOURCE_CHANGED" in _preview(target=changed_source)["conflict_reason"]
    assert "TARGET_CHANGED" in _preview(expected_target_hash=digest("different"))["conflict_reason"]
    assert "REVIEW_CHANGED" in _preview(expected_reviewed_value_hash=digest("different"))["conflict_reason"]
    assert "POLICY_CHANGED" in _preview(expected_policy_hash="old-policy")["conflict_reason"]


def test_stage6_blocks_stale_candidate_missing_provenance_and_unapproved_field():
    owner, candidate, source, target = _case()
    stale = {**candidate, "source_hash": "0" * 64}
    assert "CANDIDATE_STALE" in _preview(candidate=stale)["conflict_reason"]
    assert "MISSING_PROVENANCE" in _preview(candidate={"candidate_id": "c1"})["conflict_reason"]
    unapproved = {**owner, "owner_disposition": "REQUIRES_MAJOR_EDIT"}
    assert "NOT_OWNER_APPROVED" in _preview(owner=unapproved)["conflict_reason"]
    invalid_field = {**owner, "field": "price"}
    assert "FIELD_NOT_APPLYABLE" in _preview(owner=invalid_field)["conflict_reason"]


def test_stage6_blocks_source_conflict_even_when_owner_accepted_translation():
    owner, candidate, source, target = _case()
    conflict_source = {**source, "spec": "20 unidades", "details": "Cantidad: 22 unidades; Número del artículo: 1234567"}
    conflict_target = {**target, "spec_es": conflict_source["spec"], "details_es": conflict_source["details"]}
    conflict_candidate = {**candidate, "source_hash": source_hash(conflict_source)}
    row = _preview(candidate=conflict_candidate, source=conflict_source, target=conflict_target)
    assert row["apply_action"] == "BLOCKED_CONFLICT"
    assert row["conflict_status"] == "DO_NOT_APPLY"
    assert "SOURCE_CONFLICT" in row["conflict_reason"]


def test_stage6_field_hash_scopes_freshness_to_the_target_field():
    owner, candidate, source, target = _case()
    owner["field"] = "description"
    owner["spanish_source"] = source["description"]
    candidate.update({
        "source_field": "description",
        "source_spanish_value": source["description"],
        "source_hash": field_source_hash({"desc_es": source["description"]}, "description"),
        "hash_scope": "field",
    })
    row = _preview(owner=owner, candidate=candidate)
    assert row["hash_scope"] == "field"
    changed_description = {**target, "desc_es": "Otro texto"}
    assert "CANDIDATE_STALE" in _preview(owner=owner, candidate=candidate, source={**source, "description": "Otro texto"}, target=changed_description)["conflict_reason"]

    unchanged_details = {**source, "description": "Otro texto"}
    details_target = {**target, "desc_es": unchanged_details["description"]}
    details_candidate = {**candidate, "source_field": "details", "source_spanish_value": source["details"], "source_hash": field_source_hash({"details_es": source["details"]}, "details")}
    details_owner = {**owner, "field": "details", "spanish_source": source["details"], "owner_final_candidate": "商品编号：1234567"}
    assert "CANDIDATE_STALE" not in _preview(owner=details_owner, candidate=details_candidate, source=unchanged_details, target=details_target)["conflict_reason"]


def test_stage6_field_hash_is_independent_for_spec_and_description_and_empty_is_deterministic():
    owner, candidate, source, target = _case()
    desc_hash = field_source_hash({"description_es": source["description"]}, "description")
    assert desc_hash == field_source_hash({"desc_es": source["description"]}, "description")
    assert desc_hash != field_source_hash({"description_es": ""}, "description")
    assert field_source_hash({"spec_es": ""}, "spec") == field_source_hash({"spec_es": ""}, "spec")
    assert desc_hash != field_source_hash({"spec_es": source["spec"]}, "spec")


def test_stage6_legacy_overall_hash_remains_compatible():
    row = _preview()
    assert row["hash_scope"] == "legacy_overall"
    assert row["conflict_reason"] == []

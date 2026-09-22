from action_tracker.knowledge.tm_adapter import build_product_override_candidates


def _tm(tm_id, field, decision="APPROVE", target="中文值", scope="EXACT_GLOBAL_TM"):
    return {"tm_id": tm_id, "field_name": field, "owner_decision": decision, "target_value": target, "shadow_scope": scope}


def _staging(tm_id, field, sku="1001", source_hash="hash-a"):
    return {
        "tm_id": tm_id,
        "field_name": field,
        "source_hash": source_hash,
        "provenance": [{"sku": sku, "source_file": "fixture.jsonl", "source_line": 1}],
    }


def test_adapter_materializes_only_sku_scoped_product_fields():
    terms = [
        _tm("name-1", "name"),
        _tm("spec-1", "spec", target="规格"),
        _tm("desc-1", "description", target="完整描述"),
        _tm("ctx-1", "spec", decision="CONTEXT_ONLY", target="数量"),
    ]
    staging = [_staging("name-1", "name"), _staging("spec-1", "spec"), _staging("desc-1", "description"), _staging("ctx-1", "spec")]
    candidates, findings, counts = build_product_override_candidates(terms, staging, updated_at="2026-09-15")
    assert {(row.key, row.field) for row in candidates} == {("1001", "name_zh_standard"), ("1001", "spec_zh_standard")}
    assert counts["product_override_candidates"] == 2
    assert counts["shadow_only_rows"] == 2
    assert not findings == []


def test_adapter_blocks_conflicting_same_sku_field():
    terms = [_tm("name-1", "name", target="一"), _tm("name-2", "name", target="二")]
    staging = [_staging("name-1", "name"), _staging("name-2", "name")]
    candidates, findings, counts = build_product_override_candidates(terms, staging, updated_at="2026-09-15")
    assert candidates == []
    assert counts["conflict_rows"] == 1
    assert findings[0]["status"] == "CONFLICT_REVIEW"


def test_adapter_does_not_treat_context_only_as_global_product_override():
    terms = [_tm("spec-ctx", "spec", decision="CONTEXT_ONLY", target="6片", scope="SKU_FIELD_SOURCE_HASH_ONLY")]
    staging = [_staging("spec-ctx", "spec")]
    candidates, _, counts = build_product_override_candidates(terms, staging, updated_at="2026-09-15")
    assert candidates == []
    assert counts["shadow_only_rows"] == 1

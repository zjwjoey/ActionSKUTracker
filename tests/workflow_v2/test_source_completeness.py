from action_tracker.workflow_v2.source_audit import audit_source_records


def test_source_completeness_passes(source_row):
    result = audit_source_records([source_row], authoritative_skus={"100"})
    assert result["source_ready"] is True
    assert result["source_sku_completeness"] == "PASS"


def test_source_completeness_blocks_extra_sku(source_row):
    extra = {**source_row, "sku": "101", "canonical_id": "ACT0000101"}
    result = audit_source_records([source_row, extra], authoritative_skus={"100"})
    assert result["source_sku_completeness"] == "FAIL"
    assert result["extra_sku_count"] == 1
    assert result["source_ready"] is False

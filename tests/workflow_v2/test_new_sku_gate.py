from action_tracker.workflow_v2.source_audit import audit_source_records


def test_missing_new_sku_blocks(source_row):
    result = audit_source_records([source_row], authoritative_skus={"100", "101"}, expected_new_skus={"101"})
    assert result["new_sku_completeness"] == "FAIL"
    assert result["source_ready"] is False

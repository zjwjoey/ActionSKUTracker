from action_tracker.workflow_v2.source_audit import audit_source_records


def test_source_completeness_passes(source_row):
    result = audit_source_records([source_row], authoritative_skus={"100"})
    assert result["source_ready"] is True
    assert result["source_sku_completeness"] == "PASS"

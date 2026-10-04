from action_tracker.workflow_v2.export_audit import audit_zh


def test_zh_audit_blocks_unapproved(source_row):
    assert audit_zh([source_row])["status"] == "FAIL"

from action_tracker.workflow_v2.export_audit import audit_parity


def test_parity_checks_identity(source_row):
    zh = dict(source_row); zh["current_price"] = 9.99
    assert audit_parity([source_row], [zh])["status"] == "FAIL"

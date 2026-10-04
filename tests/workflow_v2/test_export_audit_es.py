from action_tracker.workflow_v2.export_audit import audit_es


def test_es_audit_requires_url(source_row):
    source_row["product_url"] = ""
    assert audit_es([source_row])["status"] == "FAIL"

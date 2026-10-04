from action_tracker.workflow_v2.shadow import compare_shadow_payloads


def test_shadow_comparison_covers_lifecycle_and_events(source_row):
    payload = {"records": [source_row], "new_skus": ["100"], "reappeared_skus": [], "missing_skus": [], "offline_skus": [], "price_events": [{"sku": "100"}], "badge_events": []}
    result = compare_shadow_payloads(payload, payload)
    assert result["status"] == "PASS"
    assert result["failed_checks"] == []


def test_shadow_comparison_reports_fact_drift(source_row):
    old = {"records": [source_row]}; new = {"records": [{**source_row, "current_price": 8.99}]}
    assert compare_shadow_payloads(old, new)["status"] == "FAIL"

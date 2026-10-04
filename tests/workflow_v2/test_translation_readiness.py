from action_tracker.workflow_v2.translation_stage import auto_validate


def test_policy_requires_source_and_fact_commit():
    item = {"sku": "1", "status": "PASS", "fields": {"name": "桌"}, "source_hash": "h", "qa": {"overall_ready": True, "status": "PASS"}}
    assert auto_validate([item], source_ready=False, fact_committed=True)[0]["decision"] == "REVIEW_REQUIRED"

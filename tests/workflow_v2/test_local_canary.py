from action_tracker.workflow_v2.canary import run_local_canary


def test_local_canary_uses_isolated_sqlite(workflow_root, source_row, fake_provider):
    result = run_local_canary(record=source_row, provider=fake_provider, output_dir=workflow_root / "canary")
    assert result["status"] == "PASS"
    assert result["applied"]["applied_fields"] == 6
    assert str(result["database"]).endswith("canary.sqlite3")

from action_tracker.localization.gold_gate import evaluate_gold_gate


def _policy(complete=True):
    return {
        "policy_id": "TEST_GOLD",
        "gold_claim_allowed": True,
        "layers": [
            {"id": f"L{i}", "coverage": "COMPLETE" if complete else "PARTIAL", "covered_checks": ["x"], "not_covered": [] if complete else ["y"]}
            for i in range(1, 7)
        ],
    }


def test_gold_gate_fails_closed_for_partial_policy():
    result = evaluate_gold_gate({"blocking_findings": [], "review_findings": []}, _policy(False), source_snapshot_frozen=True, provenance_complete=True)
    assert result["status"] == "RELEASE_PASS_WITH_REVIEW_NOT_GOLD"
    assert result["gold_eligible"] is False


def test_gold_gate_passes_only_when_all_evidence_is_complete():
    result = evaluate_gold_gate({"blocking_findings": [], "review_findings": []}, _policy(True), source_snapshot_frozen=True, provenance_complete=True)
    assert result["status"] == "GOLD_PASS"
    assert result["gold_eligible"] is True


def test_gold_gate_blocks_release_findings():
    result = evaluate_gold_gate({"blocking_findings": [{"code": "X"}], "review_findings": []}, _policy(True), source_snapshot_frozen=True, provenance_complete=True)
    assert result["status"] == "BLOCKED"

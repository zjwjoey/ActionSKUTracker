from action_tracker.translation.detail_rule_candidates import (
    build_detail_rule_candidates,
    build_detail_rule_proposals,
)
from action_tracker.translation.regression_cases import RegressionCase


def _queue_row(review_id="detail-1", *, kind="SOURCE_VALUE"):
    return {
        "review_id": review_id,
        "review_kind": kind,
        "source_key_normalized": "tipo de bateria",
        "source_value_normalized": "alcalina",
        "occurrences": "2",
        "rule_covered_occurrences": "0",
        "uncovered_occurrences": "2",
        "sku_count": "2",
        "sku_examples": '["1001", "1002"]',
        "observed_target_candidates": '[{"target_value":"碱性电池"}]',
        "review_reasons": "VALUE_RULE_UNCOVERED",
        "source_sha256": "s",
        "target_sha256": "t",
        "detail_rules_sha256": "r",
    }


def test_detail_rule_candidates_aggregate_evidence_and_require_owner_review():
    candidates = build_detail_rule_candidates(
        [_queue_row(), {**_queue_row("detail-2"), "occurrences": "1", "sku_count": "1", "sku_examples": '["1003"]'}],
        source_sha256="s", target_sha256="t", rules_sha256="r",
    )
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["occurrences"] == 3
    assert candidate["sku_count"] == 3
    assert candidate["decision_status"] == "NEEDS_OWNER_REVIEW"
    assert candidate["auto_apply"] is False


def test_approved_detail_decision_creates_proposal_only_and_never_rules_write():
    queue = [_queue_row()]
    decisions = [{
        "review_id": "detail-1", "decision": "APPROVE", "approved_target": "碱性电池",
        "regression_case_ids": '["DETAIL_TEST_001"]',
        "reviewer": "owner", "reviewed_at": "2026-10-01T00:00:00+00:00",
        "evidence_status": "OWNER_CONFIRMED",
        "source_sha256": "s", "target_sha256": "t", "detail_rules_sha256": "r",
    }]
    report = build_detail_rule_proposals(
        queue, decisions, source_sha256="s", target_sha256="t", rules_sha256="r",
        regression_cases=(RegressionCase(
            case_id="DETAIL_TEST_001", field="details",
            source_es="Tipo de batería: alcalina", wrong_zh="旧译法",
            expected_zh="类型: 碱性电池", issue_type="TEST",
            risk_level="HIGH", evidence_status="OWNER_CONFIRMED",
            policy_version="TEST", source_key="tipo de bateria", source_value="alcalina",
        ),),
    )
    assert report["accepted"] is True
    assert report["rules_write"] is False
    assert report["proposals"] == [{
        "review_id": "detail-1", "section": "value_translations",
        "source_key": "tipo de bateria", "source_value": "alcalina",
        "target_value": "碱性电池", "auto_apply": False,
    }]


def test_pair_alignment_decision_cannot_become_global_rule():
    queue = [_queue_row(kind="PAIR_ALIGNMENT")]
    decisions = [{
        "review_id": "detail-1", "decision": "APPROVE", "approved_target": "保留原文",
        "reviewer": "owner", "reviewed_at": "2026-10-01T00:00:00+00:00",
        "evidence_status": "OWNER_CONFIRMED",
        "source_sha256": "s", "target_sha256": "t", "detail_rules_sha256": "r",
    }]
    report = build_detail_rule_proposals(
        queue, decisions, source_sha256="s", target_sha256="t", rules_sha256="r",
    )
    assert report["accepted"] is True
    assert report["proposals"] == []


def test_candidate_decision_requires_regression_and_preserves_context_scope():
    queue = [{**_queue_row(), "context_scope": '["{\"name_es\":\"producto\"}"]'}]
    candidate = build_detail_rule_candidates(
        queue, source_sha256="s", target_sha256="t", rules_sha256="r",
    )[0]
    report = build_detail_rule_proposals(
        queue, [{
            "review_id": candidate["candidate_id"], "decision": "APPROVE",
            "approved_target": "碱性电池", "regression_case_ids": '["DETAIL_TEST_001"]',
            "reviewer": "owner", "reviewed_at": "2026-10-01T00:00:00+00:00",
            "evidence_status": "OWNER_CONFIRMED", "source_sha256": "s",
            "target_sha256": "t", "detail_rules_sha256": "r",
        }], source_sha256="s", target_sha256="t", rules_sha256="r",
        regression_cases=(RegressionCase(
            case_id="DETAIL_TEST_001", field="details",
            source_es="Tipo de batería: alcalina", wrong_zh="旧译法",
            expected_zh="类型: 碱性电池", issue_type="TEST", risk_level="HIGH",
            evidence_status="OWNER_CONFIRMED", policy_version="TEST",
            source_key="tipo de bateria", source_value="alcalina",
        ),),
    )
    assert report["accepted"] is True
    assert report["proposals"][0]["candidate_id"] == candidate["candidate_id"]
    assert report["proposals"][0]["approved_by"] == "owner"


def test_empty_detail_decision_ledger_is_not_accepted():
    report = build_detail_rule_proposals(
        [_queue_row()], [], source_sha256="s", target_sha256="t", rules_sha256="r",
        require_regression=True,
    )
    assert report["accepted"] is False
    assert report["rules_write"] is False
    assert any(error["code"] == "DECISION_LEDGER_EMPTY" for error in report["errors"])

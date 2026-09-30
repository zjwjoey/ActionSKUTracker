from action_tracker.translation.detail_review_contract import validate_detail_rule_decisions


def _queue():
    return [{
        "review_id": "detail-1", "review_kind": "SOURCE_VALUE",
        "source_key_normalized": "color", "source_value_normalized": "rojo",
    }]


def _approved(**extra):
    return {
        "review_id": "detail-1", "decision": "APPROVE",
        "source_sha256": "s", "target_sha256": "t", "detail_rules_sha256": "r",
        "reviewer": "owner", "reviewed_at": "2026-09-30T20:00:00+08:00",
        "evidence_status": "OWNER_CONFIRMED", "approved_target": "红色",
        **extra,
    }


def test_detail_decision_accepts_only_fully_bound_owner_approval():
    result = validate_detail_rule_decisions(
        _queue(), [_approved()], source_sha256="s", target_sha256="t", rules_sha256="r",
    )
    assert result["accepted"] is True
    assert len(result["approved_rows"]) == 1
    assert result["auto_apply"] is False
    assert result["rules_write"] is False


def test_detail_decision_rejects_stale_or_unconfirmed_approval():
    result = validate_detail_rule_decisions(
        _queue(), [_approved(target_sha256="stale", evidence_status="MODEL_OBSERVED")],
        source_sha256="s", target_sha256="t", rules_sha256="r",
    )
    assert result["accepted"] is False
    assert {error["code"] for error in result["errors"]} == {
        "DECISION_HASH_MISMATCH", "APPROVAL_EVIDENCE_NOT_CONFIRMED",
    }
    assert result["approved_rows"] == []


def test_detail_decision_rejects_unknown_and_duplicate_decisions():
    result = validate_detail_rule_decisions(
        _queue(), [_approved(), _approved(), {"review_id": "detail-404", "decision": "REJECT"}],
        source_sha256="s", target_sha256="t", rules_sha256="r",
    )
    assert result["accepted"] is False
    assert {error["code"] for error in result["errors"]} == {
        "DECISION_DUPLICATE", "DECISION_REVIEW_ID_UNKNOWN",
    }


def test_detail_decision_rejects_duplicate_queue_identity():
    result = validate_detail_rule_decisions(
        _queue() + _queue(), [], source_sha256="s", target_sha256="t", rules_sha256="r",
    )
    assert result["accepted"] is False
    assert result["errors"] == [{"code": "QUEUE_REVIEW_ID_DUPLICATE", "review_id": "detail-1"}]

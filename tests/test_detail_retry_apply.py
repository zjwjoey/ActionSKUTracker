import json
from pathlib import Path

import pytest

from action_tracker.orchestrator import detail_retry


def _cfg(tmp_path: Path) -> dict:
    return {
        "paths": {
            "snapshots": tmp_path / "snapshots",
            "master": tmp_path / "master" / "Action_Master.xlsx",
        }
    }


def _parent(cfg: dict, *, qa="PASS", committed=True) -> Path:
    parent = cfg["paths"]["snapshots"] / "2026-08-14" / "run-1"
    parent.mkdir(parents=True)
    (parent / "run_report.json").write_text(json.dumps({
        "qa_state": qa,
        "observation_complete": True,
        "dry_run": not committed,
        "commit_status": "FULL_COMMIT" if committed else "DRY_RUN",
        "today_sku": 2,
    }), encoding="utf-8")
    (parent / "product_updates.csv").write_text(
        "sku,canonical_id,reason,need_detail\n1001,ACT0001001,NEW,true\n",
        encoding="utf-8",
    )
    retry = parent / "detail_retries" / "retry-1"
    retry.mkdir(parents=True)
    (retry / "detail_fetch.jsonl").write_text(json.dumps({"sku": "1001", "detail": {
        "sku": "1001", "name_es": "Nuevo nombre", "spec_es": "40 gramos",
        "desc_es": "Descripción", "details_es": "Detalles", "current_price": 99.99,
    }}) + "\n", encoding="utf-8")
    return parent


def test_apply_detail_retry_updates_only_detail_fields(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    _parent(cfg)
    current = {
        "1001": {"sku": "1001", "canonical_id": "ACT0001001", "name_es": "Old",
                 "current_price": 1.99, "status": "CURRENT"},
        "1002": {"sku": "1002", "canonical_id": "ACT0001002", "current_price": 2.99,
                 "status": "CURRENT"},
    }
    captured = {}
    monkeypatch.setattr(detail_retry.reader, "load_current", lambda path: current)
    monkeypatch.setattr(detail_retry.writer, "write_master", lambda cfg, **kwargs: captured.update(kwargs))

    result = detail_retry.apply_detail_retry(cfg, "run-1")

    assert result["applied_skus"] == 1
    assert result["applied_fields"] == 4
    assert set(captured["updated_records"]) == {"1001", "1002"}
    assert current["1001"]["name_es"] == "Nuevo nombre"
    assert current["1001"]["desc_es"] == "Descripción"
    assert current["1001"]["current_price"] == 1.99
    assert current["1001"]["status"] == "CURRENT"
    assert captured["price_events"] == [] and captured["event_events"] == []


def test_apply_detail_retry_requires_committed_qa_observation(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    _parent(cfg, qa="FAIL")
    monkeypatch.setattr(detail_retry.reader, "load_current", lambda path: {})

    with pytest.raises(ValueError, match="PARENT_OBSERVATION_NOT_QA_PASS"):
        detail_retry.apply_detail_retry(cfg, "run-1")


def test_backfill_missing_details_uses_validated_dry_run_without_overwrite(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    _parent(cfg, committed=False)
    current = {
        "1001": {"sku": "1001", "canonical_id": "ACT0001001", "name_es": "Newer name",
                 "current_price": 1.99, "desc_es": None, "details_es": None, "status": "CURRENT"},
        "1002": {"sku": "1002", "canonical_id": "ACT0001002", "current_price": 2.99,
                 "status": "CURRENT"},
    }
    captured = {}
    monkeypatch.setattr(detail_retry.reader, "load_current", lambda path: current)
    monkeypatch.setattr(detail_retry.writer, "write_master", lambda cfg, **kwargs: captured.update(kwargs))

    result = detail_retry.backfill_missing_details(cfg, "run-1")

    assert result["backfilled_skus"] == 1
    assert result["backfilled_fields"] == 3
    assert current["1001"]["name_es"] == "Newer name"
    assert current["1001"]["desc_es"] == "Descripción"
    assert current["1001"]["details_es"] == "Detalles"
    assert current["1001"]["current_price"] == 1.99
    assert captured["price_events"] == [] and captured["event_events"] == []

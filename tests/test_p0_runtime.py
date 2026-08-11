import csv
import json
from pathlib import Path

import pytest

from action_tracker.orchestrator import daily
from action_tracker.orchestrator import detail_retry
from action_tracker.services.runtime import RunLock


def _cfg(tmp_path):
    return {"paths": {"state": tmp_path / "state", "snapshots": tmp_path / "snapshots",
                        "staging": tmp_path / "staging"},
            "run": {"lock_stale_minutes": 1}, "browser": {"cooldown_seconds": 0, "degraded_recovery_successes": 1},
            "lifecycle": {"max_detail_retries": 1}}


def test_lock_metadata_and_live_owner_are_mutually_exclusive(tmp_path):
    lock = RunLock(tmp_path, stale_minutes=0)
    lock.acquire("one", command="daily-run --dry-run")
    meta = json.loads((tmp_path / "daily-run.lock").read_text(encoding="utf-8"))
    assert meta["run_id"] == "one" and meta["command"] == "daily-run --dry-run" and meta["pid"] > 0
    with pytest.raises(RuntimeError, match="RUN_ALREADY_ACTIVE"):
        RunLock(tmp_path, stale_minutes=0).acquire("two", command="detail-retry")
    lock.release()


def test_stale_dead_pid_lock_is_reclaimed(tmp_path):
    path = tmp_path / "daily-run.lock"
    path.write_text(json.dumps({"run_id": "dead", "pid": 99999999}), encoding="utf-8")
    lock = RunLock(tmp_path, stale_minutes=999)
    lock.acquire("replacement")
    assert json.loads(path.read_text(encoding="utf-8"))["run_id"] == "replacement"
    lock.release()


def test_fatal_finalizer_persists_manifest_without_masking_error(tmp_path):
    cfg = _cfg(tmp_path)
    context = {"run_id": "2026-08-11_010101", "run_date": "2026-08-11", "dry_run": True,
               "started_at": "2026-08-11T01:01:01+02:00", "snap_dir": tmp_path / "snapshots" / "2026-08-11" / "2026-08-11_010101"}
    daily._persist_fatal_run_evidence(cfg, context, ValueError("broken parser"))
    snap = context["snap_dir"]
    assert json.loads((snap / "run_manifest.json").read_text(encoding="utf-8"))["fatal_error"]["type"] == "ValueError"
    assert json.loads((snap / "run_report.json").read_text(encoding="utf-8"))["qa_state"] == "NOT_REACHED"


def _csv(path: Path, rows: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)


def test_detail_retry_only_attempts_parent_pending_items(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    parent = cfg["paths"]["snapshots"] / "2026-08-11" / "parent"
    parent.mkdir(parents=True)
    (parent / "run_report.json").write_text(json.dumps({"run_date": "2026-08-11"}), encoding="utf-8")
    _csv(parent / "product_updates.csv", [
        {"sku": "1", "canonical_id": "A1", "reason": "NEW", "need_detail": "True"},
        {"sku": "2", "canonical_id": "A2", "reason": "DETAIL_REFRESH", "need_detail": "True"},
    ])
    _csv(parent / "products_normalized.csv", [
        {"sku": "1", "product_url": "https://x/1"}, {"sku": "2", "product_url": "https://x/2"},
    ])
    (parent / "detail_fetch.jsonl").write_text(json.dumps({"sku": "1", "detail": {"name_es": "done"}}) + "\n", encoding="utf-8")
    seen = []
    class FakeBrowser:
        def __init__(self, *args, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def manifest(self): return {}
    def fake_fetch(_browser, plans, _baseline, checkpoint_dir, *_args, detail_completed_skus, **_kwargs):
        seen.extend(p["sku"] for p in plans)
        detail_completed_skus.extend(seen)
        for sku in seen:
            (checkpoint_dir / "detail_fetch.jsonl").open("a", encoding="utf-8").write(json.dumps({"sku": sku, "detail": {}}) + "\n")
        return [], {}
    monkeypatch.setattr(detail_retry, "BrowserSession", FakeBrowser)
    monkeypatch.setattr(detail_retry.updater, "fetch_and_merge", fake_fetch)
    report = detail_retry.run_detail_retry(cfg, "parent")
    assert seen == ["2"] and report["planned"] == 1 and report["completed"] == 1
    assert report["detail_retry_pass"] is True
    assert not (cfg["paths"]["state"] / "daily-run.lock").exists()

from pathlib import Path

import pytest

from action_tracker.monitor.structure import discover_categories
from action_tracker.services.access import AccessController, AccessState, CollectionBlocked
from action_tracker.services.runtime import RunLock
from action_tracker.products import updater


class _Page:
    def __init__(self, links): self.links = links
    def evaluate(self, _): return self.links


class _Browser:
    def __init__(self, links): self.page = _Page(links)


class _SleepOnlyBrowser:
    def sleep(self): pass


def test_dynamic_category_discovery_overrides_fallback_count():
    cats, meta = discover_categories(_Browser([
        {"href": "https://www.action.com/es-es/c/hogar/", "name": "Hogar"},
        {"href": "https://www.action.com/es-es/c/nuevo-cat/", "name": "Nuevo cat"},
    ]), {"hogar": "fallback", "old": "fallback"})
    assert cats == {"hogar": "https://www.action.com/es-es/c/hogar/", "nuevo-cat": "https://www.action.com/es-es/c/nuevo-cat/"}
    assert meta["discovery_status"] == "SUCCESS"
    assert meta["fallback_used"] is False


def test_category_discovery_falls_back_without_navigation_links():
    cats, meta = discover_categories(_Browser([]), {"hogar": "https://x/c/hogar/"})
    assert cats == {"hogar": "https://x/c/hogar/"}
    assert meta["discovery_status"] == "DEGRADED"
    assert meta["fallback_used"] is True


def test_403_cooldown_probe_then_blocked(monkeypatch):
    ctl = AccessController(cooldown_seconds=0)
    ctl.record(status=403)
    assert ctl.state == AccessState.COOLDOWN
    ctl.before_navigation()
    assert ctl.state == AccessState.PROBE
    ctl.record(challenge=True)
    assert ctl.state == AccessState.BLOCKED
    with pytest.raises(CollectionBlocked):
        ctl.before_navigation()


def test_probe_recovery_returns_normal():
    ctl = AccessController(cooldown_seconds=0)
    ctl.record(status=429)
    ctl.before_navigation()
    ctl.record()
    assert ctl.state == AccessState.NORMAL


def test_single_run_lock_and_stale_reclaim(tmp_path: Path):
    first = RunLock(tmp_path, stale_minutes=1)
    first.acquire("one")
    with pytest.raises(RuntimeError, match="RUN_ALREADY_ACTIVE"):
        RunLock(tmp_path, stale_minutes=1).acquire("two")
    first.release()
    RunLock(tmp_path, stale_minutes=1).acquire("two")


def test_detail_tasks_stop_when_global_access_is_not_normal(tmp_path: Path):
    ctl = AccessController(cooldown_seconds=0)
    ctl.record(status=429)
    evidence = []
    changes, updated = updater.fetch_and_merge(
        object(), [{"sku": "1001", "canonical_id": "ACT0001001", "reason": "NEW", "need_detail": True,
                    "light": {"product_url": "https://x/p/1001/"}}], {}, tmp_path,
        access_controller=ctl, detail_evidence=evidence)
    assert changes == [] and updated == {}
    assert evidence[0]["error_type"] == "DETAIL_BLOCKED"


def test_degraded_access_still_allows_bounded_detail_attempt(tmp_path: Path, monkeypatch):
    ctl = AccessController(cooldown_seconds=0)
    ctl.record(error=True)
    called = []
    monkeypatch.setattr(updater, "_get_detail", lambda *args: called.append(args[2]) or None)
    updater.fetch_and_merge(_SleepOnlyBrowser(), [{"sku": "1001", "canonical_id": "ACT0001001", "reason": "NEW", "need_detail": True,
                                        "light": {"product_url": "https://x/p/1001/"}}], {}, tmp_path,
                            access_controller=ctl)
    assert called == ["1001"]

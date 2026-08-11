from pathlib import Path

import pytest

from action_tracker.monitor.structure import discover_categories
from action_tracker.services.access import AccessController, AccessState, CollectionBlocked
from action_tracker.services.runtime import RunLock
from action_tracker.products import updater
from action_tracker.services import browser as browser_mod


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


def test_degraded_recovers_after_configured_consecutive_successes():
    ctl = AccessController(degraded_recovery_successes=3)
    ctl.record(error=True)
    ctl.record(); ctl.record()
    assert ctl.state == AccessState.DEGRADED
    ctl.record()
    assert ctl.state == AccessState.NORMAL
    assert ctl.report()["degraded_recovered"] is True


def test_degraded_streak_resets_on_a_further_transient_error():
    ctl = AccessController(degraded_recovery_successes=3)
    ctl.record(error=True); ctl.record(); ctl.record(); ctl.record(error=True)
    assert ctl.success_streak == 0 and ctl.transient_error_count == 2
    ctl.record(); ctl.record()
    assert ctl.state == AccessState.DEGRADED


@pytest.mark.parametrize("issue", [{"status": 429}, {"status": 403}, {"challenge": True}])
def test_degraded_recovery_never_overrides_rate_limit_or_access_block(issue):
    ctl = AccessController(degraded_recovery_successes=3)
    ctl.record(error=True); ctl.record(); ctl.record(); ctl.record(**issue)
    assert ctl.state == AccessState.COOLDOWN
    assert ctl.success_streak == 0


def test_recovered_controller_can_satisfy_normal_observation_state():
    ctl = AccessController(degraded_recovery_successes=2)
    ctl.record(error=True); ctl.record(); ctl.record()
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


def test_persistent_browser_session_has_stable_profile_and_one_reusable_page(tmp_path: Path, monkeypatch):
    class FakePage:
        pass

    class FakeContext:
        def __init__(self):
            self.pages = []
            self.closed = False
        def add_cookies(self, _): pass
        def new_page(self):
            page = FakePage()
            self.pages.append(page)
            return page
        def close(self): self.closed = True

    class FakeChromium:
        def __init__(self): self.calls = []
        def launch_persistent_context(self, **kwargs):
            self.calls.append(kwargs)
            return FakeContext()

    chromium = FakeChromium()
    class FakePlaywright:
        def __init__(self): self.chromium = chromium
        def stop(self): pass
    class FakeStarter:
        def start(self): return FakePlaywright()

    monkeypatch.setattr(browser_mod, "sync_playwright", lambda: FakeStarter())
    profile = tmp_path / "action_es"
    session = browser_mod.BrowserSession({"profile_dir": profile, "headless": False})
    session.start()
    assert chromium.calls[0]["user_data_dir"] == str(profile.resolve())
    assert chromium.calls[0]["headless"] is False
    assert session.manifest()["persistent_context"] is True
    assert session.manifest()["context_strategy"].startswith("one persistent context")
    assert len(session._ctx.pages) == 1
    session.close()


class _Response:
    def __init__(self, status): self.status = status


class _ChallengePage:
    def __init__(self, titles, statuses=None):
        self.titles, self.statuses, self.index, self.reloads = titles, statuses or [200], 0, 0
    def goto(self, *_args, **_kwargs): return _Response(self.statuses[0])
    def title(self): return self.titles[min(self.index, len(self.titles) - 1)]
    def reload(self, *_args, **_kwargs):
        self.reloads += 1; self.index += 1
        return _Response(self.statuses[min(self.index, len(self.statuses) - 1)])


def _challenge_session(page, controller):
    return browser_mod.BrowserSession({"challenge_reloads": 3, "challenge_sleep_ms": 0}, page=page, access_controller=controller)


def test_429_never_reloads():
    page, ctl = _ChallengePage(["normal"], [429]), AccessController(cooldown_seconds=0)
    assert _challenge_session(page, ctl).goto("https://x") is False
    assert page.reloads == 0 and ctl.state == AccessState.COOLDOWN


@pytest.mark.parametrize("title", ["Un momento", "normal product page"])
def test_403_never_reloads_regardless_of_title(title):
    page, ctl = _ChallengePage([title], [403]), AccessController(cooldown_seconds=0)
    assert _challenge_session(page, ctl).goto("https://x") is False
    assert page.reloads == 0 and ctl.state == AccessState.COOLDOWN


def test_200_challenge_reloads_then_records_only_recovered_page_as_success():
    page, ctl = _ChallengePage(["Un momento", "normal product page"]), AccessController(cooldown_seconds=0)
    assert _challenge_session(page, ctl).goto("https://x") is True
    assert page.reloads == 1 and ctl.state == AccessState.NORMAL


def test_200_challenge_exhaustion_trips_controller():
    page, ctl = _ChallengePage(["Un momento"]), AccessController(cooldown_seconds=0)
    assert _challenge_session(page, ctl).goto("https://x") is False
    assert page.reloads == 3 and ctl.state == AccessState.COOLDOWN


def test_challenge_reload_stops_when_controller_state_changes_mid_page():
    ctl = AccessController(cooldown_seconds=0)
    page = _ChallengePage(["Un momento"])
    original_reload = page.reload
    def reload_then_cooldown(*args, **kwargs):
        result = original_reload(*args, **kwargs)
        ctl.state = AccessState.COOLDOWN
        return result
    page.reload = reload_then_cooldown
    assert _challenge_session(page, ctl).goto("https://x") is False
    assert page.reloads == 1 and ctl.state == AccessState.COOLDOWN


@pytest.mark.parametrize("state", [AccessState.COOLDOWN, AccessState.PROBE, AccessState.BLOCKED])
def test_challenge_retry_permission_denies_non_normal_states(state):
    ctl = AccessController(cooldown_seconds=0, state=state)
    assert ctl.allow_challenge_retry() is False

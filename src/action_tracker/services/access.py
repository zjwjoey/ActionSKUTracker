"""Global, single-session collection circuit breaker."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import time


class AccessState(StrEnum):
    NORMAL = "NORMAL"
    DEGRADED = "DEGRADED"
    COOLDOWN = "COOLDOWN"
    PROBE = "PROBE"
    BLOCKED = "BLOCKED"


class CollectionBlocked(RuntimeError):
    pass


@dataclass
class AccessController:
    cooldown_seconds: float = 60.0
    degraded_recovery_successes: int = 3
    state: AccessState = AccessState.NORMAL
    events: list[str] = field(default_factory=list)
    _probe_used: bool = False
    success_streak: int = 0
    transient_error_count: int = 0
    degraded_entered: bool = False
    degraded_recovered: bool = False

    def before_navigation(self) -> None:
        if self.state == AccessState.BLOCKED:
            raise CollectionBlocked("collection circuit is BLOCKED")
        if self.state == AccessState.COOLDOWN:
            time.sleep(self.cooldown_seconds)
            self.state = AccessState.PROBE
            self.events.append("COOLDOWN_COMPLETE")
        if self.state == AccessState.PROBE and self._probe_used:
            self.state = AccessState.BLOCKED
            raise CollectionBlocked("probe already consumed")

    def allow_challenge_retry(self) -> bool:
        """Page-scoped permission for one bounded challenge-page reload."""
        return self.state in (AccessState.NORMAL, AccessState.DEGRADED)

    def record(self, *, status: int | None = None, challenge: bool = False, error: bool = False) -> None:
        if status in (403, 401) or challenge:
            self.success_streak = 0
            if self.state == AccessState.PROBE:
                self.state = AccessState.BLOCKED
                self.events.append("PROBE_BLOCKED")
            else:
                # A restriction after an earlier successful probe starts a
                # genuinely new cooldown cycle with one fresh probe permit.
                self._probe_used = False
                self.state = AccessState.COOLDOWN
                self.events.append("CHALLENGE_OR_403")
            return
        if status == 429:
            self.success_streak = 0
            if self.state == AccessState.PROBE:
                self.state = AccessState.BLOCKED
                self.events.append("PROBE_BLOCKED")
            else:
                self._probe_used = False
                self.state = AccessState.COOLDOWN
                self.events.append("RATE_LIMITED")
            return
        # A response alone is not a reliable success signal; BrowserSession reports
        # success only after the title/challenge check has passed.
        if status is not None:
            return
        if error:
            self.success_streak = 0
            self.transient_error_count += 1
            self.degraded_entered = True
            self.state = AccessState.DEGRADED
            self.events.append("TRANSIENT_ERROR")
            return
        if self.state == AccessState.PROBE:
            self._probe_used = True
            self.state = AccessState.NORMAL
            self.events.append("PROBE_RECOVERED")
            return
        if self.state == AccessState.DEGRADED:
            self.success_streak += 1
            if self.success_streak >= self.degraded_recovery_successes:
                self.state = AccessState.NORMAL
                self.success_streak = 0
                self.degraded_recovered = True
                self.events.append("DEGRADED_RECOVERED")

    def report(self) -> dict:
        return {"final_access_state": self.state.value, "transient_error_count": self.transient_error_count,
                "degraded_entered": self.degraded_entered, "degraded_recovered": self.degraded_recovered,
                "success_streak": self.success_streak}

    @property
    def blocked(self) -> bool:
        return self.state == AccessState.BLOCKED

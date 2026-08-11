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
    state: AccessState = AccessState.NORMAL
    events: list[str] = field(default_factory=list)
    _probe_used: bool = False

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

    def record(self, *, status: int | None = None, challenge: bool = False, error: bool = False) -> None:
        if status in (403, 401) or challenge:
            if self.state == AccessState.PROBE:
                self.state = AccessState.BLOCKED
                self.events.append("PROBE_BLOCKED")
            else:
                self.state = AccessState.COOLDOWN
                self.events.append("CHALLENGE_OR_403")
            return
        if status == 429:
            self.state = AccessState.COOLDOWN
            self.events.append("RATE_LIMITED")
            return
        if error:
            self.state = AccessState.DEGRADED
            self.events.append("TRANSIENT_ERROR")
            return
        if self.state == AccessState.PROBE:
            self._probe_used = True
            self.state = AccessState.NORMAL
            self.events.append("PROBE_RECOVERED")

    @property
    def blocked(self) -> bool:
        return self.state == AccessState.BLOCKED


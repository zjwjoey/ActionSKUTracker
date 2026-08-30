from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class RunState(str, Enum):
    CREATED = "CREATED"
    PREFLIGHT = "PREFLIGHT"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"


STEP_ORDER = ("PREFLIGHT", "BACKUP", "COLLECTION", "QA", "DB_COMMIT", "EXPORT", "IMAGE", "KNOWLEDGE", "AI", "AUTO_APPROVAL", "REVIEW", "REPORT")
STEP_DEPENDENCIES = {step: STEP_ORDER[:i] for i, step in enumerate(STEP_ORDER)}
NON_BLOCKING_STEPS = frozenset({"EXPORT", "IMAGE", "KNOWLEDGE", "AI", "AUTO_APPROVAL", "REVIEW"})
EXIT_CODES = {"SUCCESS": 0, "DEGRADED": 10, "BLOCKED": 20, "FAILED": 30, "RECOVERY_REQUIRED": 40, "CONFIG_ERROR": 50}


@dataclass(frozen=True)
class StepResult:
    status: str = "SUCCESS"
    details: dict[str, Any] = field(default_factory=dict)
    retryable: bool = False
    error_code: str | None = None


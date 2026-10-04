from __future__ import annotations

from dataclasses import MISSING, dataclass, field
from enum import Enum
from typing import Any, Mapping


class Stage(str, Enum):
    PREFLIGHT = "PREFLIGHT"
    BACKUP = "BACKUP"
    EXTRACT = "EXTRACT"
    SOURCE_AUDIT = "SOURCE_AUDIT"
    SOURCE_CLEAN = "SOURCE_CLEAN"
    SOURCE_REAUDIT = "SOURCE_REAUDIT"
    FACT_COMMIT = "FACT_COMMIT"
    DETAIL_PLAN = "DETAIL_PLAN"
    DETAIL_ENRICH = "DETAIL_ENRICH"
    TRANSLATION_SOURCE_AUDIT = "TRANSLATION_SOURCE_AUDIT"
    REGISTRY_INGEST = "REGISTRY_INGEST"
    TRANSLATION_PLAN = "TRANSLATION_PLAN"
    QWEN_TRANSLATE = "QWEN_TRANSLATE"
    TRANSLATION_QA = "TRANSLATION_QA"
    TRANSLATION_POLICY = "TRANSLATION_POLICY"
    TRANSLATION_APPLY = "TRANSLATION_APPLY"
    EXPORT_AUDIT = "EXPORT_AUDIT"
    EXPORT_WRITE = "EXPORT_WRITE"
    REPORT = "REPORT"


STAGES = tuple(item.value for item in Stage)

# Critical boundaries are explicit so a later stage cannot accidentally
# overwrite an earlier source/translation/export blocker with a PASS.
STAGE_DEPENDENCIES: dict[str, tuple[str, ...]] = {
    "SOURCE_AUDIT": ("EXTRACT",),
    "SOURCE_CLEAN": ("SOURCE_AUDIT",),
    "SOURCE_REAUDIT": ("SOURCE_CLEAN",),
    "FACT_COMMIT": ("SOURCE_REAUDIT",),
    "DETAIL_PLAN": ("FACT_COMMIT",),
    "DETAIL_ENRICH": ("DETAIL_PLAN",),
    "TRANSLATION_SOURCE_AUDIT": ("FACT_COMMIT",),
    "REGISTRY_INGEST": ("TRANSLATION_SOURCE_AUDIT",),
    "TRANSLATION_PLAN": ("REGISTRY_INGEST",),
    "QWEN_TRANSLATE": ("TRANSLATION_PLAN",),
    "TRANSLATION_QA": ("QWEN_TRANSLATE",),
    "TRANSLATION_POLICY": ("TRANSLATION_QA",),
    "TRANSLATION_APPLY": ("TRANSLATION_POLICY",),
    "EXPORT_AUDIT": ("FACT_COMMIT", "TRANSLATION_APPLY"),
    "EXPORT_WRITE": ("EXPORT_AUDIT",),
}


class WorkflowState(str, Enum):
    SUCCESS = "SUCCESS"
    SUCCESS_WITH_PENDING = "SUCCESS_WITH_PENDING"
    DEGRADED = "DEGRADED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class WorkflowBlocker:
    stage: str
    code: str
    sku: str | None = None
    field_name: str | None = None
    severity: str = "BLOCKER"
    details: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage, "code": self.code, "sku": self.sku,
            "field": self.field_name, "severity": self.severity, "details": dict(self.details),
        }


@dataclass
class WorkflowContext:
    workflow_run_id: str
    business_date: str
    started_at: str
    extraction_run_id: str | None = None
    collection_run_id: str | None = None
    parent_workflow_run_id: str | None = None
    source_commit_id: str | None = None
    detail_commit_id: str | None = None
    localization_commit_id: str | None = None
    source_snapshot: str | None = None
    database_path: str | None = None
    config_evidence: dict[str, Any] = field(default_factory=dict)
    production_mode: str | None = None
    production_apply: bool = False
    authoritative_skus: set[str] = field(default_factory=set)
    new_skus: set[str] = field(default_factory=set)
    reappeared_skus: set[str] = field(default_factory=set)
    source_ready: bool = False
    fact_ready: bool = False
    presence_ready: bool = False
    translation_ready: bool = False
    export_ready: bool = False
    detail_pending: bool = False
    translation_pending: bool = False
    review_required: bool = False
    apply_pending: bool = False
    export_pending: bool = False
    blockers: list[WorkflowBlocker] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        result = dict(self.__dict__)
        for key in ("authoritative_skus", "new_skus", "reappeared_skus"):
            result[key] = sorted(result[key])
        result["blockers"] = [item.as_dict() if isinstance(item, WorkflowBlocker) else dict(item) for item in result.get("blockers", [])]
        return result

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "WorkflowContext":
        data = dict(value)
        for key in ("authoritative_skus", "new_skus", "reappeared_skus"):
            data[key] = set(str(item) for item in data.get(key, ()))
        data["blockers"] = [item if isinstance(item, WorkflowBlocker) else WorkflowBlocker(
            stage=str(item.get("stage") or "UNKNOWN"), code=str(item.get("code") or "UNKNOWN"),
            sku=item.get("sku"), field_name=item.get("field"), severity=str(item.get("severity") or "BLOCKER"),
            details=dict(item.get("details") or {})) for item in data.get("blockers", [])]
        kwargs = {}
        for key, definition in cls.__dataclass_fields__.items():
            if key in data:
                kwargs[key] = data[key]
            elif definition.default is not MISSING:
                kwargs[key] = definition.default
            elif definition.default_factory is not MISSING:  # type: ignore[comparison-overlap]
                kwargs[key] = definition.default_factory()
        return cls(**kwargs)


@dataclass(frozen=True)
class StageResult:
    status: str = "PASS"
    details: Mapping[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    retryable: bool = False

    @property
    def passed(self) -> bool:
        return self.status in {"PASS", "SKIPPED", "NOT_REQUIRED"}


DEPENDENCY_SATISFIED = frozenset({"PASS", "SKIPPED", "NOT_REQUIRED"})
DEPENDENCY_CONTINUABLE = frozenset({"PENDING", "REVIEW_REQUIRED", "DEGRADED"})
DEPENDENCY_BLOCKING = frozenset({"BLOCKED", "FAILED", "BLOCKED_BY_DEPENDENCY"})


@dataclass
class WorkflowResult:
    context: WorkflowContext
    state: str
    stages: dict[str, StageResult]
    report: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "workflow_run_id": self.context.workflow_run_id,
            "business_date": self.context.business_date,
            "state": self.state,
            "context": self.context.as_dict(),
            "stages": {
                name: {"status": value.status, "details": dict(value.details),
                       "error_code": value.error_code, "retryable": value.retryable}
                for name, value in self.stages.items()
            },
            "report": self.report,
        }

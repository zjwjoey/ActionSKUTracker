from __future__ import annotations

from dataclasses import dataclass, field
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


class WorkflowState(str, Enum):
    SUCCESS = "SUCCESS"
    DEGRADED = "DEGRADED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"


@dataclass
class WorkflowContext:
    workflow_run_id: str
    business_date: str
    started_at: str
    extraction_run_id: str | None = None
    source_commit_id: str | None = None
    detail_commit_id: str | None = None
    localization_commit_id: str | None = None
    source_snapshot: str | None = None
    authoritative_skus: set[str] = field(default_factory=set)
    new_skus: set[str] = field(default_factory=set)
    reappeared_skus: set[str] = field(default_factory=set)
    source_ready: bool = False
    translation_ready: bool = False
    export_ready: bool = False

    def as_dict(self) -> dict[str, Any]:
        result = dict(self.__dict__)
        for key in ("authoritative_skus", "new_skus", "reappeared_skus"):
            result[key] = sorted(result[key])
        return result

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "WorkflowContext":
        data = dict(value)
        for key in ("authoritative_skus", "new_skus", "reappeared_skus"):
            data[key] = set(str(item) for item in data.get(key, ()))
        return cls(**{key: data.get(key) for key in cls.__dataclass_fields__})


@dataclass(frozen=True)
class StageResult:
    status: str = "PASS"
    details: Mapping[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    retryable: bool = False

    @property
    def passed(self) -> bool:
        return self.status in {"PASS", "SKIPPED", "NOT_REQUIRED"}


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

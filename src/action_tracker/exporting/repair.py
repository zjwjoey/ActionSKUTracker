"""Orchestration for the read-only Chinese export repair boundary."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping

from .repair_overrides import apply_override, load_overrides
from .repair_report import ExportRepairReport


class ExportRepairEngine:
    """Apply only source-bound repairs and retain field-level evidence."""

    VERSION = "1.0"

    def __init__(
        self,
        *,
        report: ExportRepairReport | None = None,
        overrides_path: Path | None = None,
        excluded_display_tokens: set[str] | None = None,
    ) -> None:
        self.report = report or ExportRepairReport(rule_version=self.VERSION)
        self.overrides = load_overrides(overrides_path)
        self.excluded_display_tokens = {str(token).casefold() for token in (excluded_display_tokens or set())}

    def apply(
        self,
        *,
        sku: str,
        field: str,
        value: Any,
        source_field: str,
        source: Any,
        source_hash: str | None,
        rule: str,
        repairer: Callable[[Any, Any], Any],
    ) -> Any:
        return self.report.apply(
            sku=sku, field=field, value=value, source_field=source_field,
            source=source, source_hash=source_hash, rule=rule, repairer=repairer,
        )

    def apply_override(
        self, *, sku: str, field: str, value: Any, record: dict[str, Any], source_field: str,
    ) -> Any:
        replaced, row, outcome = apply_override(
            sku=sku, field=field, value=value, record=record, overrides=self.overrides,
        )
        if outcome == "APPLIED" and row is not None:
            self.report.events.append(self.report_event(
                sku=sku, field=field, source_field=source_field, source=record.get(source_field),
                source_hash=row["source_hash"], before=value, after=replaced,
                rule="APPROVED_EXPORT_OVERRIDE",
                approval_source="config/export_repair_overrides.csv",
                approved_by=row.get("approved_by") or None,
                approved_at=row.get("approved_at") or None,
                reason=row.get("reason") or None,
            ))
        elif outcome == "REAPPROVAL_REQUIRED" and row is not None:
            self.report.add_unresolved(
                sku=sku, field=field, source_field=source_field,
                code="OVERRIDE_FIELD_HASH_REAPPROVAL_REQUIRED",
                source=record.get(source_field), target=value, blocking=True,
                message="legacy aggregate source hash requires field-level reapproval",
            )
        elif outcome == "STALE" and row is not None and row.get("source_hash"):
            self.report.add_unresolved(
                sku=sku, field=field, source_field=source_field, code="STALE_OVERRIDE",
                source=record.get(source_field), target=value, blocking=True,
                message="approved override source hash does not match current source",
            )
        return replaced

    def report_event(self, **kwargs: Any):
        from .repair_report import RepairEvent
        return RepairEvent(rule_version=self.VERSION, status="AUTO_REPAIRED", **kwargs)


def default_overrides_path(project_root: Path) -> Path:
    return project_root / "config" / "export_repair_overrides.csv"

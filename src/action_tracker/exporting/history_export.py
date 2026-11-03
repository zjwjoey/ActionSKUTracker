"""Standalone historical Presence export; it never derives today's status."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import tempfile
import yaml
from pathlib import Path
from typing import Any

from .history import PRESENCE_UNKNOWN, HistoryExportError, build_presence_rows, load_presence_history
from .template1 import STANDALONE_HISTORY_HEADERS, write_history_xlsx


def export_history(cfg: dict[str, Any], *, export_date: str) -> dict[str, Any]:
    """Export the configured historical Presence union as a single workbook."""
    _validate_date(export_date)
    from ..operations.storage_foundation import storage_lock, StorageError
    try:
        with storage_lock(Path(cfg['paths']['state']), run_id='history-export-' + export_date):
            return _export_history_locked(cfg, export_date=export_date)
    except StorageError as exc:
        raise HistoryExportError(str(exc)) from exc


def _export_history_locked(cfg: dict[str, Any], *, export_date: str) -> dict[str, Any]:
    output = Path(cfg["paths"]["exports"]) / f"{export_date.replace('-', '')}_Action商品上下架明细.xlsx"
    output.parent.mkdir(parents=True, exist_ok=True)
    from .history_publish import recover_history_publication
    recover_history_publication(output)
    try:
        config_path = Path(cfg.get("history_sources_path") or Path(cfg["project_root"]) / "config" / "history_sources.yaml")
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        include_committed = cfg.get("history_include_committed_observations", raw.get("include_committed_observations", False))
        if not isinstance(include_committed, bool):
            raise HistoryExportError("HISTORY_INCLUDE_COMMITTED_INVALID")
        scoped_cfg = {**cfg, "history_observation_end_date": export_date, "history_include_committed_observations": include_committed}
        history = load_presence_history(scoped_cfg)
        if any(date > export_date for date in history.dates):
            raise HistoryExportError("HISTORY_FUTURE_SOURCE_AFTER_EXPORT_DATE: " + export_date)
        if include_committed is True and export_date not in history.dates:
            raise HistoryExportError("HISTORY_EXPORT_DATE_WITHOUT_VERIFIED_OBSERVATION: " + export_date)
        if include_committed is True and not any(s.date == export_date and s.observation_complete and s.absence_capability for s in history.source_stats):
            raise HistoryExportError("HISTORY_EXPORT_DATE_OBSERVATION_INCOMPLETE: " + export_date)
        rows = build_presence_rows(history)
    except HistoryExportError:
        raise
    fd, name = tempfile.mkstemp(prefix=f'.{output.stem}.', suffix='.preview.xlsx', dir=output.parent)
    os.close(fd)
    temporary = Path(name)
    try:
        write_history_xlsx(temporary, history_rows=rows, history_dates=history.dates, source_stats=history.source_stats)
        verification = verify_history_xlsx(temporary, history_dates=history.dates, expected_skus={row["编号"] for row in rows})
        manifest_path = output.with_suffix(".manifest.json")
        manifest = {
            "template_id": "action_history_presence",
            "template_version": 3,
            "fixed_headers": list(STANDALONE_HISTORY_HEADERS),
            "excluded_months": list(history.excluded_months),
            "output_sha256": _file_hash(temporary),
            "export_date": export_date,
            "history_union_sku_count": len(rows),
            "history_dates": list(history.dates),
            "presence_one_count": sum(1 for row in rows for date in history.dates if row.get(date) == 1),
            "presence_zero_count": sum(1 for row in rows for date in history.dates if row.get(date) == 0),
            "unknown_presence_count": sum(1 for row in rows for date in history.dates if row.get(date) == PRESENCE_UNKNOWN),
            "source_audit_count": len(history.source_stats),
            "seed_path": history.seed_path,
            "seed_row_count": history.seed_row_count,
            "seed_sha256": _file_hash(Path(history.seed_path)) if history.seed_path else None,
            "source_stats": [
                {**stat.__dict__, "sha256": _file_hash(Path(stat.path))}
                for stat in history.source_stats
            ],
            "validation_results": {"workbook": "PASS", "presence_values": "PASS"},
            "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        from .history_publish import publish_history_pair
        publish_history_pair(temporary, output, manifest)
        return {"output": str(output), "manifest": str(manifest_path), "sku_count": len(rows), "date_count": len(history.dates), "verification": verification}
    finally:
        temporary.unlink(missing_ok=True)


def verify_history_xlsx(path: Path, *, history_dates: tuple[str, ...], expected_skus: set[str]) -> dict[str, int]:
    import openpyxl

    workbook = openpyxl.load_workbook(path, read_only=False, data_only=True)
    try:
        if workbook.sheetnames != ["商品上下架明细", "历史来源审计"]:
            raise HistoryExportError("HISTORY_EXPORT_SHEET_MISMATCH")
        ws = workbook["商品上下架明细"]
        headers = [cell.value for cell in ws[1]]
        expected_headers = list(STANDALONE_HISTORY_HEADERS) + [_compact_date(date) for date in history_dates]
        if headers != expected_headers:
            raise HistoryExportError("HISTORY_EXPORT_HEADERS_MISMATCH")
        if ws.freeze_panes != "A2" or not ws.auto_filter.ref:
            raise HistoryExportError("HISTORY_EXPORT_FORMAT_MISMATCH")
        sku_col = headers.index("编号") + 1
        skus = [str(ws.cell(row=row, column=sku_col).value or "").strip() for row in range(2, ws.max_row + 1)]
        if set(skus) != expected_skus or len(skus) != len(set(skus)) or "" in skus:
            raise HistoryExportError("HISTORY_EXPORT_SKU_SET_MISMATCH")
        for date in history_dates:
            col = headers.index(_compact_date(date)) + 1
            values = [ws.cell(row=row, column=col).value for row in range(2, ws.max_row + 1)]
            if any(value not in (0, 1, None) for value in values):
                raise HistoryExportError(f"HISTORY_EXPORT_BAD_PRESENCE: {date}")
        audit = workbook["历史来源审计"]
        if audit.max_row - 1 != len(history_dates):
            raise HistoryExportError("HISTORY_EXPORT_AUDIT_MISMATCH")
        return {"sku_count": len(skus), "date_count": len(history_dates)}
    finally:
        workbook.close()


def _file_hash(path: Path) -> str | None:
    if not path.exists():
        raise HistoryExportError(f"HISTORY_SOURCE_MISSING: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _validate_date(value: str) -> None:
    try:
        dt.date.fromisoformat(value)
    except ValueError as exc:
        raise HistoryExportError(f"HISTORY_EXPORT_DATE_INVALID: {value}") from exc


def _compact_date(value: str) -> str:
    return f"{value[2:4]}.{value[5:7]}.{value[8:10]}"

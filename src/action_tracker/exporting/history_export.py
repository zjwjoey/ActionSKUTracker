"""Standalone historical Presence export; it never derives today's status."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import openpyxl

from .history import (
    PRESENCE_UNKNOWN, HistoryExportError, _history_archive_dir, build_presence_rows,
    filter_history_for_export, load_presence_history,
)
from .template1 import HISTORY_HEADERS, write_history_xlsx


def export_history(cfg: dict[str, Any], *, export_date: str) -> dict[str, Any]:
    """Export the configured historical Presence union as a single workbook."""
    _validate_date(export_date)
    try:
        history = filter_history_for_export(
            load_presence_history(cfg, as_of_date=export_date), cfg, as_of_date=export_date,
        )
        rows = build_presence_rows(history)
    except HistoryExportError:
        raise
    output = Path(cfg["paths"]["exports"]) / f"{export_date.replace('-', '')}_Action商品上下架明细.xlsx"
    temporary = output.with_name(f".{output.stem}.preview.xlsx")
    write_history_xlsx(temporary, history_rows=rows, history_dates=history.dates, source_stats=history.source_stats)
    try:
        verification = verify_history_xlsx(temporary, history_dates=history.dates, expected_skus={row["编号"] for row in rows})
        temporary.replace(output)
    finally:
        if temporary.exists():
            temporary.unlink()

    manifest_path = output.with_suffix(".manifest.json")
    manifest = {
        "template_id": "action_history_presence",
        "template_version": 1,
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
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    archive = archive_history_table(
        cfg, export_date=export_date, history_rows=rows,
        history_dates=history.dates, source_stats=history.source_stats,
    )
    return {"output": str(output), "manifest": str(manifest_path), "archive": archive,
            "sku_count": len(rows), "date_count": len(history.dates), "verification": verification}


def archive_history_table(
    cfg: dict[str, Any], *, export_date: str, history_rows: list[dict[str, Any]],
    history_dates: tuple[str, ...], source_stats: tuple[Any, ...] = (),
) -> dict[str, Any]:
    """Persist the validated history sheet for the next rolling export.

    The archive is a canonical history-only workbook, so Template 1 and the
    standalone export produce the same bytes for a given matrix.  Repeating
    the same date is idempotent; a different matrix for an existing date is a
    hard conflict rather than a silent overwrite.
    """
    archive_dir = _history_archive_dir(cfg)
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_path = archive_dir / f"{export_date.replace('-', '')}_Action商品上下架明细.xlsx"
    temporary = archive_path.with_name(f".{archive_path.stem}.tmp.xlsx")
    write_history_xlsx(temporary, history_rows=history_rows,
                       history_dates=history_dates, source_stats=source_stats)
    new_hash = _workbook_matrix_hash(temporary)
    archive_manifest = archive_path.with_suffix(".manifest.json")
    if archive_path.exists():
        old_hash = None
        if archive_manifest.exists():
            try:
                old_hash = json.loads(archive_manifest.read_text(encoding="utf-8")).get("matrix_sha256")
            except (OSError, UnicodeError, json.JSONDecodeError):
                old_hash = None
        old_hash = old_hash or _workbook_matrix_hash(archive_path)
        temporary.unlink(missing_ok=True)
        if old_hash != new_hash:
            raise HistoryExportError(f"HISTORY_ARCHIVE_DATE_CONFLICT: {export_date}")
        if not archive_manifest.exists():
            archive_manifest.write_text(json.dumps({
                "export_date": export_date, "matrix_sha256": old_hash,
                "history_dates": list(history_dates), "sku_count": len(history_rows),
            }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return {"path": str(archive_path), "manifest": str(archive_manifest),
                "sha256": old_hash, "status": "UNCHANGED"}
    temporary.replace(archive_path)
    archive_manifest.write_text(json.dumps({
        "export_date": export_date, "matrix_sha256": new_hash,
        "history_dates": list(history_dates), "sku_count": len(history_rows),
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"path": str(archive_path), "manifest": str(archive_manifest),
            "sha256": new_hash, "status": "ARCHIVED"}


def _workbook_matrix_hash(path: Path) -> str:
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = workbook["商品上下架明细"]
        rows = [[cell for cell in row] for row in sheet.iter_rows(values_only=True)]
    finally:
        workbook.close()
    if not rows:
        raise HistoryExportError(f"HISTORY_ARCHIVE_EMPTY: {path}")
    headers = rows[0]
    sku_index = headers.index("编号") if "编号" in headers else 1
    date_indexes = [
        index for index, header in enumerate(headers)
        if re.fullmatch(r"\d{2}\.\d{2}\.\d{2}", str(header or "").strip())
    ]
    if not date_indexes:
        raise HistoryExportError(f"HISTORY_ARCHIVE_DATE_COLUMNS_MISSING: {path}")
    matrix = [
        [row[sku_index], *[row[index] for index in date_indexes]]
        for row in rows[1:]
    ]
    payload = json.dumps(
        {"headers": [headers[index] for index in date_indexes], "rows": matrix},
        ensure_ascii=False, sort_keys=False, default=str, separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def verify_history_xlsx(path: Path, *, history_dates: tuple[str, ...], expected_skus: set[str]) -> dict[str, int]:
    import openpyxl

    workbook = openpyxl.load_workbook(path, read_only=False, data_only=True)
    try:
        if workbook.sheetnames != ["商品上下架明细", "历史来源审计"]:
            raise HistoryExportError("HISTORY_EXPORT_SHEET_MISMATCH")
        ws = workbook["商品上下架明细"]
        headers = [cell.value for cell in ws[1]]
        expected_headers = list(HISTORY_HEADERS) + [_compact_date(date) for date in history_dates]
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
            if any(value not in (0, 1, PRESENCE_UNKNOWN) for value in values):
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

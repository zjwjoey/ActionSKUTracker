"""Template 1 编排：历史 Presence union + 今日 ES/ZH 两张清单。"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

from .dictionary_join import build_zh_rows, build_zh_rows_from_localized_source, load_dictionary_context
from .repair import ExportRepairEngine, default_overrides_path
from .repair_report import ExportRepairReport, audit_repaired_rows
from .history import HistoryExportError, build_presence_rows, load_presence_history
from .service import (
    ExportValidationError,
    build_es_rows,
    resolve_formal_source,
    validate_output_rows,
    validate_source_records,
    validate_spanish_source_fields,
    validate_zh_rows_against_source,
    _resolve_image_eligibility,
    _commit_id_for_run,
    _export_repair_allowed_tokens,
)
from .template1 import CATALOG_HEADERS, HISTORY_HEADERS, verify_template1_xlsx, write_template1_xlsx


def export_template1(
    cfg: dict[str, Any], *, export_date: str, run_id: str | None = None,
    with_images: bool = False, selection_id: str | None = None,
    research_release: bool = False,
) -> dict[str, Any]:
    """生成 Template 1 三表工作簿。

    带图模式只在“今日中文清单”嵌入本地 250×250 白底衍生图；
    历史上下架矩阵和今日西班牙语清单保持无图，避免重复资产和体积膨胀。
    """
    try:
        from .profiles import load_profile
        profile = load_profile(cfg, language="es", no_images=True)
        source = resolve_formal_source(cfg, export_date=export_date, requested_run_id=run_id, profile=profile)
        artifact_source_commit_id = source.source_commit_id or _commit_id_for_run(_database_path(cfg), source.run_id)
        records = list(source.records)
        selection_source_commit_id = None
        if selection_id:
            from ..extraction.selections import SelectionService
            selection = SelectionService(_database_path(cfg)).get(selection_id)
            if not selection: raise ExportValidationError(f"SELECTION_NOT_FOUND: {selection_id}")
            members = set(selection["members"]); source_skus = {str(r.get("sku") or "") for r in records}
            missing = sorted(members - source_skus)
            if missing: raise ExportValidationError(f"SELECTION_MEMBER_NOT_IN_FORMAL_SOURCE: {','.join(missing[:10])}")
            records = [r for r in records if str(r.get("sku") or "") in members]
            selection_source_commit_id = selection.get("source_commit_id")
        validate_source_records(records, export_date=export_date)
        validate_spanish_source_fields(records)
        es_rows = build_es_rows(records)
        repair_report = ExportRepairReport(run_id=source.run_id, rule_version=ExportRepairEngine.VERSION)
        repair_allowed_tokens: set[str] = set()
        if source.kind == "SQLITE_CURRENT":
            # SQLite PRIMARY already contains the gated localization projection;
            # do not re-join the file dictionary and risk a split-brain export.
            repair_dictionary = load_dictionary_context(cfg)
            zh_rows, fallback_counts = build_zh_rows_from_localized_source(
                records, category_context=repair_dictionary, repair_report=repair_report,
                repair_overrides_path=default_overrides_path(Path(cfg["project_root"])),
            )
            repair_allowed_tokens = _export_repair_allowed_tokens(repair_dictionary)
            dictionary = None
        else:
            dictionary = load_dictionary_context(cfg)
            repair_allowed_tokens = _export_repair_allowed_tokens(dictionary)
            zh_rows, fallback_counts = build_zh_rows(
                records, dictionary, repair_report=repair_report,
                repair_overrides_path=default_overrides_path(Path(cfg["project_root"])),
            )
        validate_zh_rows_against_source(zh_rows, records)
        validate_output_rows(es_rows)
        validate_output_rows(zh_rows)
        repair_audit = audit_repaired_rows(
            records, zh_rows, allowed_tokens=repair_allowed_tokens,
        )
        unresolved_blocking = sum(1 for item in repair_report.unresolved if item.blocking)
        repair_audit["unresolved_blocking_count"] = unresolved_blocking
        repair_audit["release_ready"] = bool(repair_audit.get("release_ready")) and unresolved_blocking == 0
        repair_report.set_audit(repair_audit)
        repair_report.finalize(records, zh_rows)
        if research_release and not bool(repair_audit.get("release_ready")):
            raise ExportValidationError(
                "TEMPLATE1_EXPORT_REPAIR_GATE_FAILED:"
                f"p0={repair_audit.get('p0_findings', 0)};"
                f"affected_fields={repair_audit.get('blocking_affected_fields', 0)};"
                f"unresolved={repair_audit.get('unresolved_blocking_count', 0)};"
                f"field_pass_rate={repair_audit.get('field_pass_rate', 0):.4f}"
            )
        history = load_presence_history(cfg)
        history_rows = build_presence_rows(
            history, export_date=export_date, current_records=records,
            zh_rows=zh_rows, dictionary=dictionary,
        )
    except (ExportValidationError, HistoryExportError, ValueError) as exc:
        if isinstance(exc, ExportValidationError):
            raise
        raise ExportValidationError(str(exc)) from exc

    current_skus = {str(row.get("编号") or "").strip() for row in es_rows}
    if {str(row.get("编号") or "").strip() for row in zh_rows} != current_skus:
        raise ExportValidationError("TEMPLATE1_ES_ZH_SKU_SET_MISMATCH")
    presence_ones = {row["编号"] for row in history_rows if row.get(export_date) == 1}
    if presence_ones != current_skus:
        raise ExportValidationError("TEMPLATE1_PRESENCE_SET_MISMATCH")

    date_compact = export_date.replace("-", "")
    suffix = "带图" if with_images else "不带图"
    name = f"{date_compact}Action商品全量_三表版_{suffix}.xlsx"
    if selection_id: name = name.replace(".xlsx", f"_Selection_{selection_id}.xlsx")
    output = Path(cfg["paths"]["exports"]) / name
    temporary = output.with_name(f".{output.stem}.preview.xlsx")
    image_root = None
    if with_images:
        image_cfg = cfg.get("images") or {}
        raw_root = image_cfg.get("derivative_root")
        image_base = Path((cfg.get("paths") or {}).get("images") or Path(cfg["project_root"]) / "runtime" / "images")
        image_root = Path(str(raw_root)) if raw_root else image_base / "derivatives"
        if not image_root.is_absolute():
            image_root = Path(cfg["project_root"]) / image_root
        image_root = image_root / "excel_250"
        image_eligibility = _resolve_image_eligibility(cfg, records, image_root)
    else:
        image_eligibility = None
    image_stats = write_template1_xlsx(
        temporary, history_rows=history_rows,
        history_dates=history.dates + ((export_date,) if export_date not in history.dates else ()),
        es_rows=es_rows, zh_rows=zh_rows,
        image_root=image_root, embed_zh_images=with_images, image_eligibility=image_eligibility,
    )
    try:
        verification = verify_template1_xlsx(
            temporary, export_date=export_date, current_skus=current_skus,
            expect_images=with_images, expected_image_count=image_stats["embedded_count"],
        )
        temporary.replace(output)
    finally:
        if temporary.exists():
            temporary.unlink()

    manifest_path = output.with_suffix(".manifest.json")
    repair_report_path = output.with_suffix(".repair-report.json")
    repair_report_path.write_text(
        json.dumps(repair_report.as_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "template_id": "action_full_template_1",
        "template_version": 1,
        "export_date": export_date,
        "run_id": source.run_id,
        "source_kind": source.kind,
        "source_hash": _hash_records(records),
        "history_union_sku_count": len(history_rows),
        "current_valid_sku_count": len(current_skus),
        "new_sku_count": sum(1 for row in history_rows if row.get(export_date) == 1 and row["编号"] not in history.presence_by_sku),
        "presence_one_count": len(presence_ones),
        "es_sku_count": len(es_rows),
        "zh_sku_count": len(zh_rows),
        "es_zh_sku_set_equal": True,
        "zh_image_embedded_count": image_stats["embedded_count"],
        "zh_image_missing_count": image_stats["missing_count"],
        "missing_image_skus": sorted(image_stats.get("missing_skus", [])),
        "with_images": with_images,
        "selection_id": selection_id,
        "selection_source_commit_id": selection_source_commit_id,
        "artifact_source_commit_id": artifact_source_commit_id,
        "image_profile": "excel_250_white_v1" if with_images else None,
        "dictionary_fallback_counts": fallback_counts,
        "history_source_stats": [stat.__dict__ for stat in history.source_stats],
        "history_seed_path": history.seed_path,
        "history_seed_row_count": history.seed_row_count,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "validation_results": {"history": "PASS", "cross_sheet": "PASS", "workbook": "PASS"},
        "export_repair": {
            "engine_version": ExportRepairEngine.VERSION,
            "audit_findings": int(repair_audit.get("audit_findings") or 0),
            "affected_fields": int(repair_audit.get("affected_fields") or 0),
            "affected_skus": int(repair_audit.get("affected_skus") or 0),
            "field_pass_rate": float(repair_audit.get("field_pass_rate") or 0),
            "p0_findings": int(repair_audit.get("p0_findings") or 0),
            "unresolved_blocking_count": int(repair_audit.get("unresolved_blocking_count") or 0),
            "release_ready": bool(repair_audit.get("release_ready")),
            "repair_count": len(repair_report.events),
            "repair_report": repair_report_path.name,
        },
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if selection_id:
        from ..delivery.artifacts import ArtifactService
        ArtifactService(_database_path(cfg)).record(
            artifact_id=f"artifact_{uuid.uuid4().hex}",
            artifact_type="TEMPLATE1_XLSX", file_path=output, row_count=len(current_skus), selection_id=selection_id,
            language="multi", image_profile="excel_250_white_v1" if with_images else None,
            source_commit_id=artifact_source_commit_id, selection_source_commit_id=selection_source_commit_id,
            manifest_path=manifest_path,
        )
    return {
        "output": str(output), "manifest": str(manifest_path), "run_id": source.run_id,
        "sku_count": len(current_skus), "history_sku_count": len(history_rows),
        "profile": "action_full_template_1", "with_images": with_images,
        "image_embedded_count": image_stats["embedded_count"],
        "image_missing_count": image_stats["missing_count"],
        "selection_id": selection_id,
        "repair_report": str(repair_report_path),
        "repair_audit": repair_audit,
    }


def _hash_records(records: list[dict[str, Any]]) -> str:
    payload = json.dumps(sorted(records, key=lambda row: str(row.get("sku") or "")), ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _database_path(cfg: dict[str, Any]) -> Path:
    from ..database.integration import database_path
    return database_path(cfg)


def _compact_date(value: str) -> str:
    return f"{value[2:4]}.{value[5:7]}.{value[8:10]}"

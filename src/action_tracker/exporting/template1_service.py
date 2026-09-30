"""Template 1 编排：历史 Presence union + 今日 ES/ZH 两张清单。"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any

from .dictionary_join import (
    build_zh_rows, build_zh_rows_from_localized_source, confirmed_brand_phrases_by_sku,
    load_dictionary_context,
)
from .history import HistoryExportError, build_presence_rows, filter_history_for_export, load_presence_history
from .history_export import archive_history_table
from .qa_log import build_export_qa_log, render_export_qa_log
from .official_labels import build_official_label_rows, render_official_label_sidecar
from .service import (
    ExportValidationError,
    build_es_rows,
    resolve_formal_source,
    validate_output_rows,
    validate_source_records,
    validate_spanish_source_fields,
    validate_zh_rows_against_source,
    _available_image_skus,
    _publish_export_pair,
)
from .template1 import CATALOG_HEADERS, HISTORY_HEADERS, verify_template1_xlsx, write_template1_xlsx
from .release_gate import evaluate_release_gate


def export_template1(
    cfg: dict[str, Any], *, export_date: str, run_id: str | None = None,
    with_images: bool = False,
) -> dict[str, Any]:
    """生成 Template 1 三表工作簿。

    带图模式只在“今日中文清单”嵌入本地 250×250 白底衍生图；
    历史上下架矩阵和今日西班牙语清单保持无图，避免重复资产和体积膨胀。
    """
    try:
        from .profiles import load_profile
        profile = load_profile(cfg, language="es", no_images=True)
        source = resolve_formal_source(cfg, export_date=export_date, requested_run_id=run_id, profile=profile)
        records = list(source.records)
        validate_source_records(records, export_date=export_date)
        validate_spanish_source_fields(records)
        es_rows = build_es_rows(records)
        if source.kind == "SQLITE_CURRENT":
            # SQLite PRIMARY already contains the gated localization projection;
            # do not re-join the file dictionary and risk a split-brain export.
            zh_rows, fallback_counts = build_zh_rows_from_localized_source(records)
            dictionary = load_dictionary_context(cfg)  # read-only brand QA only
            brand_phrases = confirmed_brand_phrases_by_sku(records, dictionary)
        else:
            dictionary = load_dictionary_context(cfg)
            zh_rows, fallback_counts = build_zh_rows(records, dictionary)
            brand_phrases = confirmed_brand_phrases_by_sku(records, dictionary)
        validate_zh_rows_against_source(zh_rows, records)
        validate_output_rows(es_rows)
        validate_output_rows(zh_rows)
        es_release_gate = evaluate_release_gate(records, es_rows, language="es", strict=source.kind == "SQLITE_CURRENT")
        zh_release_gate = evaluate_release_gate(
            records, zh_rows, language="zh", strict=source.kind == "SQLITE_CURRENT",
            confirmed_brand_phrases_by_sku=brand_phrases,
            approved_terms=dictionary.terms,
        )
        history = filter_history_for_export(
            load_presence_history(cfg, as_of_date=export_date), cfg, as_of_date=export_date,
        )
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

    export_qa_rows = build_export_qa_log(
        {"es": es_release_gate, "zh": zh_release_gate}, fallback_counts,
    )
    export_qa_bytes = render_export_qa_log(export_qa_rows)
    official_label_rows = build_official_label_rows(records)
    official_label_bytes = render_official_label_sidecar(official_label_rows)

    date_compact = export_date.replace("-", "")
    suffix = "带图" if with_images else "不带图"
    output = Path(cfg["paths"]["exports"]) / f"{date_compact}Action商品全量_三表版_{suffix}.xlsx"
    temporary = output.with_name(f".{output.stem}.preview.xlsx")
    qa_log_path = output.with_name(f"{output.stem}_QA_LOG.csv")
    qa_log_temporary = qa_log_path.with_name(f".{qa_log_path.stem}.preview.csv")
    official_labels_path = output.with_name(f"{output.stem}_OFFICIAL_LABELS.csv")
    official_labels_temporary = official_labels_path.with_name(f".{official_labels_path.stem}.preview.csv")
    manifest_path = output.with_suffix(".manifest.json")
    image_root = None
    if with_images:
        image_cfg = cfg.get("images") or {}
        raw_root = image_cfg.get("derivative_root")
        image_base = Path((cfg.get("paths") or {}).get("images") or Path(cfg["project_root"]) / "runtime" / "images")
        image_root = Path(str(raw_root)) if raw_root else image_base / "derivatives"
        if not image_root.is_absolute():
            image_root = Path(cfg["project_root"]) / image_root
        image_root = image_root / "excel_250"
    image_stats = write_template1_xlsx(
        temporary, history_rows=history_rows,
        history_dates=history.dates + ((export_date,) if export_date not in history.dates else ()),
        es_rows=es_rows, zh_rows=zh_rows,
        image_root=image_root, embed_zh_images=with_images,
        allowed_image_skus=_available_image_skus(cfg) if with_images else None,
    )
    try:
        verification = verify_template1_xlsx(
            temporary, export_date=export_date, current_skus=current_skus,
            expect_images=with_images, expected_image_count=image_stats["embedded_count"],
        )
        qa_log_temporary.write_bytes(export_qa_bytes)
        official_labels_temporary.write_bytes(official_label_bytes)
        archive = archive_history_table(
            cfg, export_date=export_date, history_rows=history_rows,
            history_dates=history.dates + ((export_date,) if export_date not in history.dates else ()),
            source_stats=history.source_stats,
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
            "image_profile": "excel_250_white_v1" if with_images else None,
            "dictionary_fallback_counts": fallback_counts,
            "qa_log": {
                "artifact": qa_log_path.name,
                "row_count": len(export_qa_rows),
                "sha256": hashlib.sha256(export_qa_bytes).hexdigest(),
            },
            "official_labels": {
                "artifact": official_labels_path.name,
                "row_count": len(official_label_rows),
                "sha256": hashlib.sha256(official_label_bytes).hexdigest(),
            },
            "history_source_stats": [stat.__dict__ for stat in history.source_stats],
            "history_seed_path": history.seed_path,
            "history_seed_row_count": history.seed_row_count,
            "history_archive": archive,
            "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "quality_status": zh_release_gate["quality_status"],
            "gold_status": zh_release_gate["gold_status"],
            "gold_eligible": bool(zh_release_gate["gold_eligible"]),
            "validation_results": {
                "history": "PASS", "cross_sheet": "PASS", "workbook": "PASS",
                "localization_quality_es": es_release_gate["quality_status"],
                "localization_quality_zh": zh_release_gate["quality_status"],
                "gold_status": zh_release_gate["gold_status"],
                "localization_review_finding_count": len(zh_release_gate["review_findings"]),
            },
            "release_gate": {"es": es_release_gate, "zh": zh_release_gate},
        }
        _publish_export_pair(
            temporary, output, manifest_path, manifest,
            qa_log_preview_path=qa_log_temporary, qa_log_path=qa_log_path,
            sidecar_preview_paths={official_labels_temporary: official_labels_path},
        )
    finally:
        for path in (temporary, qa_log_temporary, official_labels_temporary):
            if path.exists():
                path.unlink()
    return {
        "output": str(output), "manifest": str(manifest_path), "qa_log": str(qa_log_path),
        "qa_log_count": len(export_qa_rows), "archive": archive, "run_id": source.run_id,
        "sku_count": len(current_skus), "history_sku_count": len(history_rows),
        "profile": "action_full_template_1", "with_images": with_images,
        "release_gate_passed": bool(zh_release_gate["passed"]),
        "quality_status": zh_release_gate["quality_status"],
        "gold_status": zh_release_gate["gold_status"],
        "gold_eligible": bool(zh_release_gate["gold_eligible"]),
        "localization_review_finding_count": len(zh_release_gate["review_findings"]),
        "image_embedded_count": image_stats["embedded_count"],
        "image_missing_count": image_stats["missing_count"],
        "missing_image_skus": sorted(image_stats.get("missing_skus", [])),
    }


def _hash_records(records: list[dict[str, Any]]) -> str:
    payload = json.dumps(sorted(records, key=lambda row: str(row.get("sku") or "")), ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _compact_date(value: str) -> str:
    return f"{value[2:4]}.{value[5:7]}.{value[8:10]}"

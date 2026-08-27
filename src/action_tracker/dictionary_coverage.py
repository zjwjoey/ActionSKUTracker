"""统计正式 CURRENT 的 AI-Free 字典覆盖率；只读来源，不写 Master。"""
from __future__ import annotations

import csv
import datetime as dt
import json
from pathlib import Path
from typing import Any

from .dictionary_resolver import RecordResolution, resolve_record
from .exporting.service import ExportValidationError, resolve_formal_source
from .exporting.profiles import load_profile
from .exporting.dictionary_join import load_dictionary_context


def dictionary_coverage(cfg: dict[str, Any], *, export_date: str | None = None, run_id: str | None = None) -> dict[str, Any]:
    profile = load_profile(cfg, language="es", no_images=True)
    date = export_date or _date_from_run(cfg, run_id)
    source = resolve_formal_source(cfg, export_date=date, requested_run_id=run_id, profile=profile)
    context = load_dictionary_context(cfg)
    resolutions = [resolve_record(dict(record), context) for record in source.records]
    report = _build_report(resolutions, source, date, context.content_hash)
    report_dir = Path(cfg["paths"]["dictionary"]) / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    stem = f"dictionary_coverage_{date}"
    (report_dir / f"{stem}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_csv(report_dir / f"{stem}.csv", resolutions)
    return report


def _build_report(resolutions: list[RecordResolution], source: Any, date: str, dictionary_hash: str) -> dict[str, Any]:
    total = len(resolutions)
    auto = sum(item.readiness == "AUTO_READY" for item in resolutions)
    return {
        "date": date,
        "run_id": source.run_id,
        "source_kind": source.kind,
        "dictionary_hash": dictionary_hash,
        "total_current_skus": total,
        "auto_ready_skus": auto,
        "auto_ready_rate": round(auto / total, 6) if total else 0,
        "review_required": sum(item.readiness == "REVIEW_REQUIRED" for item in resolutions),
        "source_blocked": sum(item.readiness == "SOURCE_BLOCKED" for item in resolutions),
        "exact_product_dictionary_hit": sum(all(item.fields[key].source == "product_dictionary" for key in ("name", "spec")) for item in resolutions),
        "manual_override_hit": sum(any(field.source == "manual_override" for field in item.fields.values()) for item in resolutions),
        "category_rule_hit": sum(any(item.fields[key].source == "category_dictionary" for key in ("cat1", "cat2")) for item in resolutions),
        "term_rule_hit": sum(item.fields["unit_price"].source == "term_dictionary" for item in resolutions),
        "valid_cached_model_hit": sum(any(field.source == "model_cache" for field in item.fields.values()) for item in resolutions),
        "model_cache_dependency_rate": round(sum(any(field.source == "model_cache" for field in item.fields.values()) for item in resolutions) / total, 6) if total else 0,
        "source_damaged": sum("SOURCE_DAMAGED" in item.review_reasons for item in resolutions),
        "source_polluted": sum("SOURCE_POLLUTED" in item.review_reasons for item in resolutions),
        "source_hash_changed": sum(item.source_hash_status != "MATCH" for item in resolutions),
        "missing_name": sum(item.fields["name"].status == "MISSING" for item in resolutions),
        "missing_category": sum(item.fields["cat1"].status == "MISSING" or item.fields["cat2"].status == "MISSING" for item in resolutions),
        "missing_spec": sum(item.fields["spec"].status == "MISSING" for item in resolutions),
        "unknown_brand": sum(item.fields["brand"].status in {"MISSING", "REVIEW"} for item in resolutions),
        "unknown_term": sum(item.fields["unit_price"].status == "FALLBACK" for item in resolutions),
        "review_reason_counts": _reason_counts(resolutions),
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }


def _write_csv(path: Path, resolutions: list[RecordResolution]) -> None:
    headers = ["sku", "auto_ready", "name_status", "cat1_status", "cat2_status", "spec_status", "brand_status", "source_hash_status", "source_quality_status", "review_reason"]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        for item in resolutions:
            writer.writerow({
                "sku": item.sku, "auto_ready": item.readiness == "AUTO_READY",
                "name_status": item.fields["name"].status, "cat1_status": item.fields["cat1"].status,
                "cat2_status": item.fields["cat2"].status, "spec_status": item.fields["spec"].status,
                "brand_status": item.fields["brand"].status, "source_hash_status": item.source_hash_status,
                "source_quality_status": item.source_quality_status, "review_reason": "|".join(item.review_reasons),
            })


def _reason_counts(resolutions: list[RecordResolution]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in resolutions:
        for reason in item.review_reasons:
            counts[reason] = counts.get(reason, 0) + 1
    return dict(sorted(counts.items()))


def _date_from_run(cfg: dict[str, Any], run_id: str | None) -> str:
    if run_id:
        matches = list(Path(cfg["paths"]["snapshots"]).glob(f"*/{run_id}/run_report.json"))
        if len(matches) != 1:
            raise ExportValidationError(f"FORMAL_RUN_NOT_FOUND: {run_id}")
        report = json.loads(matches[0].read_text(encoding="utf-8"))
        return str(report.get("run_date") or report.get("observation_date") or "")
    current = Path(cfg["paths"]["master"])
    if not current.exists():
        raise ExportValidationError("MASTER_MISSING")
    # Resolve the latest successful run through a stable date embedded in run_id.
    reports = sorted(Path(cfg["paths"]["snapshots"]).glob("*/run_report.json"), reverse=True)
    for path in reports:
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if report.get("commit_status") == "FULL_COMMIT" and report.get("dry_run") is False:
            return str(report.get("run_date") or report.get("observation_date") or path.parent.parent.name)
    raise ExportValidationError("FORMAL_RUN_NOT_FOUND")

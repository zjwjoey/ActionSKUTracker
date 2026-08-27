"""字典应用预览层：只生成字段级变更，不直接写正式 Master。"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any

from .dictionary_resolver import RecordResolution, resolve_record
from .exporting.dictionary_join import load_dictionary_context
from .exporting.profiles import load_profile
from .exporting.service import ExportValidationError, resolve_formal_source


class DictionaryApplyError(RuntimeError):
    """字典 Apply 预览或 Gate 失败。"""


def dictionary_apply(cfg: dict[str, Any], *, run_id: str, dry_run: bool = True) -> dict[str, Any]:
    if not dry_run:
        # 正式 Master 写入必须单独完成审计、diff、备份和原子替换验收；
        # 本阶段明确只提供预览，避免调用者误以为已完成写入。
        raise DictionaryApplyError("FORMAL_DICTIONARY_APPLY_NOT_ENABLED")
    profile = load_profile(cfg, language="es", no_images=True)
    try:
        source = resolve_formal_source(cfg, export_date=_run_date(cfg, run_id), requested_run_id=run_id, profile=profile)
    except ExportValidationError as exc:
        raise DictionaryApplyError(str(exc)) from exc
    context = load_dictionary_context(cfg)
    resolutions = [resolve_record(dict(record), context) for record in source.records]
    output_dir = Path(cfg["paths"]["dictionary"]) / "apply" / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    preview_rows = _preview_rows(source.records, resolutions)
    review_rows = _review_rows(resolutions)
    _write_csv(output_dir / "apply_preview.csv", ["sku", "field", "old_value", "new_value", "source", "reason"], preview_rows)
    _write_csv(output_dir / "review_required.csv", ["sku", "readiness", "field", "status", "source", "reason"], review_rows)
    manifest = {
        "run_id": run_id,
        "date": _run_date(cfg, run_id),
        "dry_run": True,
        "master_hash_before": _master_hash(cfg),
        "master_hash_after": None,
        "dictionary_hash": context.content_hash,
        "auto_ready_count": sum(item.readiness == "AUTO_READY" for item in resolutions),
        "applied_sku_count": len({row["sku"] for row in preview_rows}),
        "applied_field_count": len(preview_rows),
        "review_required_count": sum(item.readiness == "REVIEW_REQUIRED" for item in resolutions),
        "blocked_count": sum(item.readiness == "SOURCE_BLOCKED" for item in resolutions),
        "formal_write": False,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    (output_dir / "apply_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"run_id": run_id, "dry_run": True, "output_dir": str(output_dir), **{key: manifest[key] for key in ("auto_ready_count", "applied_sku_count", "applied_field_count", "review_required_count", "blocked_count")}}


def _preview_rows(records: list[dict[str, Any]] | tuple[dict[str, Any], ...], resolutions: list[RecordResolution]) -> list[dict[str, str]]:
    by_sku = {str(record.get("sku") or ""): record for record in records}
    field_map = {"name": "name_zh", "cat1": "cat1_zh", "cat2": "cat2_zh", "spec": "spec_zh", "unit_price": "unit_price", "description": "desc_zh", "details": "details_zh"}
    rows: list[dict[str, str]] = []
    for item in resolutions:
        if item.readiness != "AUTO_READY":
            continue
        record = by_sku[item.sku]
        for field, result in item.fields.items():
            if field == "brand" or result.status != "READY" or result.source in {"fallback", "none", "missing"}:
                continue
            old = str(record.get(field_map.get(field, "")) or "").strip()
            if old == result.value:
                continue
            rows.append({"sku": item.sku, "field": field, "old_value": old, "new_value": result.value, "source": result.source, "reason": "AUTO_READY 字段预览"})
    return rows


def _review_rows(resolutions: list[RecordResolution]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item in resolutions:
        if item.readiness == "AUTO_READY":
            continue
        for reason in item.review_reasons or (item.readiness,):
            field = _field_for_reason(reason)
            result = item.fields.get(field)
            rows.append({"sku": item.sku, "readiness": item.readiness, "field": field, "status": result.status if result else "BLOCKED", "source": result.source if result else "source_quality", "reason": reason})
    return rows


def _field_for_reason(reason: str) -> str:
    return {"NAME_REVIEW": "name", "SPEC_REVIEW": "spec", "CATEGORY_REVIEW": "cat1", "BRAND_CANDIDATE": "brand", "TERM_REVIEW": "unit_price"}.get(reason, "")


def _write_csv(path: Path, headers: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def _run_date(cfg: dict[str, Any], run_id: str) -> str:
    matches = list(Path(cfg["paths"]["snapshots"]).glob(f"*/{run_id}/run_report.json"))
    if len(matches) != 1:
        raise DictionaryApplyError(f"FORMAL_RUN_NOT_FOUND: {run_id}")
    report = json.loads(matches[0].read_text(encoding="utf-8"))
    return str(report.get("run_date") or report.get("observation_date") or matches[0].parent.parent.name)


def _master_hash(cfg: dict[str, Any]) -> str | None:
    path = Path(cfg["paths"]["master"])
    if not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()

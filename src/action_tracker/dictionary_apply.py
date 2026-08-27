"""Auditable dictionary Apply preview and the disabled-by-default commit gate."""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

from .dictionary_resolver import RecordResolution, resolve_record
from .excel.reader import load_current
from .excel.writer import commit_master, stage_master
from .exporting.dictionary_join import load_dictionary_context
from .exporting.profiles import load_profile
from .exporting.service import ExportValidationError, resolve_formal_source
from .services.runtime import RunLock


class DictionaryApplyError(RuntimeError):
    """字典 Apply 预览或 Gate 失败。"""


# Apply is limited to derived Chinese fields.  Official Spanish facts,
# prices, URLs, lifecycle and Presence are immutable safety invariants.
ALLOWLIST = {
    "name": "name_zh", "cat1": "cat1_zh", "cat2": "cat2_zh", "spec": "spec_zh",
    "description": "desc_zh", "details": "details_zh",
}
IMMUTABLE_FIELDS = (
    "sku", "canonical_id", "name_es", "cat1_es", "cat2_es", "spec_es", "current_price",
    "original_price", "unit_price", "product_url", "image_url", "status", "first_seen", "last_seen",
    "is_new_badge", "promotion", "sustainable", "discount", "raw_tags", "presence",
)


def dictionary_apply(cfg: dict[str, Any], *, run_id: str, dry_run: bool = True) -> dict[str, Any]:
    if not dry_run and not bool((cfg.get("dictionary_apply") or {}).get("production_enabled", False)):
        raise DictionaryApplyError("PRODUCTION_DICTIONARY_APPLY_DISABLED")
    profile = load_profile(cfg, language="es", no_images=True)
    try:
        source = resolve_formal_source(cfg, export_date=_run_date(cfg, run_id), requested_run_id=run_id, profile=profile)
    except ExportValidationError as exc:
        raise DictionaryApplyError(str(exc)) from exc
    context = load_dictionary_context(cfg)
    resolutions = [resolve_record(dict(record), context) for record in source.records]
    master_path = Path(cfg["paths"]["master"])
    before_hash = _master_hash(cfg)
    master_records = load_current(master_path) if master_path.exists() else {}
    immutable_count = _immutable_diff_count(source.records, master_records)
    output_dir = Path(cfg["paths"]["dictionary"]) / "apply" / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    preview_rows, summary = _preview_rows(source.records, resolutions)
    review_rows = _review_rows(resolutions)
    diff_headers = ["sku", "field", "old_value", "new_value", "source", "resolver_status", "reason"]
    _write_csv(output_dir / "apply_preview.csv", diff_headers, preview_rows)
    _write_csv(output_dir / "field_diff.csv", diff_headers, preview_rows)
    _write_csv(output_dir / "review_required.csv", ["sku", "readiness", "field", "status", "source", "reason"], review_rows)
    auto_count = sum(item.readiness == "AUTO_READY" for item in resolutions)
    review_count = sum(item.readiness == "REVIEW_REQUIRED" for item in resolutions)
    blocked_count = sum(item.readiness == "SOURCE_BLOCKED" for item in resolutions)
    run_date = _run_date(cfg, run_id)
    manifest = {
        "run_id": run_id, "run_date": run_date, "date": run_date, "dry_run": dry_run,
        "master_hash_before": before_hash, "temporary_master_hash": before_hash if dry_run else None,
        "master_hash_after_if_committed": None, "master_hash_after": None,
        "dictionary_hash": context.content_hash, "dictionary_baseline_hash": _dictionary_baseline_hash(cfg),
        "total_current_skus": len(source.records), "auto_ready_count": auto_count,
        "review_required_count": review_count, "source_blocked_count": blocked_count,
        "preview_field_count": len(preview_rows) + summary["unchanged_field_count"],
        "actual_changed_field_count": summary["actual_changed_field_count"],
        "unchanged_field_count": summary["unchanged_field_count"], "immutable_fact_change_count": immutable_count,
        "field_change_summary": summary["fields"], "applied_sku_count": len({row["sku"] for row in preview_rows}),
        "applied_field_count": len(preview_rows), "formal_write": False,
        "production_enabled": bool((cfg.get("dictionary_apply") or {}).get("production_enabled", False)),
        "committed": False, "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    if not dry_run:
        errors = _gate_errors(cfg, source, resolutions, context, master_records, before_hash)
        if errors:
            raise DictionaryApplyError("DICTIONARY_APPLY_GATE_REJECTED: " + ",".join(errors))
        committed_hash = _commit_allowlisted(cfg, source.records, resolutions, master_records, before_hash, run_id)
        manifest.update({"temporary_master_hash": committed_hash, "master_hash_after_if_committed": committed_hash,
                         "master_hash_after": committed_hash, "formal_write": True, "committed": True})
    (output_dir / "apply_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"run_id": run_id, "dry_run": dry_run, "output_dir": str(output_dir), **{
        key: manifest[key] for key in ("auto_ready_count", "applied_sku_count", "applied_field_count", "review_required_count", "source_blocked_count", "actual_changed_field_count")
    }}


def _preview_rows(records: Iterable[dict[str, Any]], resolutions: list[RecordResolution]) -> tuple[list[dict[str, str]], dict[str, Any]]:
    by_sku = {str(record.get("sku") or ""): record for record in records}
    rows: list[dict[str, str]] = []
    fields = {field: {"ACTUAL_CHANGE": 0, "UNCHANGED": 0, "BLANK_TO_VALUE": 0, "VALUE_TO_NEW_VALUE": 0} for field in ALLOWLIST}
    for item in resolutions:
        if item.readiness != "AUTO_READY":
            continue
        record = by_sku[item.sku]
        for field, target in ALLOWLIST.items():
            result = item.fields.get(field)
            if not result or result.status != "READY" or result.source in {"fallback", "none", "missing"}:
                continue
            old, new = str(record.get(target) or "").strip(), str(result.value or "").strip()
            if old == new:
                fields[field]["UNCHANGED"] += 1
                continue
            fields[field]["ACTUAL_CHANGE"] += 1
            kind = "BLANK_TO_VALUE" if not old else "VALUE_TO_NEW_VALUE"
            fields[field][kind] += 1
            rows.append({"sku": item.sku, "field": target, "old_value": old, "new_value": new,
                         "source": result.source, "resolver_status": item.readiness, "reason": "AUTO_READY 字段预览"})
    return rows, {"fields": fields, "actual_changed_field_count": len(rows),
                  "unchanged_field_count": sum(item["UNCHANGED"] for item in fields.values())}


def _review_rows(resolutions: list[RecordResolution]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item in resolutions:
        if item.readiness == "AUTO_READY":
            continue
        for reason in item.review_reasons or (item.readiness,):
            field = _field_for_reason(reason)
            result = item.fields.get(field)
            rows.append({"sku": item.sku, "readiness": item.readiness, "field": field,
                         "status": result.status if result else "BLOCKED", "source": result.source if result else "source_quality", "reason": reason})
    return rows


def _field_for_reason(reason: str) -> str:
    return {"NAME_REVIEW": "name", "SPEC_REVIEW": "spec", "CATEGORY_REVIEW": "cat1", "BRAND_CANDIDATE": "brand", "TERM_REVIEW": "unit_price"}.get(reason, "")


def _gate_errors(cfg: dict[str, Any], source: Any, resolutions: list[RecordResolution], context: Any,
                master_records: dict[str, dict[str, Any]], before_hash: str | None) -> list[str]:
    errors: list[str] = []
    report = _read_json(source.directory / "run_report.json") if source.directory else {}
    qa = _read_json(source.directory / "qa_report.json") if source.directory else {}
    if report.get("dry_run") is not False:
        errors.append("RUN_NOT_FORMAL")
    if report.get("commit_status") != "FULL_COMMIT":
        errors.append("COMMIT_STATUS_NOT_FULL_COMMIT")
    allowed = set(load_profile(cfg, language="es", no_images=True).source_policy.get("allowed_qa_states") or ("PASS", "PASS_PRESENCE_ONLY"))
    if not qa.get("passed") or qa.get("state") not in allowed:
        errors.append("QA_NOT_PASS")
    audit_files = sorted(Path(cfg["paths"]["dictionary"]).glob("audit_report_*.json"), reverse=True)
    if not audit_files or int((_read_json(audit_files[0]).get("summary") or {}).get("fail") or 0) != 0:
        errors.append("DICTIONARY_AUDIT_NOT_PASS")
    if not (Path(cfg["paths"]["dictionary_baseline"]) / "baseline_manifest.json").exists():
        errors.append("DICTIONARY_BASELINE_NOT_AUDITABLE")
    if any(item.readiness != "AUTO_READY" for item in resolutions):
        errors.append("RESOLVER_NOT_AUTO_READY")
    if set(master_records) != {str(record.get("sku") or "") for record in source.records}:
        errors.append("TARGET_CURRENT_SKU_SET_MISMATCH")
    if not before_hash or _master_hash_from_path(Path(cfg["paths"]["master"])) != before_hash:
        errors.append("MASTER_CHANGED_CONCURRENTLY")
    if not bool((cfg.get("dictionary_apply") or {}).get("production_enabled", False)):
        errors.append("PRODUCTION_DICTIONARY_APPLY_DISABLED")
    return errors


def _commit_allowlisted(cfg: dict[str, Any], records: Iterable[dict[str, Any]], resolutions: list[RecordResolution],
                        master_records: dict[str, dict[str, Any]], before_hash: str, run_id: str) -> str:
    updated = {sku: dict(record) for sku, record in master_records.items()}
    for item in resolutions:
        if item.readiness != "AUTO_READY" or item.sku not in updated:
            continue
        for field, target in ALLOWLIST.items():
            result = item.fields.get(field)
            if result and result.status == "READY" and result.source not in {"fallback", "none", "missing"}:
                updated[item.sku][target] = result.value
    lock = RunLock(Path(cfg["paths"]["state"]), stale_minutes=int((cfg.get("run") or {}).get("lock_stale_minutes", 180)))
    lock.acquire(run_id, command="dictionary-apply")
    tmp: Path | None = None
    try:
        if _master_hash_from_path(Path(cfg["paths"]["master"])) != before_hash:
            raise DictionaryApplyError("MASTER_CHANGED_CONCURRENTLY")
        tmp = stage_master(cfg, updated_records=updated, price_events=[], event_events=[])
        _assert_master_safe(Path(cfg["paths"]["master"]), tmp)
        commit_master(tmp, Path(cfg["paths"]["master"]))
        tmp = None
        return _master_hash_from_path(Path(cfg["paths"]["master"])) or ""
    finally:
        lock.release()
        if tmp and tmp.exists():
            tmp.unlink()


def _assert_master_safe(before: Path, staged: Path) -> None:
    old, new = load_current(before), load_current(staged)
    if list(old) != list(new) or set(old) != set(new):
        raise DictionaryApplyError("IMMUTABLE_SKU_ORDER_OR_SET_CHANGED")
    for sku in old:
        for field in IMMUTABLE_FIELDS:
            if _norm(old[sku].get(field)) != _norm(new[sku].get(field)):
                raise DictionaryApplyError(f"IMMUTABLE_FACT_CHANGED:{sku}:{field}")


def _immutable_diff_count(source_records: Iterable[dict[str, Any]], master_records: dict[str, dict[str, Any]]) -> int:
    count = 0
    for source in source_records:
        current = master_records.get(str(source.get("sku") or ""))
        if not current:
            count += 1
            continue
        count += sum(_norm(source.get(field)) != _norm(current.get(field)) for field in IMMUTABLE_FIELDS)
    return count


def _norm(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_csv(path: Path, headers: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def _run_date(cfg: dict[str, Any], run_id: str) -> str:
    matches = list(Path(cfg["paths"]["snapshots"]).glob(f"*/{run_id}/run_report.json"))
    if len(matches) != 1:
        raise DictionaryApplyError(f"FORMAL_RUN_NOT_FOUND: {run_id}")
    report = _read_json(matches[0])
    return str(report.get("run_date") or report.get("observation_date") or matches[0].parent.parent.name)


def _master_hash(cfg: dict[str, Any]) -> str | None:
    path = Path(cfg["paths"]["master"])
    return _master_hash_from_path(path) if path.exists() else None


def _master_hash_from_path(path: Path) -> str | None:
    if not path.exists():
        return None
    return _hash(path)


def _dictionary_baseline_hash(cfg: dict[str, Any]) -> str | None:
    path = Path(cfg["paths"]["dictionary_baseline"]) / "baseline_manifest.json"
    return _hash(path) if path.exists() else None


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()

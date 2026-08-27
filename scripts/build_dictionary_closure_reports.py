"""Build auditable review/source-blocked closure reports for one formal run.

This script is deliberately conservative: it compares trusted Spanish source
fields only.  It never back-translates Chinese values or invents a repair.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from action_tracker.config import load_settings
from action_tracker.dictionary_resolver import resolve_record
from action_tracker.exporting.dictionary_join import _fact_source_hash, load_dictionary_context
from action_tracker.exporting.profiles import load_profile
from action_tracker.exporting.service import resolve_formal_source

FIELDS = ("name_es", "cat1_es", "cat2_es", "spec_es")
PRODUCT_FIELDS = {
    "name_es": "name_es_raw", "cat1_es": "cat1_es", "cat2_es": "cat2_es", "spec_es": "spec_es_raw",
}


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _format_norm(value: Any) -> str:
    value = _text(value).casefold()
    value = re.sub(r"\s+", " ", value)
    return re.sub(r"[^\w\s]", "", value, flags=re.UNICODE)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _source_comparison(record: dict[str, Any], product: dict[str, str]) -> tuple[str, list[str], str]:
    changed: list[str] = []
    format_only = True
    evidence: dict[str, Any] = {}
    for field in FIELDS:
        current, old = _text(record.get(field)), _text(product.get(PRODUCT_FIELDS[field]))
        evidence[field] = {"current": current, "dictionary": old}
        if current != old:
            changed.append(field)
            if _format_norm(current) != _format_norm(old):
                format_only = False
    if not changed:
        return "MATCH", [], _json(evidence)
    if format_only:
        return "A_FORMAT_ONLY", changed, _json(evidence)
    if "spec_es" in changed:
        return "B_SPEC_CHANGE", changed, _json(evidence)
    if "name_es" in changed:
        return "C_NAME_CHANGE", changed, _json(evidence)
    if "cat1_es" in changed or "cat2_es" in changed:
        return "D_CATEGORY_CHANGE", changed, _json(evidence)
    return "E_SOURCE_ISSUE", changed, _json(evidence)


def build_reports(cfg: dict[str, Any], *, run_id: str) -> dict[str, Any]:
    report_matches = list(Path(cfg["paths"]["snapshots"]).glob(f"*/{run_id}/run_report.json"))
    if len(report_matches) != 1:
        raise ValueError(f"FORMAL_RUN_NOT_FOUND: {run_id}")
    run_report = json.loads(report_matches[0].read_text(encoding="utf-8"))
    date = str(run_report.get("run_date") or run_report.get("observation_date") or report_matches[0].parent.parent.name)
    profile = load_profile(cfg, language="es", no_images=True)
    source = resolve_formal_source(cfg, export_date=date, requested_run_id=run_id, profile=profile)
    context = load_dictionary_context(cfg)
    resolutions = [resolve_record(dict(record), context) for record in source.records]
    by_sku = {str(record.get("sku") or ""): record for record in source.records}
    report_dir = Path(cfg["paths"]["dictionary"]) / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    closure_rows: list[dict[str, Any]] = []
    for item in resolutions:
        if item.readiness == "AUTO_READY":
            continue
        record = by_sku[item.sku]
        product = context.product_by_sku.get(item.sku, {})
        change_kind, changed_fields, evidence = _source_comparison(record, product)
        reasons = list(item.review_reasons)
        if item.source_quality_status in {"SOURCE_DAMAGED", "SOURCE_POLLUTED"}:
            action, auto, human = "HOLD_SOURCE_BLOCKED", False, True
        elif change_kind == "A_FORMAT_ONLY":
            action, auto, human = "VERIFY_FORMAT_ONLY_WITH_SOURCE_EVIDENCE", True, False
        elif change_kind in {"B_SPEC_CHANGE", "C_NAME_CHANGE", "D_CATEGORY_CHANGE", "E_SOURCE_ISSUE"}:
            action, auto, human = "REVIEW_SOURCE_CHANGE", False, True
        else:
            action, auto, human = "REVIEW_DICTIONARY_FIELD", False, True
        closure_rows.append({
            "sku": item.sku, "review_reasons": "|".join(reasons),
            "name_es": _text(record.get("name_es")), "current_name_zh": item.fields["name"].value,
            "spec_es": _text(record.get("spec_es")), "current_spec_zh": item.fields["spec"].value,
            "cat1_es": _text(record.get("cat1_es")), "cat2_es": _text(record.get("cat2_es")),
            "cat1_zh": item.fields["cat1"].value, "cat2_zh": item.fields["cat2"].value,
            "brand_id": _text(product.get("brand_id")),
            "old_source_hash": _text(product.get("source_hash")), "current_source_hash": _fact_source_hash(record),
            "suggested_action": f"{action}:{change_kind}", "can_auto_resolve": str(auto).lower(),
            "requires_human": str(human).lower(), "evidence": evidence,
        })
    closure_headers = ["sku", "review_reasons", "name_es", "current_name_zh", "spec_es", "current_spec_zh",
                       "cat1_es", "cat2_es", "cat1_zh", "cat2_zh", "brand_id", "old_source_hash",
                       "current_source_hash", "suggested_action", "can_auto_resolve", "requires_human", "evidence"]
    _write(report_dir / "review_closure_report.csv", closure_headers, closure_rows)

    blocked_rows: list[dict[str, Any]] = []
    damage_path = context.directory / "source_damage_report.csv"
    with damage_path.open("r", encoding="utf-8-sig", newline="") as handle:
        for damage in csv.DictReader(handle):
            sku = _text(damage.get("sku"))
            if sku not in by_sku:
                continue
            record, product = by_sku[sku], context.product_by_sku.get(sku, {})
            damaged = _text(damage.get("damaged_fields"))
            damaged_set = {part.strip() for part in damaged.split(",") if part.strip()}
            clean = {field: _text(product.get(PRODUCT_FIELDS[field])) for field in FIELDS if PRODUCT_FIELDS[field] not in damaged_set}
            blocked_rows.append({
                "sku": sku, "status": _text(damage.get("status")), "damaged_fields": damaged,
                "current_source": _json({field: _text(record.get(field)) for field in FIELDS}),
                "historical_clean_source": _json(clean) if clean else "NOT_FOUND",
                "snapshot_evidence": _text(damage.get("notes")) or "source_damage_report.csv",
                "repairable": "false", "repair_strategy": "等待可信西语快照/官网证据；禁止中文反推",
                "decision": "HOLD_FOR_TRUSTED_SPANISH_EVIDENCE",
            })
    blocked_headers = ["sku", "status", "damaged_fields", "current_source", "historical_clean_source",
                       "snapshot_evidence", "repairable", "repair_strategy", "decision"]
    _write(report_dir / "source_blocked_review.csv", blocked_headers, blocked_rows)
    result = {"run_id": run_id, "date": date, "review_rows": len(closure_rows),
              "review_sku_count": len({row["sku"] for row in closure_rows}),
              "source_blocked_rows": len(blocked_rows), "source_blocked_sku_count": len({row["sku"] for row in blocked_rows}),
              "format_only_candidates": sum(row["can_auto_resolve"] == "true" for row in closure_rows),
              "review_closure_report": str(report_dir / "review_closure_report.csv"),
              "source_blocked_review": str(report_dir / "source_blocked_review.csv")}
    (report_dir / f"dictionary_closure_{date}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def _write(path: Path, headers: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    print(json.dumps(build_reports(load_settings(), run_id=args.run_id), ensure_ascii=False, indent=2))

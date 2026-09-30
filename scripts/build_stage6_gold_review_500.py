"""Build the read-only Chinese Gold review package for the Stage 6 source pool.

This step deliberately does not call a model and never writes SQLite, Master,
Dictionary, or a training split.  It applies only already trusted, field-level
dictionary resolutions; unresolved fields remain explicit model/owner review
gaps instead of being filled with Spanish fallbacks or invented text.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "runtime/stage6/20260913/source_candidate_500/stage6_source_candidate_500.jsonl"
OUT = ROOT / "runtime/stage6/20260913/gold_review_500"
FIELDS = ("name", "cat1", "cat2", "spec", "description", "details")
RULE_SOURCES = frozenset({
    "manual_override", "product_dictionary", "category_dictionary", "term_dictionary", "model_cache",
})
sys.path.insert(0, str(ROOT / "src"))
from action_tracker.config import load_settings  # noqa: E402
from action_tracker.exporting.dictionary_join import load_dictionary_context  # noqa: E402
from action_tracker.dictionary_resolver import resolve_record  # noqa: E402
from action_tracker.services.hashing import localization_source_hash  # noqa: E402
from action_tracker.translation.model_guard import validate_model_output  # noqa: E402


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def immutable_write(path: Path, payload: bytes) -> None:
    if path.exists():
        if path.read_bytes() != payload:
            raise RuntimeError(f"STAGE6_GOLD_ARTIFACT_CHANGED:{path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def source_from_row(row: dict[str, Any]) -> dict[str, str]:
    return {field: str(row["messages"][0]["content_json"].get(field) or "").strip() for field in FIELDS}


def source_hash(source: dict[str, str]) -> str:
    return localization_source_hash({
        "name_es": source["name"], "cat1_es": source["cat1"], "cat2_es": source["cat2"],
        "spec_es": source["spec"], "desc_es": source["description"], "details_es": source["details"],
    })


def review_id(sku: str, digest: str) -> str:
    return hashlib.sha256(f"stage6-gold-review-v1|{sku}|{digest}".encode("utf-8")).hexdigest()


def build_record(meta: dict[str, Any], source: dict[str, str]) -> dict[str, str]:
    return {
        "sku": str(meta.get("sku") or ""),
        "name_es": source["name"], "cat1_es": source["cat1"], "cat2_es": source["cat2"],
        "spec_es": source["spec"], "desc_es": source["description"], "details_es": source["details"],
        "unit_price": "",
    }


def read_source() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(SOURCE.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        raw = json.loads(line)
        if not isinstance(raw, dict):
            raise ValueError(f"SOURCE_ROW_NOT_OBJECT:{line_no}")
        messages = raw.get("messages") or []
        if len(messages) != 1 or messages[0].get("role") != "user":
            raise ValueError(f"SOURCE_MESSAGE_INVALID:{line_no}")
        messages[0]["content_json"] = json.loads(messages[0].get("content") or "{}")
        if set(messages[0]["content_json"]) != set(FIELDS):
            raise ValueError(f"SOURCE_FIELDS_INVALID:{line_no}")
        rows.append(raw)
    if len(rows) != 500:
        raise ValueError(f"SOURCE_COUNT_INVALID:{len(rows)}")
    return rows


def main() -> int:
    rows = read_source()
    settings = load_settings(ROOT / "config/settings.yaml")
    context = load_dictionary_context(settings)
    package: list[dict[str, Any]] = []
    field_rows: list[dict[str, Any]] = []
    rule_closed = 0
    model_gaps = 0
    guard_pass = 0
    guard_reject = 0
    rows_all_closed = 0
    for row in rows:
        meta = row["metadata"]
        source = source_from_row(row)
        digest = source_hash(source)
        if digest != str(meta.get("source_hash") or ""):
            raise ValueError(f"SOURCE_HASH_MISMATCH:{meta.get('sku')}")
        resolution = resolve_record(build_record(meta, source), context)
        proposed: dict[str, str] = {}
        reviews: list[dict[str, Any]] = []
        for field in FIELDS:
            item = resolution.fields.get(field)
            candidate = str(item.value or "").strip() if item else ""
            resolution_source = str(item.source or "") if item else "missing"
            guard_reasons: list[str] = []
            if candidate and item and item.status == "READY" and resolution_source in RULE_SOURCES:
                check = validate_model_output({field: source[field]}, {field: candidate}, expected_fields=[field])
                guard_reasons = list(check.field_reasons.get(field, ()))
                if check.accepted:
                    proposed[field] = candidate
                    field_status = "RULE_CLOSED_GUARD_PASS"
                    guard_pass += 1
                    rule_closed += 1
                else:
                    field_status = "RULE_VALUE_REVIEW_REQUIRED"
                    guard_reject += 1
            else:
                field_status = "MODEL_OR_OWNER_REVIEW_REQUIRED"
                model_gaps += 1
            reviews.append({
                "field": field, "source_es": source[field], "proposed_zh": proposed.get(field, ""),
                "resolution_source": resolution_source, "field_status": field_status,
                "guard_reasons": guard_reasons, "owner_disposition": "PENDING_OWNER_REVIEW",
            })
            field_rows.append({
                "review_id": review_id(str(meta["sku"]), digest), "sku": meta["sku"],
                "source_run_id": meta["source_run_id"], "source_hash": digest, "field": field,
                "source_es": source[field], "proposed_zh": proposed.get(field, ""),
                "resolution_source": resolution_source, "field_status": field_status,
                "guard_reasons": ";".join(guard_reasons), "owner_disposition": "PENDING_OWNER_REVIEW",
            })
        if len(proposed) == len(FIELDS):
            rows_all_closed += 1
        package.append({
            "review_id": review_id(str(meta["sku"]), digest),
            "sku": str(meta["sku"]), "source_run_id": str(meta["source_run_id"]),
            "source_observed_at": meta.get("source_observed_at"), "source_hash": digest,
            "source": source, "proposed_zh": proposed, "field_reviews": reviews,
            "candidate_status": "PENDING_OWNER_REVIEW",
            "training_eligible": False, "production_writes": False, "training_runs": 0,
        })
    package.sort(key=lambda item: (int(item["sku"]) if str(item["sku"]).isdigit() else 10**20, item["sku"]))
    field_rows.sort(key=lambda item: (int(item["sku"]) if str(item["sku"]).isdigit() else 10**20, item["sku"], item["field"]))
    jsonl = b"".join((json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8") for item in package)
    immutable_write(OUT / "stage6_gold_review_500.jsonl", jsonl)
    columns = ("review_id", "sku", "source_run_id", "source_hash", "field", "source_es", "proposed_zh", "resolution_source", "field_status", "guard_reasons", "owner_disposition")
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    writer.writerows(field_rows)
    immutable_write(OUT / "stage6_gold_review_500.csv", ("\ufeff" + stream.getvalue()).encode("utf-8"))
    audit = {
        "contract_id": "STAGE6_GOLD_REVIEW_PACKAGE_V1", "source_pool_count": len(rows),
        "field_review_count": len(field_rows), "rule_closed_fields": rule_closed,
        "model_or_owner_gap_fields": model_gaps, "guard_pass_fields": guard_pass,
        "guard_reject_fields": guard_reject, "rows_all_six_fields_rule_closed": rows_all_closed,
        "candidate_status": "PENDING_OWNER_REVIEW", "training_eligible": False,
        "production_writes": 0, "training_runs": 0, "model_inference": "NOT_RUN",
        "cuda_available": False, "guard_policy": "validate_model_output for rule-resolved values; unresolved fields are not invented",
        "status": "READY_FOR_MODEL_AND_OWNER_REVIEW",
    }
    immutable_write(OUT / "manifest.json", (json.dumps(audit, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8"))
    report = (
        "# Stage 6 Chinese Gold Review Package\n\n"
        f"- Source rows: {len(rows)}; field review rows: {len(field_rows)}\n"
        f"- Rule-closed fields: {rule_closed}; model/owner gaps: {model_gaps}\n"
        f"- Guard pass: {guard_pass}; Guard review required: {guard_reject}\n"
        "- All rows remain `PENDING_OWNER_REVIEW`; no automatic Gold promotion.\n"
        "- Production writes: 0; training runs: 0.\n"
        "- Model inference was not run in this step; no fallback Spanish text was promoted.\n"
        f"- Status: `{audit['status']}`\n"
    )
    immutable_write(OUT / "audit.md", report.encode("utf-8"))
    print(json.dumps(audit, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

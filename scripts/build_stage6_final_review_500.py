"""Merge frozen Stage 6 model batches into the final Owner-review package.

The script is strictly read-only with respect to production data.  It joins
source facts, closed dictionary/rule fields, and model fields that passed the
existing Guard.  Anything that did not pass remains blank and is routed to
Owner review; no Spanish fallback is promoted.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "runtime/stage6/20260913"
SOURCE = BASE / "source_candidate_500/stage6_source_candidate_500.jsonl"
INPUT_MANIFEST = BASE / "gold_model_inputs_500/manifest.json"
BATCH_ROOT = BASE / "gold_model_batches"
HOLDOUT_MANIFEST = BASE / "holdout_freeze_100/manifest.json"
OUT = BASE / "gold_review_500_final"
FIELDS = ("name", "cat1", "cat2", "spec", "description", "details")


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def immutable_write(path: Path, payload: bytes) -> None:
    if path.exists() and path.read_bytes() != payload:
        raise RuntimeError(f"ARTIFACT_CHANGED:{path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(payload)


def source_hash(source: dict[str, str]) -> str:
    # Keep the existing localization_source_hash_v1 contract without changing
    # historical hashes.  Import lazily so this remains a data-factory script.
    import sys
    sys.path.insert(0, str(ROOT / "src"))
    from action_tracker.services.hashing import localization_source_hash
    return localization_source_hash({
        "name_es": source["name"], "cat1_es": source["cat1"], "cat2_es": source["cat2"],
        "spec_es": source["spec"], "desc_es": source["description"], "details_es": source["details"],
    })


def load_source() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in SOURCE.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        message = row.get("messages", [{}])[0]
        source = json.loads(message.get("content") or "{}")
        if set(source) != set(FIELDS):
            raise RuntimeError(f"SOURCE_FIELDS_INVALID:{row.get('metadata', {}).get('sku')}")
        row["_source"] = {field: str(source.get(field) or "").strip() for field in FIELDS}
        meta = row.get("metadata") or {}
        if source_hash(row["_source"]) != str(meta.get("source_hash") or ""):
            raise RuntimeError(f"SOURCE_HASH_MISMATCH:{meta.get('sku')}")
        rows.append(row)
    if len(rows) != 500:
        raise RuntimeError(f"SOURCE_COUNT_INVALID:{len(rows)}")
    return rows


def load_batches() -> tuple[dict[tuple[str, str], dict[str, Any]], dict[str, Any]]:
    manifest = json.loads(INPUT_MANIFEST.read_text(encoding="utf-8"))
    expected_ids = set(manifest["batches"])
    outputs: dict[tuple[str, str], dict[str, Any]] = {}
    batch_audit: dict[str, Any] = {}
    for batch_id, entry in manifest["batches"].items():
        path = BATCH_ROOT / batch_id / "candidates.jsonl"
        batch_manifest = path.with_suffix(".manifest.json")
        if not batch_manifest.exists() or not path.exists():
            raise RuntimeError(f"MODEL_BATCH_MISSING:{batch_id}")
        bm = json.loads(batch_manifest.read_text(encoding="utf-8"))
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if int(bm.get("summary", {}).get("rows", -1)) != int(entry["count"]):
            raise RuntimeError(f"MODEL_BATCH_COUNT_MISMATCH:{batch_id}")
        if len(rows) != int(entry["count"]):
            raise RuntimeError(f"MODEL_BATCH_OUTPUT_COUNT_MISMATCH:{batch_id}")
        for row in rows:
            key = (str(row.get("sku") or ""), str(row.get("source_hash") or ""))
            if key in outputs:
                raise RuntimeError(f"MODEL_OUTPUT_DUPLICATE:{key}")
            outputs[key] = row
        batch_audit[batch_id] = bm.get("summary", {})
    if set(batch_audit) != expected_ids:
        raise RuntimeError("MODEL_BATCH_SET_MISMATCH")
    return outputs, batch_audit


def load_holdout() -> dict[str, Any]:
    holdout = json.loads(HOLDOUT_MANIFEST.read_text(encoding="utf-8"))
    if holdout.get("owner_review") != "PENDING":
        raise RuntimeError("HOLDOUT_MANIFEST_MUTATED")
    return holdout


def review_id(sku: str, digest: str) -> str:
    return hashlib.sha256(f"stage6-gold-review-v2|{sku}|{digest}".encode("utf-8")).hexdigest()


def main() -> int:
    rows = load_source()
    model, batch_audit = load_batches()
    holdout = load_holdout()
    holdout_families = set(holdout["holdout_families"])
    package: list[dict[str, Any]] = []
    field_rows: list[dict[str, Any]] = []
    counts = {"rule_closed": 0, "model_guard_pass": 0, "model_review": 0, "rule_review": 0}
    for row in rows:
        meta = row["metadata"]
        sku = str(meta["sku"])
        digest = str(meta["source_hash"])
        source = row["_source"]
        candidate = model[(sku, digest)]
        rule_values = candidate.get("rule_resolved_fields") or {}
        prediction = candidate.get("prediction") if candidate.get("accepted_by_guard") else None
        model_gap = set(candidate.get("model_gap_fields") or ())
        proposed: dict[str, str] = {}
        reviews: list[dict[str, Any]] = []
        for field in FIELDS:
            if field in rule_values and str(rule_values[field].get("value") or "").strip():
                value = str(rule_values[field]["value"]).strip()
                status = "RULE_CLOSED_GUARD_PASS"
                resolution = str(rule_values[field].get("source") or "rule")
                counts["rule_closed"] += 1
            elif isinstance(prediction, dict) and isinstance(prediction.get(field), str) and prediction[field].strip():
                value = prediction[field].strip()
                status = "MODEL_GUARD_PASS"
                resolution = "frozen_qwen_adapter"
                counts["model_guard_pass"] += 1
            elif field in model_gap:
                value = ""
                status = "MODEL_OR_OWNER_REVIEW_REQUIRED"
                resolution = "model_gap"
                counts["model_review"] += 1
            else:
                value = ""
                status = "RULE_VALUE_REVIEW_REQUIRED"
                resolution = "rule_guard_rejected"
                counts["rule_review"] += 1
            if value:
                proposed[field] = value
            reasons = candidate.get("field_reasons", {}).get(field, []) or []
            reviews.append({
                "field": field, "source_es": source[field], "proposed_zh": value,
                "resolution_source": resolution, "field_status": status,
                "guard_reasons": reasons, "owner_disposition": "PENDING_OWNER_REVIEW",
            })
            field_rows.append({
                "review_id": review_id(sku, digest), "sku": sku,
                "source_run_id": str(meta["source_run_id"]), "source_hash": digest,
                "field": field, "source_es": source[field], "proposed_zh": value,
                "resolution_source": resolution, "field_status": status,
                "guard_reasons": ";".join(reasons), "owner_disposition": "PENDING_OWNER_REVIEW",
            })
        family = str(meta.get("family_key") or "")
        package.append({
            "review_id": review_id(sku, digest), "sku": sku,
            "source_run_id": str(meta["source_run_id"]), "source_observed_at": meta.get("source_observed_at"),
            "source_hash": digest, "family_key": family,
            "partition": "BLIND_HOLDOUT" if family in holdout_families else "TRAINING_POOL",
            "source": source, "proposed_zh": proposed, "field_reviews": reviews,
            "candidate_status": "PENDING_OWNER_REVIEW", "training_eligible": False,
            "production_writes": False, "training_runs": 0,
        })
    package.sort(key=lambda item: (int(item["sku"]) if item["sku"].isdigit() else 10**20, item["sku"], item["source_hash"]))
    field_rows.sort(key=lambda item: (int(item["sku"]) if item["sku"].isdigit() else 10**20, item["sku"], item["field"]))
    if len(package) != 500 or len(field_rows) != 3000:
        raise RuntimeError("FINAL_REVIEW_COUNT_INVALID")
    OUT.mkdir(parents=True, exist_ok=True)
    jsonl = b"".join((canonical(item) + "\n").encode("utf-8") for item in package)
    immutable_write(OUT / "stage6_gold_review_500_final.jsonl", jsonl)
    columns = ("review_id", "sku", "source_run_id", "source_hash", "field", "source_es", "proposed_zh", "resolution_source", "field_status", "guard_reasons", "owner_disposition")
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=columns, lineterminator="\n")
    writer.writeheader(); writer.writerows(field_rows)
    immutable_write(OUT / "stage6_gold_review_500_final.csv", ("\ufeff" + stream.getvalue()).encode("utf-8"))
    holdout_rows = sum(item["partition"] == "BLIND_HOLDOUT" for item in package)
    audit = {
        "contract_id": "STAGE6_FINAL_GOLD_REVIEW_V2", "source_count": 500, "field_review_count": 3000,
        "model_batches": len(batch_audit), "model_batch_audit": batch_audit,
        "rule_closed_fields": counts["rule_closed"], "model_guard_pass_fields": counts["model_guard_pass"],
        "model_review_fields": counts["model_review"], "rule_review_fields": counts["rule_review"],
        "remaining_manual_review_fields": counts["model_review"] + counts["rule_review"],
        "blind_holdout_rows": holdout_rows, "blind_holdout_families": holdout.get("selected_family_count"),
        "training_pool_rows": 500 - holdout_rows, "training_eligible_rows": 0,
        "production_writes": 0, "training_runs": 0, "owner_review": "PENDING",
        "source_hash_recompute": "500/500 PASS", "source_snapshot_exact_match": "500/500 PASS",
        "status": "READY_FOR_OWNER_REVIEW",
    }
    immutable_write(OUT / "manifest.json", (json.dumps(audit, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8"))
    report = "\n".join([
        "# Stage 6 Final Gold Review Package", "", f"- Source candidates: 500", f"- Field review rows: 3000",
        f"- Rule/dictionary Guard pass: {counts['rule_closed']}", f"- Model Guard pass: {counts['model_guard_pass']}",
        f"- Remaining manual review fields: {audit['remaining_manual_review_fields']}",
        f"- Blind holdout: {holdout_rows} rows across {holdout.get('selected_family_count')} families",
        "- Production writes: 0; training runs: 0; Owner review: PENDING.",
        "- No Master, SQLite, Dictionary or existing training split was modified.", "",
        "Status: READY_FOR_OWNER_REVIEW", "",
    ])
    immutable_write(OUT / "audit.md", (report + "\n").encode("utf-8"))
    print(json.dumps(audit, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

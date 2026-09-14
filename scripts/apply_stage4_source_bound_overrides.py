"""Apply owner-approved Stage-4 field fixes to frozen diagnostic predictions.

This is an evidence-only resolver.  It is deliberately source-hash bound and
never writes Master, SQLite, dictionaries, or model adapters.  A replacement
is accepted only when the frozen row's (SKU, field, source_hash) exactly
matches the reviewed override.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--predictions", required=True)
    ap.add_argument("--test-file", required=True)
    ap.add_argument("--overrides", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--audit", required=True)
    args = ap.parse_args()
    predictions = Path(args.predictions).resolve()
    overrides_path = Path(args.overrides).resolve()
    test_file = Path(args.test_file).resolve()
    output = Path(args.output).resolve()
    audit_path = Path(args.audit).resolve()
    rows = [json.loads(line) for line in predictions.read_text(encoding="utf-8").splitlines() if line.strip()]
    overrides = json.loads(overrides_path.read_text(encoding="utf-8"))
    test_rows = [json.loads(line) for line in test_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    source_hash_by_sku = {str(row.get("metadata", {}).get("sku")): str(row.get("metadata", {}).get("source_hash", "")) for row in test_rows}
    if not isinstance(overrides, list):
        raise SystemExit("OVERRIDES_MUST_BE_LIST")
    index = {}
    for item in overrides:
        key = (str(item["sku"]), str(item["field"]))
        if key in index:
            raise SystemExit(f"DUPLICATE_OVERRIDE: {key}")
        index[key] = item
    seen = set()
    changed = []
    output_rows = []
    for row in rows:
        sku = str(row.get("sku"))
        for field, item in list(index.items()):
            if field[0] != sku:
                continue
            if field in seen:
                raise SystemExit(f"DUPLICATE_ROW_FOR_OVERRIDE: {field}")
            expected_hash = source_hash_by_sku.get(sku, "")
            if not expected_hash:
                raise SystemExit(f"SOURCE_HASH_MISSING: {field}")
            if expected_hash != str(item["source_hash"]):
                raise SystemExit(f"SOURCE_HASH_MISMATCH: {field}")
            source = row.get("source") or {}
            reviewed = str(item["reviewed_value"])
            if str(item.get("source_value", source.get(field[1], ""))) != str(source.get(field[1], "")):
                raise SystemExit(f"SOURCE_VALUE_MISMATCH: {field}")
            prediction = row.setdefault("prediction", {})
            before = prediction.get(field[1])
            prediction[field[1]] = reviewed
            row.setdefault("resolver", {})[field[1]] = {
                "resolver_id": "STAGE4_SOURCE_BOUND_OWNER_OVERRIDE_V1",
                "source_hash": expected_hash,
                "reviewed_value_hash": hashlib.sha256(reviewed.encode("utf-8")).hexdigest(),
                "owner_approved": True,
            }
            changed.append({
                "sku": sku,
                "field": field[1],
                "source_hash": expected_hash,
                "before": before,
                "after": reviewed,
                "reason": item.get("reason", "owner-approved frozen Stage-4 hard-fact remediation"),
            })
            seen.add(field)
        output_rows.append(row)
    missing = sorted(set(index) - seen)
    if missing:
        raise SystemExit(f"OVERRIDE_ROW_MISSING: {missing}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in output_rows), encoding="utf-8")
    audit = {
        "schema": "STAGE4_SOURCE_BOUND_OVERRIDE_AUDIT_V1",
        "source_predictions": str(predictions),
        "source_predictions_sha256": sha256(predictions),
        "overrides": str(overrides_path),
        "overrides_sha256": sha256(overrides_path),
        "output": str(output),
        "output_sha256": sha256(output),
        "frozen_rows": len(rows),
        "override_count": len(overrides),
        "changed_cell_count": len(changed),
        "changed_cells": changed,
        "production_writes": False,
        "master_changed": False,
        "sqlite_changed": False,
        "dictionary_changed": False,
        "adapter_changed": False,
    }
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"rows": len(rows), "changed_cell_count": len(changed), "output": str(output), "audit": str(audit_path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

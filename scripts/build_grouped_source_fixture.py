"""Build a grouped, source-only full-record fixture from fieldwise test rows.

This is an offline evaluation helper.  It does not copy assistant/reference
messages into the fixture and never writes Master, SQLite, or dictionary data.
Rows are grouped by the immutable (SKU, source_hash) identity and the six
Spanish source facts are merged in the canonical field order.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

FIELDS = ("name", "cat1", "cat2", "spec", "description", "details")


def _parse_object(value: str) -> dict[str, Any]:
    payload = json.loads(value)
    if not isinstance(payload, dict):
        raise ValueError("SOURCE_PAYLOAD_NOT_OBJECT")
    return {str(k): v for k, v in payload.items()}


def _source(row: dict[str, Any]) -> dict[str, Any]:
    for message in row.get("messages", []):
        if message.get("role") == "user":
            return _parse_object(str(message.get("content", "")))
    raise ValueError("USER_SOURCE_MESSAGE_MISSING")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    source_path = Path(args.input)
    output_path = Path(args.output)

    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    with source_path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"ROW_NOT_OBJECT:{line_no}")
            metadata = dict(row.get("metadata") or {})
            sku = str(metadata.get("sku", "")).strip()
            source_hash = str(metadata.get("source_hash", "")).strip()
            if not sku or not source_hash:
                raise ValueError(f"IDENTITY_MISSING:{line_no}")
            key = (sku, source_hash)
            item = grouped.setdefault(key, {
                "sku": sku,
                "source_hash": source_hash,
                "source_run_id": metadata.get("source_run_id"),
                "source_snapshot_path": metadata.get("source_snapshot_path"),
                "source_observed_at": metadata.get("source_observed_at"),
                "source": {},
            })
            payload = _source(row)
            for field, value in payload.items():
                if field not in FIELDS:
                    raise ValueError(f"UNEXPECTED_FIELD:{field}:{line_no}")
                if field in item["source"] and item["source"][field] != value:
                    raise ValueError(f"SOURCE_FIELD_CONFLICT:{sku}:{field}")
                item["source"][field] = value

    records = []
    incomplete = []
    for item in grouped.values():
        missing = [field for field in FIELDS if field not in item["source"]]
        if missing:
            # A field-conditioned split can legitimately omit a source field
            # when the official record is empty.  It cannot be used for a
            # complete-record gate, so retain an audit entry and exclude it
            # rather than inventing a value.
            incomplete.append({"sku": item["sku"], "source_hash": item["source_hash"], "missing_fields": missing})
            continue
        source = {field: item["source"][field] for field in FIELDS}
        metadata = {
            "sku": item["sku"],
            "source_hash": item["source_hash"],
            "source_run_id": item.get("source_run_id"),
            "source_snapshot_path": item.get("source_snapshot_path"),
            "source_observed_at": item.get("source_observed_at"),
            # Empty field means the offline staging path evaluates all six
            # source facts as one production-like record.
            "field": "",
            "fixture_type": "GROUPED_FULL_SOURCE_FROM_FIELD_TEST",
            "production_writes": False,
        }
        records.append({
            "messages": [
                {"role": "system", "content": "source-only evaluation fixture"},
                {"role": "user", "content": json.dumps(source, ensure_ascii=False)},
            ],
            "metadata": metadata,
        })
    records.sort(key=lambda row: (row["metadata"]["sku"], row["metadata"]["source_hash"]))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        for row in records:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    manifest = {
        "fixture_type": "GROUPED_FULL_SOURCE_FROM_FIELD_TEST",
        "input": str(source_path),
        "output": str(output_path),
        "input_sha256": _sha256(source_path),
        "output_sha256": _sha256(output_path),
        "row_count": len(records),
        "unique_sku_count": len({row["metadata"]["sku"] for row in records}),
        "incomplete_group_count": len(incomplete),
        "incomplete_groups": incomplete,
        "fields": list(FIELDS),
        "reference_targets_copied": False,
        "production_writes": False,
    }
    output_path.with_suffix(".manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

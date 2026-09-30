"""Build a read-only Owner review queue for current dictionary rows.

The queue is deliberately non-mutating.  It exposes current Spanish facts,
the current Chinese dictionary values, source hash, and a blank decision field
for field-level Owner review.  It never approves or applies a value.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


FIELDS = (
    ("name", "name_es_raw", "name_zh_standard"),
    ("spec", "spec_es_raw", "spec_zh_standard"),
    ("cat1", "cat1_es", "cat1_zh"),
    ("cat2", "cat2_es", "cat2_zh"),
)


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build(dictionary: Path, output_dir: Path, audit_path: Path | None = None) -> dict[str, object]:
    product_path = dictionary / "product_dictionary.csv"
    output_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, str]] = []
    with product_path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if _text(row.get("review_status")) != "NEEDS_REVIEW":
                continue
            for field_name, source_col, target_col in FIELDS:
                rows.append(
                    {
                        "sku": _text(row.get("sku")),
                        "field": field_name,
                        "source_hash": _text(row.get("source_hash")),
                        "source_hash_contract_version": "localization_source_hash_v1",
                        "name_es_raw": _text(row.get("name_es_raw")),
                        "spec_es_raw": _text(row.get("spec_es_raw")),
                        "cat1_es": _text(row.get("cat1_es")),
                        "cat2_es": _text(row.get("cat2_es")),
                        "current_value_zh": _text(row.get(target_col)),
                        "field_source_value_es": _text(row.get(source_col)),
                        "translation_status": _text(row.get("translation_status")),
                        "review_status": _text(row.get("review_status")),
                        "owner_decision": "",
                        "owner_value_zh": "",
                        "owner_note": "",
                    }
                )
    rows.sort(key=lambda item: (item["sku"], item["field"]))
    columns = list(rows[0]) if rows else [
        "sku", "field", "source_hash", "source_hash_contract_version",
        "name_es_raw", "spec_es_raw", "cat1_es", "cat2_es",
        "current_value_zh", "field_source_value_es", "translation_status",
        "review_status", "owner_decision", "owner_value_zh", "owner_note",
    ]
    csv_path = output_dir / "current_dictionary_owner_review_queue.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    jsonl_path = output_dir / "current_dictionary_owner_review_queue.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    manifest = {
        "queue_type": "CURRENT_DICTIONARY_OWNER_REVIEW_V1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dictionary_dir": str(dictionary),
        "source_file": str(product_path),
        "source_sha256": _sha256(product_path),
        "pending_sku_count": len({row["sku"] for row in rows}),
        "pending_field_count": len(rows),
        "decisions_written": 0,
        "production_writes": 0,
        "audit_reference": str(audit_path) if audit_path else None,
        "csv": str(csv_path),
        "jsonl": str(jsonl_path),
        "status": "OWNER_REVIEW_REQUIRED",
    }
    (output_dir / "current_dictionary_owner_review_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    md = [
        "# Current Dictionary Owner Review Queue",
        "",
        "本包只读生成，不批准、不写入 Master、不执行 Production Apply。",
        "",
        f"- 待审核 SKU：{manifest['pending_sku_count']}",
        f"- 待审核字段：{manifest['pending_field_count']}",
        "- Owner 决定：留空（APPROVE / REVISE / REJECT）",
        "- 生产写入：0",
        "",
        "审核原则：逐字段判断；如果中文值需要修改，请填写 owner_value_zh；不要整行覆盖。",
    ]
    (output_dir / "README.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dictionary", default="runtime/dictionary")
    parser.add_argument("--output-dir", default="runtime/dictionary/owner_review_20260915")
    parser.add_argument("--audit", default="runtime/dictionary/audit_report_20260915.json")
    args = parser.parse_args()
    result = build(Path(args.dictionary), Path(args.output_dir), Path(args.audit))
    print(json.dumps(result, ensure_ascii=False, indent=2))

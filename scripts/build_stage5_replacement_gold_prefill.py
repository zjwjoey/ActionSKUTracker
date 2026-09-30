"""Build a read-only Chinese Gold prefill for replacement source candidates."""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from pathlib import Path


CAT2 = {
    "Aparatos de cocina": "厨房电器",
    "Relojes y joyas": "手表和珠宝",
    "Maquillaje": "彩妆",
}


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


def build(source_path: Path, dictionary: Path, database: Path, output: Path) -> dict[str, object]:
    source_rows = [json.loads(line) for line in source_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    products = {row["sku"]: row for row in csv.DictReader((dictionary / "product_dictionary.csv").open(encoding="utf-8-sig", newline=""))}
    categories = {(row["cat1_es"], row["cat2_es"]): row["cat2_zh"] for row in csv.DictReader((dictionary / "category_dictionary.csv").open(encoding="utf-8-sig", newline="")) if _text(row.get("cat2_zh"))}
    conn = sqlite3.connect(database); conn.row_factory = sqlite3.Row
    rows = []
    for item in source_rows:
        sku = item["metadata"]["sku"]; source = json.loads(item["messages"][0]["content"]); product = products.get(sku, {})
        zh = {
            "name": _text(product.get("name_zh_standard")),
            "spec": _text(product.get("spec_zh_standard")),
            "cat1": _text(product.get("cat1_zh")),
            "cat2": _text(product.get("cat2_zh")) or categories.get((source.get("cat1", ""), source.get("cat2", ""))) or CAT2.get(source.get("cat2", ""), ""),
        }
        localized = conn.execute("SELECT description, details FROM product_localizations WHERE official_sku=? AND language='zh'", (sku,)).fetchone()
        zh["description"] = _text(localized["description"] if localized else "")
        zh["details"] = _text(localized["details"] if localized else "")
        missing = [field for field, value in zh.items() if not value]
        rows.append({
            "sku": sku, "source_hash": item["metadata"].get("source_hash", ""),
            "name_es": source.get("name", ""), "cat1_es": source.get("cat1", ""), "cat2_es": source.get("cat2", ""),
            "spec_es": source.get("spec", ""), "description_es": source.get("description", ""), "details_es": source.get("details", ""),
            "name_zh": zh["name"], "cat1_zh": zh["cat1"], "cat2_zh": zh["cat2"], "spec_zh": zh["spec"],
            "description_zh": zh["description"], "details_zh": zh["details"],
            "missing_zh_fields": "|".join(missing), "candidate_status": "GOLD_PREFILL_REVIEW_REQUIRED",
            "owner_decision": "", "owner_note": "",
        })
    conn.close(); rows.sort(key=lambda row: row["sku"])
    output.parent.mkdir(parents=True, exist_ok=True)
    columns = list(rows[0]) if rows else ["sku"]
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns); writer.writeheader(); writer.writerows(rows)
    manifest = {
        "artifact": "STAGE5_REPLACEMENT_GOLD_PREFILL_V1", "source_count": len(rows),
        "missing_field_count": sum(bool(row["missing_zh_fields"]) for row in rows),
        "description_or_details_missing": sum("description" in row["missing_zh_fields"] or "details" in row["missing_zh_fields"] for row in rows),
        "owner_decisions": 0, "training_runs": 0, "production_writes": 0,
        "status": "OWNER_REVIEW_REQUIRED", "csv": str(output),
    }
    output.with_name("stage5_replacement_gold_prefill_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--source", default="runtime/training/qwen3_8b/20260915/stage5_replacement_source_versions_119/stage5_new_high_confidence_36_source_only.jsonl"); parser.add_argument("--dictionary", default="runtime/dictionary"); parser.add_argument("--database", default="runtime/db/action_tracker.db"); parser.add_argument("--output", default="runtime/training/qwen3_8b/20260915/stage5_replacement_source_versions_119/stage5_replacement_gold_prefill.csv")
    args = parser.parse_args(); print(json.dumps(build(Path(args.source), Path(args.dictionary), Path(args.database), Path(args.output)), ensure_ascii=False, indent=2))

"""Create field-level Qwen training data from all reliable SKU facts."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sqlite3
from collections import Counter
from pathlib import Path

CJK = re.compile(r"[\u3400-\u9fff]")
HTML_TAG = re.compile(r"<[^>]+>")
NULL_PREFIX = re.compile(r"^\s*null\.", re.IGNORECASE)
UI_COPY = re.compile(r"añadir a tus favoritos|加入收藏", re.IGNORECASE)
FIELDS = ("name", "cat1", "cat2", "spec", "description", "details")
SOURCE = {"name": "name_es_raw", "cat1": "cat1_es", "cat2": "cat2_es", "spec": "spec_es_raw"}
TARGET = {"name": "name_zh_standard", "cat1": "cat1_zh", "cat2": "cat2_zh", "spec": "spec_zh_standard"}


def unsafe_source_reason(value: str) -> str | None:
    """Return a reason for source text that must not enter model data."""
    text = str(value or "")
    if HTML_TAG.search(text):
        return "HTML"
    if NULL_PREFIX.search(text):
        return "NULL_PREFIX"
    if UI_COPY.search(text):
        return "UI_COPY"
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default="2026-09-08")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    out_dir = root / "runtime" / "training" / "qwen3_8b" / args.date.replace("-", "")
    out_dir.mkdir(parents=True, exist_ok=True)
    resolutions: dict[str, dict[str, str]] = {}
    resolution_path = out_dir / "qwen_human_resolutions_8.jsonl"
    if resolution_path.exists():
        for line in resolution_path.open(encoding="utf-8"):
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("status") == "HUMAN_CONFIRMED" and isinstance(row.get("target"), dict):
                resolutions[str(row["sku"])] = {str(k): str(v).strip() for k, v in row["target"].items()}
    blocked = {r["sku"] for r in csv.DictReader((root / "data/dictionary/source_damage_report.csv").open(encoding="utf-8-sig")) if r.get("status") in {"SOURCE_DAMAGED", "SOURCE_POLLUTED"}}
    products = list(csv.DictReader((root / "data/dictionary/product_dictionary.csv").open(encoding="utf-8-sig")))
    overrides: dict[str, dict[str, str]] = {}
    for row in csv.DictReader((root / "data/dictionary/manual_overrides.csv").open(encoding="utf-8-sig")):
        if row.get("scope") == "product" and row.get("field") in set(TARGET.values()):
            overrides.setdefault(str(row.get("key") or ""), {})[str(row["field"])] = str(row.get("value") or "").strip()
    con = sqlite3.connect(root / "runtime/db/action_tracker.db"); con.row_factory = sqlite3.Row
    loc: dict[str, dict[str, dict]] = {}
    for row in con.execute("SELECT * FROM product_localizations WHERE language IN ('es','zh')"):
        loc.setdefault(row["official_sku"], {})[row["language"]] = dict(row)
    counts, excluded = Counter(), Counter(); rows: list[dict] = []
    for product in products:
        sku = product["sku"]
        if sku in blocked:
            excluded["source_blocked"] += 1; continue
        pair = loc.get(sku, {}); zh = pair.get("zh", {})
        for field in FIELDS:
            source = str(product.get(SOURCE.get(field, ""), "") or "").strip() if field in SOURCE else str(pair.get("es", {}).get(field, "") or "").strip()
            target_field = TARGET.get(field, "")
            target = (overrides.get(sku, {}).get(target_field) or str(product.get(target_field, "") or "")).strip() if field in TARGET else str(zh.get(field, "") or "").strip()
            target = resolutions.get(sku, {}).get(field, target)
            if not source:
                excluded[f"missing_source_{field}"] += 1; continue
            unsafe = unsafe_source_reason(source)
            if unsafe:
                excluded[f"unsafe_source_{unsafe}_{field}"] += 1; continue
            # Most training targets must contain Chinese.  A human-confirmed
            # target is the explicit exception: specifications such as
            # ``250ml`` and ``1kg`` are valid standardized Chinese outputs
            # even though they contain no CJK character.  Do not silently
            # discard those reviewed facts from the gold/silver field set.
            if not target or target == "中文品名待人工核验" or (not CJK.search(target) and sku not in resolutions):
                excluded[f"missing_target_{field}"] += 1; continue
            tier = "HUMAN_REVIEWED" if sku in resolutions or target_field in overrides.get(sku, {}) or product.get("review_status") == "HUMAN_REVIEWED" or product.get("locked") == "1" else "DICTIONARY_OR_MODEL"
            rows.append({
                "field": field, "source": source, "target": target,
                "sku": sku, "source_hash": product.get("source_hash", ""), "label_tier": tier,
            })
            counts[field] += 1
    rows.sort(key=lambda r: (r["field"], int(r["sku"]) if r["sku"].isdigit() else 10**18, r["sku"]))
    path = out_dir / "qwen_field_examples_all.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps({
                "messages": [
                    {"role": "system", "content": f"将 Action 西语{row['field']}忠实标准化为中文，不臆造信息，保持数字、单位和品牌/型号。"},
                    {"role": "user", "content": json.dumps({row["field"]: row["source"]}, ensure_ascii=False)},
                    {"role": "assistant", "content": json.dumps({row["field"]: row["target"]}, ensure_ascii=False)},
                ],
                "metadata": {k: row[k] for k in ("field", "sku", "source_hash", "label_tier")},
            }, ensure_ascii=False) + "\n")
    report = {
        "date": args.date, "source_rows": len(products), "reliable_source_rows": len(products) - excluded["source_blocked"],
        "field_example_rows": len(rows), "field_counts": dict(counts), "excluded": dict(excluded),
        "source": "data/dictionary/product_dictionary.csv + manual_overrides.csv + human resolutions + SQLite product_localizations",
        "output": str(path), "duplicate_key_count": len(rows) - len({(r["field"], r["sku"]) for r in rows}),
    }
    (out_dir / "field_dataset_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False)); return 0


if __name__ == "__main__":
    raise SystemExit(main())

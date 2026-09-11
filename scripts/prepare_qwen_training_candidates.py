"""Build a deterministic first-pass Qwen training candidate set.

This is a read-only projection of the verified SQLite localizations.  It never
changes Master or the dictionary.  Records are excluded when the Spanish source
is damaged/polluted, a required field is blank, or the Chinese target is still
an explicit placeholder.  The candidate set intentionally keeps source_hash and
provenance in metadata while excluding SKU/price/URL from the model input.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
import re
from collections import Counter
from pathlib import Path


FIELDS = ("name", "cat1", "cat2", "spec", "description", "details")
PLACEHOLDERS = {"中文品名待人工核验", "官网无独立描述", "官网未提供详细商品描述"}
NUM = re.compile(r"\d+(?:[.,]\d+)?")


def _numeric_tokens(value: object) -> list[str]:
    return sorted(x.replace(",", ".") for x in NUM.findall(str(value or "")))


def _hash_source(es: dict[str, object]) -> str:
    payload = "\x1f".join(str(es.get(k) or "").strip() for k in FIELDS[:4])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=5000)
    parser.add_argument("--date", default="2026-09-08")
    parser.add_argument("--all-history", action="store_true", help="use all reliable historical/current SKU pairs")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    db = root / "runtime" / "db" / "action_tracker.db"
    out_dir = root / "runtime" / "training" / "qwen3_8b" / args.date.replace("-", "")
    out_dir.mkdir(parents=True, exist_ok=True)
    blocked = {
        row["sku"] for row in csv.DictReader(
            (root / "data" / "dictionary" / "source_damage_report.csv").open(encoding="utf-8-sig")
        ) if row.get("status") in {"SOURCE_DAMAGED", "SOURCE_POLLUTED"}
    }
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    current = ({
        row["official_sku"] for row in con.execute("SELECT official_sku FROM products WHERE status='CURRENT'")
    } if not args.all_history else {
        row["official_sku"] for row in con.execute("SELECT official_sku FROM products")
    })
    localized: dict[str, dict[str, dict[str, object]]] = {}
    for row in con.execute("SELECT * FROM product_localizations WHERE language IN ('es','zh')"):
        localized.setdefault(row["official_sku"], {})[row["language"]] = dict(row)
    excluded = Counter()
    candidates: list[dict[str, object]] = []
    for sku in sorted(current, key=lambda value: (int(value) if str(value).isdigit() else 10**18, str(value))):
        if sku in blocked:
            excluded["source_blocked"] += 1
            continue
        pair = localized.get(sku, {})
        es, zh = pair.get("es"), pair.get("zh")
        if not es or not zh:
            excluded["missing_language_pair"] += 1
            continue
        if any(not str(es.get(field) or "").strip() for field in FIELDS):
            excluded["missing_spanish_field"] += 1
            continue
        if any(not str(zh.get(field) or "").strip() for field in FIELDS):
            excluded["missing_chinese_field"] += 1
            continue
        if any(str(zh.get(field) or "").strip() in PLACEHOLDERS for field in FIELDS):
            excluded["placeholder_target"] += 1
            continue
        candidates.append({"sku": sku, "es": es, "zh": zh})

    # Round-robin by Chinese一级类目 keeps the first 5,000 representative.
    groups: dict[str, list[dict[str, object]]] = {}
    for item in candidates:
        groups.setdefault(str(item["zh"].get("cat1") or "未分类"), []).append(item)
    for values in groups.values():
        values.sort(key=lambda item: str(item["sku"]))
    selected: list[dict[str, object]] = []
    while len(selected) < min(args.limit, len(candidates)):
        progressed = False
        for category in sorted(groups):
            if groups[category]:
                selected.append(groups[category].pop(0))
                progressed = True
                if len(selected) >= args.limit:
                    break
        if not progressed:
            break

    system = "将 Action 西语商品字段标准化为简洁、忠实的中文。保持数字、单位、数量、尺寸和品牌/型号事实；不得臆造源数据没有的信息。输出固定六字段 JSON。"
    jsonl = out_dir / f"qwen_candidates_{len(selected)}.jsonl"
    with jsonl.open("w", encoding="utf-8") as handle:
        for item in selected:
            es, zh, sku = item["es"], item["zh"], item["sku"]
            source = {f: str(es.get(f) or "").strip() for f in FIELDS}
            target = {f: str(zh.get(f) or "").strip() for f in FIELDS}
            numeric_review = any(
                _numeric_tokens(source[f]) != _numeric_tokens(target[f]) for f in FIELDS
            )
            record = {
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": json.dumps(source, ensure_ascii=False, sort_keys=True)},
                    {"role": "assistant", "content": json.dumps(target, ensure_ascii=False, sort_keys=True)},
                ],
                "metadata": {
                    "sku": sku,
                    "source_hash": str(es.get("source_hash") or _hash_source(es)),
                    "source_review_status": str(es.get("review_status") or ""),
                    "target_review_status": str(zh.get("review_status") or ""),
                    "target_source": str(zh.get("source") or ""),
                    "numeric_consistency": "REVIEW" if numeric_review else "PASS",
                },
            }
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    counts = Counter(str(item["zh"].get("cat1") or "未分类") for item in selected)
    summary = {
        "date": args.date, "scope": "all_history" if args.all_history else "current", "candidate_pool": len(candidates), "selected": len(selected),
        "limit": args.limit, "excluded": dict(excluded), "category_counts": dict(sorted(counts.items())),
        "fields": list(FIELDS), "source": "SQLite product_localizations + current products",
        "output": str(jsonl),
    }
    (out_dir / "selection_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

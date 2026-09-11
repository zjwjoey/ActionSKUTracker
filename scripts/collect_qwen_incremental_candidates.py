"""Collect a leakage-safe, review-required incremental Qwen candidate set.

The collector intentionally creates *candidate gold*, not training data.  It
uses current PRIMARY facts and Chinese labels only as review material, excludes
every SKU used by the baseline gold run and the existing field validation/test
sets, and never writes Master, SQLite, or any dictionary.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from action_tracker.config import load_settings
from action_tracker.exporting.dictionary_join import load_dictionary_context
from action_tracker.services.hashing import localization_source_hash
from action_tracker.translation.model_guard import validate_model_output


FIELDS = ("name", "cat1", "cat2", "spec", "description", "details")
VALID_CAT1 = {
    "DIY五金", "办公文具", "宠物用品", "厨房餐具", "服饰鞋包", "个人美容",
    "家居布置", "家务清洁", "旅行用品", "食品饮料", "数码影音", "玩具",
    "兴趣手作", "园艺户外", "运动用品",
}
_CJK = re.compile(r"[\u3400-\u9fff]")
_HTML = re.compile(r"<[^>]+>")
_NULL_PREFIX = re.compile(r"^\s*null\.", re.IGNORECASE)
_UI_COPY = re.compile(r"añadir a tus favoritos|加入收藏", re.IGNORECASE)


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rows(path: Path) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def _sku_set(paths: Iterable[Path]) -> set[str]:
    return {str(row["metadata"]["sku"]) for path in paths for row in _rows(path)}


def _field_train_metadata(path: Path) -> dict[str, set[str]]:
    result: dict[str, set[str]] = defaultdict(set)
    for row in _rows(path):
        metadata = row.get("metadata") or {}
        result[str(metadata.get("sku") or "")].add(str(metadata.get("label_tier") or ""))
    return result


def _unsafe_source(value: str) -> bool:
    return bool(_HTML.search(value) or _NULL_PREFIX.search(value) or _UI_COPY.search(value) or _CJK.search(value))


def _source_hash(source: dict[str, str]) -> str:
    return localization_source_hash({
        "name_es": source["name"], "cat1_es": source["cat1"], "cat2_es": source["cat2"],
        "spec_es": source["spec"], "desc_es": source["description"], "details_es": source["details"],
    })


def _brand_phrases(context: Any, sku: str) -> tuple[str, ...]:
    product = context.product_by_sku.get(sku, {})
    brand_id = str(product.get("brand_id") or "").strip()
    brand = context.brand_by_id.get(brand_id, {}) if brand_id else {}
    values = [str(brand.get("canonical_name") or brand_id).strip()]
    values.extend(item.strip() for item in str(brand.get("aliases_es") or "").split("|") if item.strip())
    return tuple(dict.fromkeys(item for item in values if item))


def _balanced_select(candidates: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Round-robin categories while preferring fully human-reviewed labels."""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in candidates:
        groups[row["target"]["cat1"]].append(row)
    for values in groups.values():
        values.sort(key=lambda row: (0 if row["label_tier"] == "HUMAN_REVIEWED" else 1,
                                     int(row["sku"]) if row["sku"].isdigit() else 10**18, row["sku"]))
    selected: list[dict[str, Any]] = []
    while len(selected) < limit:
        progressed = False
        for category in sorted(groups):
            if groups[category]:
                selected.append(groups[category].pop(0))
                progressed = True
                if len(selected) == limit:
                    break
        if not progressed:
            break
    return selected


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect review-required incremental Qwen candidates")
    parser.add_argument("--date", required=True, help="Artifact date, YYYY-MM-DD")
    parser.add_argument("--limit", type=int, default=500)
    args = parser.parse_args()
    if args.limit <= 0:
        raise SystemExit("LIMIT_MUST_BE_POSITIVE")

    baseline = ROOT / "runtime" / "training" / "qwen3_8b" / "20260908"
    out_dir = ROOT / "runtime" / "training" / "qwen3_8b" / args.date.replace("-", "")
    out_dir.mkdir(parents=True, exist_ok=True)
    gold_paths = [baseline / f"qwen_gold_clean_{part}.jsonl" for part in ("train", "validation", "test")]
    field_paths = {part: baseline / f"qwen_field_{part}.jsonl" for part in ("train", "validation", "test")}
    if any(not path.exists() for path in [*gold_paths, *field_paths.values()]):
        raise SystemExit("BASELINE_SPLIT_ARTIFACT_MISSING")
    baseline_gold = _sku_set(gold_paths)
    field_train = _sku_set([field_paths["train"]])
    field_holdout = _sku_set([field_paths["validation"], field_paths["test"]])
    field_tiers = _field_train_metadata(field_paths["train"])

    blocked_path = ROOT / "data" / "dictionary" / "source_damage_report.csv"
    blocked = {
        str(row.get("sku") or "") for row in csv.DictReader(blocked_path.open(encoding="utf-8-sig"))
        if str(row.get("status") or "") in {"SOURCE_DAMAGED", "SOURCE_POLLUTED"}
    }
    cfg = load_settings(ROOT / "config" / "settings.yaml")
    context = load_dictionary_context(cfg)
    db_path = ROOT / "runtime" / "db" / "action_tracker.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    source_rows = conn.execute(
        """SELECT p.official_sku, es.name AS es_name, es.cat1 AS es_cat1, es.cat2 AS es_cat2,
                  es.spec AS es_spec, es.description AS es_description, es.details AS es_details,
                  zh.name AS zh_name, zh.cat1 AS zh_cat1, zh.cat2 AS zh_cat2, zh.spec AS zh_spec,
                  zh.description AS zh_description, zh.details AS zh_details
           FROM products p
           JOIN product_localizations es ON es.official_sku=p.official_sku AND es.language='es'
           JOIN product_localizations zh ON zh.official_sku=p.official_sku AND zh.language='zh'
           WHERE p.status='CURRENT'
           ORDER BY CAST(p.official_sku AS INTEGER), p.official_sku"""
    ).fetchall()
    conn.close()

    rejected = Counter()
    candidates: list[dict[str, Any]] = []
    for raw in source_rows:
        sku = str(raw["official_sku"])
        if sku in blocked:
            rejected["source_blocked"] += 1
            continue
        if sku in baseline_gold:
            rejected["baseline_gold_excluded"] += 1
            continue
        if sku not in field_train:
            rejected["not_in_untrained_field_train_pool"] += 1
            continue
        if sku in field_holdout:
            raise RuntimeError(f"FIELD_SPLIT_LEAK:{sku}")
        source = {field: str(raw[f"es_{field}"] or "").strip() for field in FIELDS}
        target = {field: str(raw[f"zh_{field}"] or "").strip() for field in FIELDS}
        if any(not source[field] for field in FIELDS):
            rejected["source_incomplete"] += 1
            continue
        if any(not target[field] for field in FIELDS):
            rejected["target_incomplete"] += 1
            continue
        if any(_unsafe_source(source[field]) for field in FIELDS):
            rejected["source_polluted"] += 1
            continue
        if target["cat1"] not in VALID_CAT1:
            rejected["target_cat1_invalid"] += 1
            continue
        guard = validate_model_output(source, target, expected_fields=FIELDS,
                                      allowed_brand_phrases=_brand_phrases(context, sku))
        if not guard.accepted:
            for reason in guard.reasons:
                rejected[f"target_guard_{reason.lower()}"] += 1
            continue
        tiers = field_tiers.get(sku, set())
        label_tier = "HUMAN_REVIEWED" if tiers == {"HUMAN_REVIEWED"} else "DICTIONARY_OR_MODEL"
        candidates.append({"sku": sku, "source": source, "target": target,
                           "source_hash": _source_hash(source), "label_tier": label_tier})

    selected = _balanced_select(candidates, args.limit)
    if len(selected) != args.limit:
        raise SystemExit(f"INSUFFICIENT_ELIGIBLE_CANDIDATES:{len(selected)}")
    selected_skus = {row["sku"] for row in selected}
    if selected_skus & baseline_gold or selected_skus & field_holdout:
        raise RuntimeError("POST_SELECTION_SPLIT_LEAK")

    system = ("将 Action 西语商品六字段忠实标准化为简体中文；保持数字、单位、数量、尺寸和品牌/型号，"
              "不臆造。该记录为候选金标，须人工复核后才能用于训练。")
    data_path = out_dir / f"qwen_incremental_candidate_{args.limit}.jsonl"
    review_path = out_dir / f"qwen_incremental_candidate_{args.limit}_review.csv"
    with data_path.open("w", encoding="utf-8", newline="") as handle:
        for item in selected:
            record = {
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": json.dumps(item["source"], ensure_ascii=False, sort_keys=True)},
                    {"role": "assistant", "content": json.dumps(item["target"], ensure_ascii=False, sort_keys=True)},
                ],
                "metadata": {
                    "sku": item["sku"], "source_hash": item["source_hash"],
                    "label_tier": item["label_tier"], "candidate_status": "REVIEW_REQUIRED",
                    "collection": "incremental_untrained_current_pool",
                },
            }
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    review_headers = ["sku", "label_tier", "source_hash", "review_status", *[f"es_{field}" for field in FIELDS],
                      *[f"zh_{field}" for field in FIELDS]]
    with review_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=review_headers)
        writer.writeheader()
        for item in selected:
            writer.writerow({
                "sku": item["sku"], "label_tier": item["label_tier"], "source_hash": item["source_hash"],
                "review_status": "PENDING", **{f"es_{field}": item["source"][field] for field in FIELDS},
                **{f"zh_{field}": item["target"][field] for field in FIELDS},
            })
    manifest = {
        "date": args.date, "status": "CANDIDATES_COLLECTED_NOT_TRAINING_READY", "requested_skus": args.limit,
        "selected_skus": len(selected), "candidate_pool": len(candidates), "rejected": dict(sorted(rejected.items())),
        "category_counts": dict(sorted(Counter(item["target"]["cat1"] for item in selected).items())),
        "label_tier_counts": dict(sorted(Counter(item["label_tier"] for item in selected).items())),
        "baseline_gold_overlap": sorted(selected_skus & baseline_gold),
        "baseline_field_holdout_overlap": sorted(selected_skus & field_holdout),
        "training_eligible": False,
        "required_next_step": "Review every SKU; only approved, source-hash-stable rows may enter a newly split training set.",
        "artifacts": {
            "candidate_jsonl": str(data_path), "candidate_jsonl_sha256": _hash(data_path),
            "review_csv": str(review_path), "review_csv_sha256": _hash(review_path),
        },
    }
    manifest_path = out_dir / f"qwen_incremental_candidate_{args.limit}.manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

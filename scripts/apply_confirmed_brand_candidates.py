"""将审核确认的品牌/IP候选并入本地品牌字典，保留待人工复核标记。"""
from __future__ import annotations

import csv
from pathlib import Path

from action_tracker.dictionary import BRAND_DICTIONARY_HEADERS, load_dictionary_rows, write_dictionary_csv


def _key(value: str) -> str:
    return " ".join(value.casefold().split())


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    dictionary = root / "runtime" / "dictionary"
    decisions_path = dictionary / "brand_review_queue.csv"
    brand_path = dictionary / "brand_dictionary.csv"
    existing = load_dictionary_rows(brand_path, headers=BRAND_DICTIONARY_HEADERS, key_fields=("brand_id",))
    by_name = {_key(row["canonical_name"]): row for row in existing if row["canonical_name"]}
    additions = 0
    with decisions_path.open(encoding="utf-8-sig", newline="") as handle:
        for decision in csv.DictReader(handle):
            if decision.get("decision") != "CONFIRMED_BRAND_OR_IP":
                continue
            for entity in (part.strip() for part in decision.get("canonical_entities", "").split("|")):
                if not entity or _key(entity) in by_name:
                    continue
                row = {
                    "brand_id": entity, "canonical_name": entity, "aliases_es": entity,
                    "keep_original": "1", "is_action_brand": "0", "confidence": "AI_REVIEWED",
                    "review_status": "NEEDS_HUMAN_REVIEW",
                    "notes": "来源：2026-08-25 品牌/IP审核；已确认商品名证据，待人工抽检。",
                }
                existing.append(row)
                by_name[_key(entity)] = row
                additions += 1
    write_dictionary_csv(brand_path, existing, BRAND_DICTIONARY_HEADERS, key_fields=("brand_id",))
    print({"brand_rows": len(existing), "added": additions, "decision_source": str(decisions_path)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

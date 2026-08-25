"""生成第二阶段人工审查包；只读取运行时字典，不自动修改商品结果。"""
from __future__ import annotations

import csv
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DICTIONARY = ROOT / "runtime" / "dictionary"


def read(name: str) -> list[dict[str, str]]:
    with (DICTIONARY / name).open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write(name: str, rows: list[dict[str, str]], headers: list[str]) -> None:
    with (DICTIONARY / name).open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    products = {row["sku"]: row for row in read("product_dictionary.csv")}
    damage = read("source_damage_report.csv")
    queue = read("brand_review_queue.csv")
    terms = read("term_dictionary.csv")
    latest = max((row.get("source_last_seen", "") for row in products.values()), default="")

    damage_rows = []
    for row in damage:
        product = products.get(row["sku"], {})
        damage_rows.append({
            "sku": row["sku"],
            "in_current": "1" if product.get("source_last_seen") == latest else "0",
            "source_last_seen": product.get("source_last_seen", ""),
            "name_es_raw": product.get("name_es_raw", ""),
            "name_zh_standard": product.get("name_zh_standard", ""),
            "spec_es_raw": product.get("spec_es_raw", ""),
            "spec_zh_standard": product.get("spec_zh_standard", ""),
            "cat1_es": product.get("cat1_es", ""),
            "cat1_zh": product.get("cat1_zh", ""),
            "damaged_fields": row.get("damaged_fields", ""),
            "status": row.get("status", "SOURCE_DAMAGED"),
            "recommended_action": (
                "核对字段是否为网页 UI 文案；确认无商品规格后保持空值"
                if row.get("status") == "SOURCE_POLLUTED" else
                "补充可信西语原始证据；没有证据则保持隔离，不反向翻译"
            ),
        })
    write("manual_review_source_damage.csv", damage_rows, list(damage_rows[0]) if damage_rows else ["sku"])

    brand_rows = []
    for row in queue:
        brand_rows.append({
            "sku": row.get("sku", ""),
            "source_last_seen": row.get("source_last_seen", ""),
            "name_es": row.get("name_es", ""),
            "current_name_zh": row.get("current_name_zh", ""),
            "spec_es": row.get("spec_es", ""),
            "current_spec_zh": row.get("current_spec_zh", ""),
            "latin_candidates": row.get("latin_candidates", ""),
            "decision": row.get("decision", ""),
            "canonical_entities": row.get("canonical_entities", ""),
            "confidence": row.get("confidence", ""),
            "review_status": row.get("review_status", ""),
            "human_decision": "",
            "human_note": "",
        })
    write("manual_review_brand_series.csv", brand_rows, list(brand_rows[0]) if brand_rows else ["sku"])

    term_rows = []
    for row in terms:
        term_rows.append({**row, "human_decision": "", "human_note": ""})
    write("manual_review_terms.csv", term_rows, list(term_rows[0]) if term_rows else ["term_es"])

    quality = {}
    model_review_rows = []
    for row in read("model_translation_overrides.csv"):
        quality[row.get("quality_status", "")] = quality.get(row.get("quality_status", ""), 0) + 1
        if row.get("quality_status") == "NEEDS_REVIEW":
            product = products.get(row.get("sku", ""), {})
            model_review_rows.append({
                "sku": row.get("sku", ""),
                "source_last_seen": product.get("source_last_seen", ""),
                "name_es_raw": product.get("name_es_raw", ""),
                "name_zh_standard": row.get("name_zh_standard", ""),
                "spec_es_raw": product.get("spec_es_raw", ""),
                "spec_zh_standard": row.get("spec_zh_standard", ""),
                "model_notes": row.get("notes", ""),
                "human_decision": "",
                "human_note": "",
            })
    write("manual_review_model_quality.csv", model_review_rows, list(model_review_rows[0]) if model_review_rows else ["sku"])
    summary = {
        "latest_date": latest,
        "source_damage_rows": len(damage_rows),
        "source_damage_current_rows": sum(row["in_current"] == "1" for row in damage_rows),
        "brand_series_rows": len(brand_rows),
        "brand_confirmed_rows": sum(row["decision"] == "CONFIRMED_BRAND_OR_IP" for row in brand_rows),
        "series_style_rows": sum(row["decision"] == "PRODUCT_SERIES_OR_STYLE" for row in brand_rows),
        "brand_undecided_rows": sum(row["decision"] in {"", "STYLE_OR_MODEL_REVIEW"} for row in brand_rows),
        "term_rows": len(term_rows),
        "model_needs_review_rows": len(model_review_rows),
        "model_needs_review_current_rows": sum(
            products.get(row["sku"], {}).get("source_last_seen") == latest
            and products.get(row["sku"], {}).get("name_zh_standard") == "中文品名待人工核验"
            for row in model_review_rows
        ),
        "model_quality_status": quality,
        "instructions": [
            "只填写 human_decision 和 human_note，不直接改原始列。",
            "SOURCE_DAMAGED 没有可信来源时保持隔离；SOURCE_POLLUTED 先确认网页 UI 文案，不用模型反向生成西语。",
            "品牌/IP/系列确认后再由程序写入锁定值或品牌字典。",
        ],
    }
    (DICTIONARY / "manual_review_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

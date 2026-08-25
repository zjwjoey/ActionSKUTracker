"""写入第二阶段已确认的当前商品中文品名及用户确认的品牌。"""
from __future__ import annotations

from datetime import date
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from action_tracker.dictionary import (  # noqa: E402
    BRAND_DICTIONARY_HEADERS,
    OVERRIDE_HEADERS,
    load_dictionary_rows,
    write_dictionary_csv,
)


CONFIRMED = {
    "2557704": "Troppie果味饮料",
    "3201830": "拖鞋",
    "3209615": "Furby Furblets电子宠物",
    "3209639": "Rodeo混合糖果",
    "3209865": "Gabby's Dollhouse玩具套装",
    "3209915": "Absolu Chic Paris毛毯",
    "3209975": "锯齿纹装饰靠垫",
    "3210263": "TCX防裂丙烯酸密封胶套装",
    "3210285": "3M Command装饰夹",
    "3217162": "帽子",
    "3218007": "墙面装饰",
    "3219656": "Zed Candy USA硬糖",
    "3222377": "K歌套装",
    "3223230": "Quality Street巧克力糖果",
    "3225423": "Jacky-M自粘假指甲",
}

CONFIRMED_BRANDS = {"Troppie"}


def main() -> int:
    dictionary = ROOT / "runtime" / "dictionary"
    path = dictionary / "manual_overrides.csv"
    rows = load_dictionary_rows(path, headers=OVERRIDE_HEADERS, key_fields=("scope", "key", "field"))
    by_key = {(row["scope"], row["key"], row["field"]): row for row in rows}
    today = date.today().isoformat()
    for sku, value in CONFIRMED.items():
        by_key[("product", sku, "name_zh_standard")] = {
            "scope": "product", "key": sku, "field": "name_zh_standard", "value": value,
            "reason": "第二阶段人工审查：类目、规格或详情足以确认商品本体",
            "source": "STAGE2_HUMAN_REVIEW_2026-08-25", "locked": "1", "updated_at": today,
        }
        by_key[("product", sku, "locked")] = {
            "scope": "product", "key": sku, "field": "locked", "value": "1",
            "reason": "第二阶段人工审查结果锁定，防止后续模型覆盖",
            "source": "STAGE2_HUMAN_REVIEW_2026-08-25", "locked": "1", "updated_at": today,
        }
    write_dictionary_csv(path, list(by_key.values()), OVERRIDE_HEADERS, key_fields=("scope", "key", "field"))
    brand_path = dictionary / "brand_dictionary.csv"
    brands = load_dictionary_rows(brand_path, headers=BRAND_DICTIONARY_HEADERS, key_fields=("brand_id",))
    known_names = {row["canonical_name"].casefold() for row in brands if row["canonical_name"]}
    added_brands = 0
    for brand in sorted(CONFIRMED_BRANDS):
        if brand.casefold() in known_names:
            continue
        brands.append({
            "brand_id": brand, "canonical_name": brand, "aliases_es": brand,
            "keep_original": "1", "is_action_brand": "0", "confidence": "HUMAN_CONFIRMED",
            "review_status": "HUMAN_REVIEWED",
            "notes": "用户于 2026-08-25 确认；Action ES SKU 2557704 的饮料品牌。",
        })
        added_brands += 1
    write_dictionary_csv(brand_path, brands, BRAND_DICTIONARY_HEADERS, key_fields=("brand_id",))
    print({"applied": len(CONFIRMED), "added_brands": added_brands, "override_file": str(path)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

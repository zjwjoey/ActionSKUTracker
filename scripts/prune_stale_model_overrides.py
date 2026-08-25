"""删除来源哈希已失效的模型翻译覆盖；失效覆盖不可继续留在正式字典中。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from action_tracker.dictionary import (  # noqa: E402
    MODEL_TRANSLATION_HEADERS,
    PRODUCT_DICTIONARY_HEADERS,
    load_dictionary_rows,
    write_dictionary_csv,
)


def main() -> int:
    dictionary = ROOT / "runtime" / "dictionary"
    products = {
        row["sku"]: row for row in load_dictionary_rows(
            dictionary / "product_dictionary.csv", headers=PRODUCT_DICTIONARY_HEADERS, key_fields=("sku",)
        )
    }
    overrides = load_dictionary_rows(
        dictionary / "model_translation_overrides.csv", headers=MODEL_TRANSLATION_HEADERS, key_fields=("sku",)
    )
    kept = [row for row in overrides if row["sku"] in products and row["source_hash"] == products[row["sku"]]["source_hash"]]
    removed = len(overrides) - len(kept)
    write_dictionary_csv(
        dictionary / "model_translation_overrides.csv", kept, MODEL_TRANSLATION_HEADERS, key_fields=("sku",)
    )
    print({"kept": len(kept), "removed_stale": removed})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

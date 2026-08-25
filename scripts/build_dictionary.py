"""从正式 Master 生成第一版本地 Action 字典。"""
from __future__ import annotations

import json
import hashlib
import re
from datetime import datetime
from pathlib import Path

import yaml

from action_tracker.config import ensure_runtime_dirs, load_settings
from action_tracker.dictionary import (
    BRAND_DICTIONARY_HEADERS,
    CATEGORY_DICTIONARY_HEADERS,
    OVERRIDE_HEADERS,
    MODEL_TRANSLATION_HEADERS,
    PRODUCT_DICTIONARY_HEADERS,
    SOURCE_DAMAGE_HEADERS,
    TERM_DICTIONARY_HEADERS,
    DICTIONARY_SCHEMA_VERSION,
    build_product_dictionary,
    category_rows_from_products,
    load_dictionary_csv,
    load_dictionary_rows,
    index_product_overrides,
    index_model_translations,
    normalize_category_key,
    write_manifest,
    write_dictionary_csv,
)
from action_tracker.excel.reader import load_current, load_long_term_official
from action_tracker.dictionary_sources import (
    is_polluted_source_field,
    load_brand_reference,
    load_clean_historical_spanish_reference,
    load_standardized_seed,
    restore_spanish_facts,
)


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")


def _source_path(cfg: dict, key: str) -> Path:
    raw = (cfg.get("dictionary_sources") or {}).get(key, "")
    path = Path(raw)
    return path if path.is_absolute() else cfg["project_root"] / path


def _dictionary_input(runtime_path: Path, baseline_dir: Path) -> Path:
    """优先读取本地运行结果；首次在新工作区运行时回退到 Git 基线。"""
    return runtime_path if runtime_path.exists() else baseline_dir / runtime_path.name


def main() -> int:
    cfg = load_settings()
    ensure_runtime_dirs(cfg)
    out_dir: Path = cfg["paths"]["dictionary"]
    baseline_dir: Path = cfg["paths"]["dictionary_baseline"]
    master = cfg["paths"]["master"]
    # CURRENT 只是一日的在售切片；长期字典必须以 08 的所有正式 SKU 为底。
    records = load_long_term_official(master)
    spanish_reference_path = _source_path(cfg, "historical_spanish_reference")
    repair_reference_path = _source_path(cfg, "historical_spanish_repair_reference")
    if repair_reference_path.exists():
        spanish_reference_path = repair_reference_path
    recovered_spanish_fields = restore_spanish_facts(
        records, load_clean_historical_spanish_reference(spanish_reference_path),
    )
    current_records = load_current(master)
    for sku, current in current_records.items():
        merged = records.setdefault(sku, {"sku": sku})
        for field, value in current.items():
            if value is not None and str(value).strip():
                merged[field] = value
    # 详情页/列表页的网页按钮文案偶尔会错位进入规格列。保留污染记录，
    # 但不把它当作西语事实，也不让旧译文继续冒充有效规格。
    polluted_source_fields: dict[str, set[str]] = {}
    for sku, record in records.items():
        if is_polluted_source_field("spec_es", record.get("spec_es")):
            polluted_source_fields.setdefault(sku, set()).add("spec_es_raw")
            record["spec_es"] = ""
            record["_clear_spec_es"] = True
    standardized_seed_path = _source_path(cfg, "standardized_seed")
    standardized_seed = load_standardized_seed(standardized_seed_path)
    mapping_file = cfg["project_root"] / "config" / "dictionary_categories.yaml"
    mapping_raw = yaml.safe_load(mapping_file.read_text(encoding="utf-8")) if mapping_file.exists() else {}
    category_mapping = {}
    for key, value in (mapping_raw.get("cat1_mappings") or {}).items():
        category_mapping[normalize_category_key(key)] = value
    product_path = out_dir / "product_dictionary.csv"
    existing = load_dictionary_csv(_dictionary_input(product_path, baseline_dir), key_field="sku")
    override_path = out_dir / "manual_overrides.csv"
    category_path = out_dir / "category_dictionary.csv"
    existing_categories = load_dictionary_rows(
        _dictionary_input(category_path, baseline_dir), headers=CATEGORY_DICTIONARY_HEADERS, key_fields=("cat1_es", "cat2_es"),
    )
    override_rows = load_dictionary_rows(
        _dictionary_input(override_path, baseline_dir), headers=OVERRIDE_HEADERS, key_fields=("scope", "key", "field"),
    )
    model_translation_path = out_dir / "model_translation_overrides.csv"
    model_translation_rows = load_dictionary_rows(
        _dictionary_input(model_translation_path, baseline_dir), headers=MODEL_TRANSLATION_HEADERS, key_fields=("sku",),
    )
    products = build_product_dictionary(
        records, existing, category_mapping=category_mapping,
        product_overrides=index_product_overrides(override_rows), standardized_seed=standardized_seed,
        model_translations=index_model_translations(model_translation_rows),
    )
    categories = category_rows_from_products(
        products, category_mapping, existing=existing_categories,
    )
    source_damage_rows = []
    for row in products:
        damaged = [
            field for field in ("name_es_raw", "spec_es_raw", "cat1_es", "cat2_es")
            if _CJK_RE.search(_text(row.get(field)))
        ]
        polluted = sorted(polluted_source_fields.get(row["sku"], set()))
        fields = sorted(set(damaged) | set(polluted))
        if fields:
            source_damage_rows.append({
                "sku": row["sku"],
                "damaged_fields": ",".join(fields),
                "status": "SOURCE_POLLUTED" if polluted else "SOURCE_DAMAGED",
                "notes": (
                    "网页 UI 文案错位进入规格字段；已清空该西语字段，禁止当作商品事实"
                    if polluted else
                    "未找到可信西语原始证据；禁止将中文反向翻译成西语"
                ),
            })
    product_changed = write_dictionary_csv(
        product_path, products, PRODUCT_DICTIONARY_HEADERS, key_fields=("sku",),
    )
    category_changed = write_dictionary_csv(
        category_path, categories, CATEGORY_DICTIONARY_HEADERS, key_fields=("cat1_es", "cat2_es"),
    )
    write_dictionary_csv(
        out_dir / "source_damage_report.csv", source_damage_rows, SOURCE_DAMAGE_HEADERS,
        key_fields=("sku",),
    )
    brand_path = out_dir / "brand_dictionary.csv"
    existing_brands = load_dictionary_rows(
        brand_path, headers=BRAND_DICTIONARY_HEADERS, key_fields=("brand_id",),
    )
    reference_brands = load_brand_reference(_source_path(cfg, "brand_reference"))
    brand_rows = {row["brand_id"]: row for row in reference_brands}
    brand_rows.update({row["brand_id"]: row for row in existing_brands})
    brand_changed = write_dictionary_csv(
        brand_path, [brand_rows[key] for key in sorted(brand_rows)], BRAND_DICTIONARY_HEADERS,
        key_fields=("brand_id",),
    )
    term_path = out_dir / "term_dictionary.csv"
    existing_terms = load_dictionary_rows(
        term_path, headers=TERM_DICTIONARY_HEADERS, key_fields=("term_es", "term_type"),
    )
    term_rows = {(_text(row.get("term_es")), _text(row.get("term_type"))): row for row in existing_terms}
    term_seed_path = _source_path(cfg, "term_seed")
    term_seed_raw = yaml.safe_load(term_seed_path.read_text(encoding="utf-8")) if term_seed_path.exists() else {}
    for raw in (term_seed_raw.get("terms") or []):
        row = {header: _text(raw.get(header)) for header in TERM_DICTIONARY_HEADERS}
        key = (row["term_es"], row["term_type"])
        if all(key) and key not in term_rows:
            term_rows[key] = row
    term_changed = write_dictionary_csv(
        term_path, [term_rows[key] for key in sorted(term_rows)], TERM_DICTIONARY_HEADERS,
        key_fields=("term_es", "term_type"),
    )
    for filename, headers in (
        ("model_translation_overrides.csv", MODEL_TRANSLATION_HEADERS),
        ("manual_overrides.csv", OVERRIDE_HEADERS),
    ):
        path = out_dir / filename
        if not path.exists():
            key_fields = {
                "model_translation_overrides.csv": ("sku",),
                "manual_overrides.csv": ("scope", "key", "field"),
            }[filename]
            initial_rows = {
                "model_translation_overrides.csv": model_translation_rows,
                "manual_overrides.csv": override_rows,
            }[filename]
            write_dictionary_csv(path, initial_rows, headers, key_fields=key_fields)
    write_manifest(out_dir / "build_manifest.json", {
        "schema_version": DICTIONARY_SCHEMA_VERSION,
        "built_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_master": str(master),
        "dictionary_baseline": str(baseline_dir),
        "source_master_sha256": hashlib.sha256(master.read_bytes()).hexdigest(),
        "long_term_official_skus": len(load_long_term_official(master)),
        "current_skus": len(current_records),
        "effective_product_skus": len(products),
        "category_rows": len(categories),
        "missing_source_first_seen": sum(not row["source_first_seen"] for row in products),
        "missing_source_last_seen": sum(not row["source_last_seen"] for row in products),
        "recovered_historical_spanish_fields": recovered_spanish_fields,
        "standardized_seed_rows": len(standardized_seed),
        "brand_reference_rows": len(reference_brands),
        "product_changed": product_changed,
        "category_changed": category_changed,
        "brand_changed": brand_changed,
        "term_rows": len(term_rows),
        "term_changed": term_changed,
        "source_damage_skus": len(source_damage_rows),
        "source_damage_fields": sum(len(row["damaged_fields"].split(",")) for row in source_damage_rows),
    })
    print(json.dumps({
        "master": str(master),
        "dictionary_dir": str(out_dir),
        "dictionary_baseline": str(baseline_dir),
        "product_rows": len(products),
        "category_rows": len(categories),
        "recovered_historical_spanish_fields": recovered_spanish_fields,
        "standardized_seed_rows": len(standardized_seed),
        "brand_reference_rows": len(reference_brands),
        "preserved_existing_rows": len(existing),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

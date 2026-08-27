"""独立审查本地 Action 字典与运行时翻译结果。"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from action_tracker.dictionary import (  # noqa: E402
    BRAND_DICTIONARY_HEADERS,
    CATEGORY_DICTIONARY_HEADERS,
    DICTIONARY_BASELINE_FILENAMES,
    MODEL_TRANSLATION_HEADERS,
    PRODUCT_DICTIONARY_HEADERS,
    SOURCE_DAMAGE_HEADERS,
    TERM_DICTIONARY_HEADERS,
)
from action_tracker.dictionary_sources import is_polluted_source_field  # noqa: E402
from action_tracker.excel.reader import load_current  # noqa: E402
import refine_dictionary_translations as refiner  # noqa: E402


CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
FIXED_CATEGORIES = {
    "DIY五金", "办公文具", "宠物用品", "厨房餐具", "服饰鞋包", "个人美容", "家居布置",
    "家务清洁", "旅行用品", "食品饮料", "数码影音", "玩具", "兴趣手作", "园艺户外", "运动用品",
}


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def duplicate_values(rows: list[dict[str, str]], fields: tuple[str, ...]) -> list[str]:
    seen: set[tuple[str, ...]] = set()
    duplicates: list[str] = []
    for row in rows:
        key = tuple((row.get(field) or "").strip() for field in fields)
        if key in seen:
            duplicates.append("/".join(key))
        seen.add(key)
    return duplicates


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def baseline_file_set_mismatches(files: object) -> tuple[list[str], list[str]]:
    if isinstance(files, (str, bytes)):
        actual = set()
    else:
        try:
            actual = set(files)  # type: ignore[arg-type]
        except TypeError:
            actual = set()
    expected = set(DICTIONARY_BASELINE_FILENAMES)
    return sorted(expected - actual), sorted(actual - expected)


def main() -> int:
    dictionary = ROOT / "runtime" / "dictionary"
    master = ROOT / "runtime" / "master" / "Action_Master.xlsx"
    checks: list[dict[str, object]] = []

    def check(name: str, passed: bool, details: object, *, warning: bool = False) -> None:
        status = "WARN" if warning and not passed else ("PASS" if passed else "FAIL")
        checks.append({"name": name, "status": status, "details": details})

    specs = {
        "product_dictionary.csv": (PRODUCT_DICTIONARY_HEADERS, ("sku",)),
        "brand_dictionary.csv": (BRAND_DICTIONARY_HEADERS, ("brand_id",)),
        "category_dictionary.csv": (CATEGORY_DICTIONARY_HEADERS, ("cat1_es", "cat2_es")),
        "term_dictionary.csv": (TERM_DICTIONARY_HEADERS, ("term_es", "term_type")),
        "model_translation_overrides.csv": (MODEL_TRANSLATION_HEADERS, ("sku",)),
        "source_damage_report.csv": (SOURCE_DAMAGE_HEADERS, ("sku",)),
    }
    tables: dict[str, list[dict[str, str]]] = {}
    for filename, (headers, keys) in specs.items():
        path = dictionary / filename
        actual, rows = read_csv(path)
        tables[filename] = rows
        check(f"schema:{filename}", actual == headers, {"expected": headers, "actual": actual})
        duplicates = duplicate_values(rows, keys)
        required_keys = ("cat1_es",) if filename == "category_dictionary.csv" else keys
        empty_keys = ["/".join((row.get(key) or "").strip() for key in required_keys) for row in rows if any(not (row.get(key) or "").strip() for key in required_keys)]
        check(f"keys:{filename}", not duplicates and not empty_keys, {"duplicates": duplicates[:10], "empty": empty_keys[:10]})

    products = tables["product_dictionary.csv"]
    brands = tables["brand_dictionary.csv"]
    categories = tables["category_dictionary.csv"]
    model_rows = tables["model_translation_overrides.csv"]
    damage_rows = tables["source_damage_report.csv"]
    # Manual product overrides are the approved field-level dictionary layer.
    # Apply them to the audit view so accepted values are audited exactly as
    # exports resolve them, while keeping the raw product table checks intact.
    _, manual_rows = read_csv(dictionary / "manual_overrides.csv")
    manual_by_product: dict[str, dict[str, str]] = {}
    for override in manual_rows:
        if (override.get("scope") or "").strip() != "product":
            continue
        sku = (override.get("key") or "").strip()
        field = (override.get("field") or "").strip()
        value = (override.get("value") or "").strip()
        if sku and field and value:
            manual_by_product.setdefault(sku, {})[field] = value
    effective_products = []
    for product in products:
        effective = dict(product)
        overrides = manual_by_product.get(product.get("sku", ""), {})
        effective.update(overrides)
        if overrides.get("name_zh_standard"):
            # A confirmed field-level name override is a usable translation
            # even when the raw product row still carries the old fallback
            # status; this mirrors Resolver precedence.
            effective["translation_status"] = "HUMAN_REVIEWED"
        effective_products.append(effective)
    baseline = ROOT / "data" / "dictionary"
    baseline_manifest_path = baseline / "baseline_manifest.json"
    if baseline_manifest_path.exists():
        baseline_manifest = json.loads(baseline_manifest_path.read_text(encoding="utf-8"))
        missing_files, unexpected_files = baseline_file_set_mismatches(
            (baseline_manifest.get("files") or {}).keys()
        )
        check("versioned_baseline_file_set", not missing_files and not unexpected_files, {
            "missing": missing_files, "unexpected": unexpected_files,
        }, warning=True)
        baseline_mismatches = []
        for filename, entry in (baseline_manifest.get("files") or {}).items():
            baseline_file = baseline / filename
            runtime_file = dictionary / filename
            if not baseline_file.exists() or not runtime_file.exists():
                baseline_mismatches.append(filename)
                continue
            if sha256(baseline_file) != entry.get("sha256") or sha256(runtime_file) != entry.get("sha256"):
                baseline_mismatches.append(filename)
        check("versioned_baseline_matches_runtime", not baseline_mismatches, {
            "mismatches": baseline_mismatches,
        }, warning=True)
    else:
        check("versioned_baseline_matches_runtime", False, {"missing": str(baseline_manifest_path)}, warning=True)
    latest = max((row.get("source_last_seen", "") for row in products), default="")
    product_by_sku = {row["sku"]: row for row in products}
    master_current = load_current(master)
    current_skus = set(master_current)
    current = [product_by_sku[sku] for sku in sorted(current_skus) if sku in product_by_sku]
    check("master_hash", hashlib.sha256(master.read_bytes()).hexdigest() == json.loads((dictionary / "build_manifest.json").read_text(encoding="utf-8"))["source_master_sha256"], "Master 未被构建流程改动")
    dictionary_current_skus = {row["sku"] for row in current}
    check("current_sku_set_exact", dictionary_current_skus == current_skus, {
        "master_current": len(current_skus), "dictionary_current": len(dictionary_current_skus),
        "missing": sorted(current_skus - dictionary_current_skus)[:20],
        "extra": sorted(dictionary_current_skus - current_skus)[:20],
    })
    check("current_first_seen_complete", not [row["sku"] for row in current if not row.get("source_first_seen")], {
        "missing_skus": [row["sku"] for row in current if not row.get("source_first_seen")][:50],
    }, warning=True)
    check("current_source_is_spanish", not any(CJK.search(row.get(field, "") or "") for row in current for field in ("name_es_raw", "spec_es_raw", "cat1_es", "cat2_es")), {"latest_date": latest})
    check("current_chinese_name_complete", not any(not row.get("name_zh_standard") for row in current), {"blank_skus": [row["sku"] for row in current if not row.get("name_zh_standard")][:20]})
    placeholders = [row["sku"] for row in current if row.get("name_zh_standard") == "中文品名待人工核验"]
    check("current_translation_placeholders", not placeholders, {"placeholder_skus": placeholders[:50]})
    check("current_categories_fixed", all(row.get("cat1_zh") in FIXED_CATEGORIES for row in current), sorted({row.get("cat1_zh", "") for row in current} - FIXED_CATEGORIES))
    check("category_dictionary_fixed", all(row.get("cat1_zh") in FIXED_CATEGORIES for row in categories), sorted({row.get("cat1_zh", "") for row in categories} - FIXED_CATEGORIES))
    check("brand_names_unique", len({(row.get("canonical_name") or "").casefold() for row in brands}) == len(brands), len(brands))
    brand_reference_keys = set()
    for row in brands:
        values = [row.get("brand_id") or "", row.get("canonical_name") or ""]
        values.extend(part.strip() for part in (row.get("aliases_es") or "").split("|") if part.strip())
        brand_reference_keys.update(" ".join(value.casefold().split()) for value in values if value)
    dangling_brand_ids = sorted({
        row.get("brand_id") or "" for row in products
        if row.get("brand_id") and " ".join((row.get("brand_id") or "").casefold().split()) not in brand_reference_keys
    })
    check("product_brand_references", not dangling_brand_ids, {"missing_brand_ids": dangling_brand_ids[:20]})

    stale_overrides = [row["sku"] for row in model_rows if row["sku"] in product_by_sku and row["source_hash"] != product_by_sku[row["sku"]]["source_hash"]]
    missing_override_skus = [row["sku"] for row in model_rows if row["sku"] not in product_by_sku]
    check("model_override_source_hash", not stale_overrides and not missing_override_skus, {"stale": stale_overrides[:20], "missing": missing_override_skus[:20]})

    actual_damage: dict[str, set[str]] = {}
    for row in products:
        damaged = {field for field in ("name_es_raw", "spec_es_raw", "cat1_es", "cat2_es") if CJK.search(row.get(field, "") or "")}
        if damaged:
            actual_damage[row["sku"]] = damaged
    reported_damage = {
        row["sku"]: set((row.get("damaged_fields") or "").split(","))
        for row in damage_rows if row.get("status") == "SOURCE_DAMAGED"
    }
    polluted_report = [row for row in damage_rows if row.get("status") == "SOURCE_POLLUTED"]
    check("source_damage_report_exact", actual_damage == reported_damage, {"actual_skus": len(actual_damage), "reported_skus": len(reported_damage)})
    raw_polluted = [
        sku for sku, row in master_current.items()
        if is_polluted_source_field("spec_es", row.get("spec_es"))
    ]
    reported_polluted_skus = {row["sku"] for row in polluted_report if row["sku"] in current_skus}
    check("source_ui_pollution_isolated", reported_polluted_skus == set(raw_polluted), {
        "raw_master_skus": raw_polluted[:20], "reported_skus": sorted(reported_polluted_skus)[:20],
    }, warning=True)

    brand_rows = tables["brand_dictionary.csv"]
    brands_for_scan = sorted({row["canonical_name"] for row in brand_rows if row.get("canonical_name")}, key=len, reverse=True)
    candidates = refiner._candidates(effective_products, brands_for_scan)
    reviewed = refiner._reviewed_non_translation_keys(dictionary)
    manual_complete = {
        sku for sku, overrides in manual_by_product.items()
        if overrides.get("name_zh_standard") and overrides.get("spec_zh_standard")
    }
    unresolved = [
        item["sku"] for item in candidates
        if item["sku"] not in manual_complete
        and (item["sku"], item["source_hash"]) not in reviewed
        and item["sku"] in current_skus
    ]
    check("current_translation_residual", not unresolved, {"latest_date": latest, "unresolved_skus": unresolved[:50]})
    pending_review = [row["sku"] for row in current if row.get("review_status") == "NEEDS_REVIEW"]
    check("current_review_pending", not pending_review, {"pending_skus": pending_review[:50]}, warning=True)

    queue_path = dictionary / "brand_review_queue.csv"
    queue_headers, queue = read_csv(queue_path)
    undecided = [row["sku"] for row in queue if row.get("decision") in {"STYLE_OR_MODEL_REVIEW", ""}]
    check("brand_review_queue_complete", not undecided, {"rows": len(queue), "undecided": undecided[:50]})

    failures = [item for item in checks if item["status"] == "FAIL"]
    warnings = [item for item in checks if item["status"] == "WARN"]
    report = {
        "audit_date": date.today().isoformat(),
        "latest_date": latest,
        "product_rows": len(products),
        "current_rows": len(current),
        "checks": checks,
        "summary": {"pass": len(checks) - len(failures) - len(warnings), "warn": len(warnings), "fail": len(failures)},
    }
    output = dictionary / f"audit_report_{date.today():%Y%m%d}.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), **report["summary"]}, ensure_ascii=False))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

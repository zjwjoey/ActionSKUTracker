"""正式 Observation 后的增量字典标准化。

本模块故意不接入 daily.py：Presence 和 Master 提交已经在此之前冻结。
它只读取已正式提交 snapshot 的官网事实，并只处理 NEW、官网事实哈希
变动、或仍待审核的当前 SKU；不会访问官网、不会调用模型或翻译服务。
"""
from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import yaml

from .dictionary import (
    BRAND_DICTIONARY_HEADERS,
    CATEGORY_DICTIONARY_HEADERS,
    MODEL_TRANSLATION_HEADERS,
    OVERRIDE_HEADERS,
    PRODUCT_DICTIONARY_HEADERS,
    build_product_dictionary,
    category_rows_from_products,
    index_model_translations,
    index_product_overrides,
    load_dictionary_rows,
    normalize_category_key,
    product_source_hash,
    reconcile_brand_rows,
    write_dictionary_csv,
    write_manifest,
)
from .dictionary_sources import is_polluted_source_field


class DictionaryEnrichmentError(RuntimeError):
    """输入并非可审计的正式 Observation，或其证据不完整。"""


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise DictionaryEnrichmentError(f"MISSING_EVIDENCE: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return [{key: _text(value) for key, value in row.items()} for row in csv.DictReader(fh)]


def _write_evidence_csv(path: Path, headers: list[str], rows: Iterable[dict[str, str]]) -> None:
    """写运行证据；空结果保留表头，便于自动化读取。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _snapshot_for_run(cfg: dict[str, Any], run_id: str) -> Path:
    matches = list(Path(cfg["paths"]["snapshots"]).glob(f"*/{run_id}"))
    if len(matches) != 1:
        raise DictionaryEnrichmentError(f"FORMAL_RUN_NOT_FOUND: {run_id}")
    return matches[0]


def _validate_formal_snapshot(snapshot: Path, run_id: str) -> dict[str, Any]:
    try:
        qa = json.loads((snapshot / "qa_report.json").read_text(encoding="utf-8"))
        report = json.loads((snapshot / "run_report.json").read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DictionaryEnrichmentError(f"MISSING_FORMAL_GATE: {snapshot}") from exc
    except json.JSONDecodeError as exc:
        raise DictionaryEnrichmentError(f"INVALID_FORMAL_GATE: {snapshot}") from exc
    if report.get("run_id") != run_id:
        raise DictionaryEnrichmentError(f"RUN_ID_MISMATCH: {run_id}")
    if report.get("dry_run") is not False:
        raise DictionaryEnrichmentError("DRY_RUN_NOT_ALLOWED")
    if report.get("commit_status") != "FULL_COMMIT":
        raise DictionaryEnrichmentError("FORMAL_FULL_COMMIT_REQUIRED")
    if not qa.get("passed") or qa.get("state") not in {"PASS", "PASS_PRESENCE_ONLY"}:
        raise DictionaryEnrichmentError("QA_PASS_REQUIRED")
    return report


def _record_from_snapshot(row: dict[str, str]) -> dict[str, str]:
    """将 snapshot 的列名收敛为商品字典唯一需要的官网事实。"""
    record = {
        "sku": _text(row.get("sku")),
        "canonical_id": _text(row.get("canonical_id")),
        "name_es": _text(row.get("name_es")),
        "cat1_es": _text(row.get("cat1_es")),
        "cat2_es": _text(row.get("cat2_es")),
        "spec_es": _text(row.get("spec_es")),
        "name_zh": _text(row.get("name_zh")),
        "cat1_zh": _text(row.get("cat1_zh")),
        "cat2_zh": _text(row.get("cat2_zh")),
        "spec_zh": _text(row.get("spec_zh")),
        "translation_status": _text(row.get("translation_status")),
        "first_seen": _text(row.get("first_seen")),
        "last_seen": _text(row.get("last_seen")),
    }
    # 与全量字典构建保持同一条污染隔离规则。网页收藏/品牌导航文案不是
    # 商品规格；若在这里重新写回，增量流程会污染已清理的正式字典。
    if is_polluted_source_field("spec_es", record["spec_es"]):
        record["spec_es"] = ""
        record["_clear_spec_es"] = "1"
    return record


def _fact_hash(record: dict[str, str], existing: dict[str, str] | None = None) -> str:
    """以本轮可证明的官网事实计算哈希。

    Listing 的空列仅代表本轮未补全，不能被解释成官网删除字段。因此空值
    沿用已确认事实；唯一例外是已识别的网页 UI 污染，必须明确清空。
    """
    existing = existing or {}
    def fact(snapshot_field: str, dictionary_field: str) -> str:
        if snapshot_field == "spec_es" and record.get("_clear_spec_es"):
            return ""
        return _text(record.get(snapshot_field)) or _text(existing.get(dictionary_field))
    return product_source_hash({
        "name_es_raw": fact("name_es", "name_es_raw"),
        "cat1_es": fact("cat1_es", "cat1_es"),
        "cat2_es": fact("cat2_es", "cat2_es"),
        "spec_es_raw": fact("spec_es", "spec_es_raw"),
    })


def _load_category_mapping(cfg: dict[str, Any]) -> dict[str, dict[str, str]]:
    path = Path(cfg["project_root"]) / "config" / "dictionary_categories.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    return {
        normalize_category_key(key): {str(k): _text(v) for k, v in value.items()}
        for key, value in (raw.get("cat1_mappings") or {}).items()
    }


def _load_rows(directory: Path, filename: str, headers: list[str], key_fields: tuple[str, ...]) -> list[dict[str, str]]:
    return load_dictionary_rows(directory / filename, headers=headers, key_fields=key_fields)


def select_candidates(
    snapshot_records: dict[str, dict[str, str]],
    sku_delta_rows: Iterable[dict[str, str]],
    products: Iterable[dict[str, str]],
) -> dict[str, set[str]]:
    """仅从本轮当前 SKU 中选择增量项，并为每一项保留可解释原因。"""
    selected: dict[str, set[str]] = {}
    for row in sku_delta_rows:
        sku = _text(row.get("sku"))
        if sku in snapshot_records and _text(row.get("status")) == "NEW":
            selected.setdefault(sku, set()).add("NEW")
    for product in products:
        sku = _text(product.get("sku"))
        record = snapshot_records.get(sku)
        if not record:
            continue
        if _text(product.get("source_hash")) != _fact_hash(record, product):
            selected.setdefault(sku, set()).add("SOURCE_HASH_CHANGED")
        if _text(product.get("translation_status")) == "NEEDS_REVIEW" or _text(product.get("review_status")) == "NEEDS_REVIEW":
            selected.setdefault(sku, set()).add("NEEDS_REVIEW")
    return selected


def processable_candidate_skus(
    candidates: dict[str, set[str]],
    products_by_sku: dict[str, dict[str, str]],
    records: dict[str, dict[str, str]],
    models: dict[str, dict[str, str]],
) -> set[str]:
    """确定哪些候选可以安全重建为字典行。

    `NEW` 是生命周期事件，并不保证该 SKU 从未进入长期字典。若其官网事实
    未变，不能以本轮 Listing 的 fallback 中文覆盖已有确认结果；它只需留下
    本轮审计证据。待审核项同理，只有匹配当前事实哈希的既有模型结果才可
    无网络地重新套用。
    """
    processable: set[str] = set()
    for sku, reasons in candidates.items():
        old = products_by_sku.get(sku)
        if not old or "SOURCE_HASH_CHANGED" in reasons:
            processable.add(sku)
            continue
        model = models.get(sku, {})
        if "NEEDS_REVIEW" in reasons and _text(model.get("source_hash")) == _fact_hash(records[sku], old):
            processable.add(sku)
    return processable


def _candidate_review_reasons(rows: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    """Step 4 只产出待审核证据；Step 5 再接管统一 Review Queue。"""
    output: list[dict[str, str]] = []
    for row in rows:
        sku = row["sku"]
        if row["translation_status"] in {"UNTRANSLATED", "NEEDS_REVIEW"} or row["review_status"] == "NEEDS_REVIEW":
            output.append({"sku": sku, "reason": "NAME_OR_SPEC_REVIEW", "detail": "无有效人工/字典/模型同源中文结果；未调用模型。"})
        if not row["brand_id"]:
            output.append({"sku": sku, "reason": "BRAND_IDENTIFICATION_PENDING", "detail": "官网事实未提供可确认品牌；程序不会从品名猜测品牌。"})
        if not row["cat1_zh"]:
            output.append({"sku": sku, "reason": "CATEGORY_REVIEW", "detail": "未命中 15 个固定一级类目映射。"})
    return output


def enrich_dictionary(cfg: dict[str, Any], *, run_id: str) -> dict[str, Any]:
    """基于一个 QA PASS + FULL_COMMIT snapshot 做一次最小字典增量更新。"""
    snapshot = _snapshot_for_run(cfg, run_id)
    report = _validate_formal_snapshot(snapshot, run_id)
    snapshot_rows = _read_csv(snapshot / "products_normalized.csv")
    records = {_text(row.get("sku")): _record_from_snapshot(row) for row in snapshot_rows if _text(row.get("sku"))}
    if len(records) != len(snapshot_rows):
        raise DictionaryEnrichmentError("SNAPSHOT_DUPLICATE_OR_EMPTY_SKU")

    dictionary = Path(cfg["paths"]["dictionary"])
    products = _load_rows(dictionary, "product_dictionary.csv", PRODUCT_DICTIONARY_HEADERS, ("sku",))
    candidates = select_candidates(records, _read_csv(snapshot / "sku_delta.csv"), products)
    candidate_skus = sorted(candidates)
    by_sku = {row["sku"]: row for row in products}
    overrides = index_product_overrides(_load_rows(dictionary, "manual_overrides.csv", OVERRIDE_HEADERS, ("scope", "key", "field")))
    models = index_model_translations(_load_rows(dictionary, "model_translation_overrides.csv", MODEL_TRANSLATION_HEADERS, ("sku",)))
    processable_skus = processable_candidate_skus(candidates, by_sku, records, models)
    candidate_records = {sku: records[sku] for sku in sorted(processable_skus)}
    candidate_existing = {sku: by_sku[sku] for sku in processable_skus if sku in by_sku}
    category_mapping = _load_category_mapping(cfg)
    updated_candidates = build_product_dictionary(
        candidate_records, candidate_existing, category_mapping=category_mapping,
        product_overrides=overrides, model_translations=models,
        updated_at=_text(report.get("run_date")) or datetime.now().date().isoformat(),
    )
    updated_by_sku = {row["sku"]: row for row in updated_candidates}
    merged_products = [updated_by_sku.get(row["sku"], row) for row in products]
    for sku in candidate_skus:
        if sku not in by_sku:
            merged_products.append(updated_by_sku[sku])
    merged_products.sort(key=lambda row: row["sku"])

    existing_categories = _load_rows(dictionary, "category_dictionary.csv", CATEGORY_DICTIONARY_HEADERS, ("cat1_es", "cat2_es"))
    merged_categories = category_rows_from_products(updated_candidates, category_mapping, existing=existing_categories)
    existing_brands = _load_rows(dictionary, "brand_dictionary.csv", BRAND_DICTIONARY_HEADERS, ("brand_id",))
    merged_brands = reconcile_brand_rows(merged_products, {row["brand_id"]: row for row in existing_brands})

    product_changed = write_dictionary_csv(dictionary / "product_dictionary.csv", merged_products, PRODUCT_DICTIONARY_HEADERS, key_fields=("sku",))
    category_changed = write_dictionary_csv(dictionary / "category_dictionary.csv", merged_categories, CATEGORY_DICTIONARY_HEADERS, key_fields=("cat1_es", "cat2_es"))
    brand_changed = write_dictionary_csv(dictionary / "brand_dictionary.csv", merged_brands, BRAND_DICTIONARY_HEADERS, key_fields=("brand_id",))

    output_dir = dictionary / "enrichment" / run_id
    selected_rows = [
        {"sku": sku, "reasons": "|".join(sorted(candidates[sku])), "source_hash": _fact_hash(records[sku], by_sku.get(sku))}
        for sku in candidate_skus
    ]
    effective_candidate_rows = [updated_by_sku.get(sku, by_sku[sku]) for sku in candidate_skus]
    review_reasons = _candidate_review_reasons(effective_candidate_rows)
    for filename, headers, rows in (
        ("selected_skus.csv", ["sku", "reasons", "source_hash"], selected_rows),
        ("review_candidates.csv", ["sku", "reason", "detail"], review_reasons),
    ):
        _write_evidence_csv(output_dir / filename, headers, rows)

    result = {
        "run_id": run_id,
        "run_date": report.get("run_date"),
        "snapshot": str(snapshot),
        "selected_skus": len(candidate_skus),
        "standardized_skus": len(processable_skus),
        "selected_by_reason": {reason: sum(reason in reasons for reasons in candidates.values()) for reason in ("NEW", "SOURCE_HASH_CHANGED", "NEEDS_REVIEW")},
        "review_candidates": len(review_reasons),
        "product_changed": product_changed,
        "category_changed": category_changed,
        "brand_changed": brand_changed,
        "model_or_network_called": False,
        "translation_enabled": bool(cfg.get("run", {}).get("translation_enabled")),
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    write_manifest(output_dir / "enrichment_report.json", result)
    return result

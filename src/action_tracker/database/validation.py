"""Deterministic Excel -> SQLite Mirror validation gates."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from .connection import connect
from .migration import SHEET_CONFIG, _canonical_raw_json, _map, _maybe_float, _raw_hash, _records, _row_to_dict, _text, sha256_file


def _read_rows(master_path: Path) -> dict[str, list[tuple[int, list[str], list[Any]]]]:
    wb = load_workbook(master_path, read_only=True, data_only=True)
    try:
        return {name: list(_records(wb[name], header_row)) for name, header_row in SHEET_CONFIG.items()}
    finally:
        wb.close()


def _get(headers: list[str], values: list[Any], label: str, default: Any = "") -> Any:
    return values[headers.index(label)] if label in headers else default


def _source_counts(rows: dict[str, list[tuple[int, list[str], list[Any]]]]) -> dict[str, Any]:
    counts: dict[str, Any] = {name: {"rows": len(items)} for name, items in rows.items()}
    long_rows = rows["08_LONG_TERM_MASTER"]
    long_headers = long_rows[0][1] if long_rows else []
    sku_idx, status_idx = _map(long_headers, "正式SKU"), _map(long_headers, "当前状态")
    formal_skus = {_text(values[sku_idx]) for _, _, values in long_rows if _text(values[sku_idx])}
    counts["08_LONG_TERM_MASTER"].update({
        "formal_sku_count": len(formal_skus),
        "unmatched_count": sum(1 for _, _, values in long_rows if not _text(values[sku_idx])),
        "formal_sku_set": formal_skus,
        "current_sku_set": {_text(values[sku_idx]) for _, _, values in long_rows if _text(values[sku_idx]) and _text(values[status_idx]) == "CURRENT"},
        "canonical_by_sku": {_text(values[sku_idx]): _text(_get(long_headers, values, "实体ID")) for _, _, values in long_rows if _text(values[sku_idx])},
    })
    for name in ("01_SKU_ZH_CURRENT", "02_SKU_ES_CURRENT"):
        items = rows[name]
        headers = items[0][1] if items else []
        idx = _map(headers, "SKU")
        counts[name]["sku_set"] = {_text(values[idx]) for _, _, values in items if _text(values[idx])}
        if name == "02_SKU_ES_CURRENT":
            cidx = _map(headers, "Canonical_ID")
            counts[name]["canonical_by_sku"] = {_text(values[idx]): _text(values[cidx]) for _, _, values in items if _text(values[idx])}
    for name in ("03_PRICE_HISTORY", "04_EVENT_HISTORY"):
        counts[name]["formal_sku_rows"] = sum(1 for _, headers, values in rows[name] if _text(_get(headers, values, "SKU")))
    return counts


def _expected(rows: dict[str, list[tuple[int, list[str], list[Any]]]]) -> dict[str, Any]:
    products, localizations = {}, {}
    for row_no, headers, values in rows["08_LONG_TERM_MASTER"]:
        sku = _text(_get(headers, values, "正式SKU"))
        if not sku:
            continue
        products[sku] = {
            "sku": sku, "canonical_id": _text(_get(headers, values, "实体ID")), "name_es": _text(_get(headers, values, "西班牙语品名")),
            "cat1_es": _text(_get(headers, values, "一级类目（西语）")), "cat2_es": _text(_get(headers, values, "二级类目（西语）")), "spec_es": _text(_get(headers, values, "规格（西语）")),
            "product_url": _text(_get(headers, values, "商品链接")), "current_price": _maybe_float(_get(headers, values, "当前售价 (€)")),
            "historical_min_price": _maybe_float(_get(headers, values, "历史最低价 (€)")), "historical_max_price": _maybe_float(_get(headers, values, "历史最高价 (€)")),
            "current_status_raw": _text(_get(headers, values, "当前状态")), "first_seen_at": _text(_get(headers, values, "首次观察日期"), "首次观察日期"),
            "last_seen_at": _text(_get(headers, values, "最后观察日期"), "最后观察日期"), "description_es": None, "details_es": None, "image_url": None,
            "source_sheet": "08_LONG_TERM_MASTER", "source_row_no": row_no,
            "source_raw_json": _row_to_dict(headers, values),
        }
        localizations[sku] = {
            "sku": sku, "language": "zh", "name": _text(_get(headers, values, "中文品名")), "cat1": _text(_get(headers, values, "一级类目（中文）")),
            "cat2": _text(_get(headers, values, "二级类目（中文）")), "spec": _text(_get(headers, values, "规格（中文）")), "description": None, "details": None,
            "source": "MASTER_08_LONG_TERM_MASTER", "source_hash": None, "review_status": "LEGACY_UNVERIFIED",
            "source_sheet": "08_LONG_TERM_MASTER", "source_row_no": row_no,
        }
    for row_no, headers, values in rows["01_SKU_ZH_CURRENT"]:
        sku = _text(_get(headers, values, "SKU"))
        if sku in localizations:
            localizations[sku].update({"name": _text(_get(headers, values, "中文品名")), "cat1": _text(_get(headers, values, "一级类目（中文）")), "cat2": _text(_get(headers, values, "二级类目（中文）")), "spec": _text(_get(headers, values, "规格（中文）")), "description": _text(_get(headers, values, "中文描述")), "details": _text(_get(headers, values, "中文产品详情")), "source": "MASTER_01_SKU_ZH_CURRENT", "review_status": _text(_get(headers, values, "翻译状态")) or "LEGACY_UNVERIFIED", "source_sheet": "01_SKU_ZH_CURRENT", "source_row_no": row_no})
    for _, headers, values in rows["02_SKU_ES_CURRENT"]:
        sku = _text(_get(headers, values, "SKU"))
        if sku in products:
            products[sku].update({"name_es": _text(_get(headers, values, "西班牙语品名")), "cat1_es": _text(_get(headers, values, "一级类目（西语）")), "cat2_es": _text(_get(headers, values, "二级类目（西语）")), "spec_es": _text(_get(headers, values, "规格（西语）")), "description_es": _text(_get(headers, values, "描述（西语）")), "details_es": _text(_get(headers, values, "产品详情（西语）")), "image_url": _text(_get(headers, values, "图片链接")), "current_price": _maybe_float(_get(headers, values, "当前售价 (€)")), "product_url": _text(_get(headers, values, "商品链接"))})
    prices, events = [], []
    for _, headers, values in rows["03_PRICE_HISTORY"]:
        sku = _text(_get(headers, values, "SKU"))
        if sku in products:
            prices.append({"sku": sku, "canonical_id": _text(_get(headers, values, "Canonical_ID")), "observed_at": _text(_get(headers, values, "日期"), "日期"), "run_id": None, "previous_price": _maybe_float(_get(headers, values, "旧售价 (€)")), "new_price": _maybe_float(_get(headers, values, "新售价 (€)")), "original_price": _maybe_float(_get(headers, values, "原价 (€)")), "unit_price_raw": None, "change_type": _text(_get(headers, values, "变化类型")), "promotion_raw": _text(_get(headers, values, "促销状态")), "source_file": _text(_get(headers, values, "来源文件")), "source_sheet": _text(_get(headers, values, "来源Sheet")), "raw_json": _row_to_json(headers, values)})
    for _, headers, values in rows["04_EVENT_HISTORY"]:
        sku = _text(_get(headers, values, "SKU"))
        if sku in products:
            events.append({"sku": sku, "canonical_id": _text(_get(headers, values, "Canonical_ID")), "occurred_at": _text(_get(headers, values, "日期"), "日期"), "run_id": None, "event_type": _text(_get(headers, values, "事件类型")), "old_value": _text(_get(headers, values, "旧值")), "new_value": _text(_get(headers, values, "新值")), "evidence": _text(_get(headers, values, "备注")), "source_file": _text(_get(headers, values, "来源文件")), "source_sheet": "04_EVENT_HISTORY"})
    run_map = {"Run ID": "run_id", "运行日期": "run_date", "开始时间": "started_at", "结束时间": "finished_at", "Git Commit": "git_commit", "Sitemap SKU数": "sitemap_count", "Listing SKU数": "listing_count", "ACTIVE": "current_count", "NEW": "new_count", "REAPPEARED": "reappeared_count", "MISSING_FIRST": "missing_first_count", "MISSING_CONTINUED": "missing_continued_count", "OFFLINE": "offline_count", "PRICE_UP": "price_up_count", "PRICE_DOWN": "price_down_count", "PROMO_START": "promo_start_count", "PROMO_END": "promo_end_count", "QA状态": "qa_status", "运行状态": "commit_status"}
    run_text_fields = {
        "Run ID": "run_id", "运行日期": "run_date", "开始时间": "started_at", "结束时间": "finished_at",
        "Git Commit": "git_commit", "QA状态": "qa_status", "运行状态": "commit_status",
    }
    runs = []
    for row_no, headers, values in rows["05_RUN_LOG"]:
        record = {}
        for source, target in run_map.items():
            value = _get(headers, values, source)
            record[target] = _text(value, source) if source in run_text_fields else value
        record.update({"access_state": None, "observation_complete": None, "snapshot_path": None, "source_row_no": row_no, "source_raw_json": _row_to_dict(headers, values)})
        runs.append(record)
    reviews = {}
    for row_no, headers, values in rows["06_REVIEW_QUEUE"]:
        reviews[f"MASTER06:{row_no}"] = {"review_id": f"MASTER06:{row_no}", "sku": _text(_get(headers, values, "SKU")) or None, "review_date": _text(_get(headers, values, "日期"), "日期"), "issue_type": _text(_get(headers, values, "问题类型")), "evidence": _text(_get(headers, values, "证据")), "candidate_value": _text(_get(headers, values, "候选值")), "confidence": _maybe_float(_get(headers, values, "置信度")), "suggested_action": _text(_get(headers, values, "建议动作")), "manual_note": _text(_get(headers, values, "人工备注")), "source_row_no": row_no, "source_sheet": "06_REVIEW_QUEUE"}
    return {"products": products, "localizations": localizations, "price_history": prices, "events": events, "runs": runs, "reviews": reviews}


def _row_to_json(headers: list[str], values: list[Any]) -> dict[str, Any]:
    return _row_to_dict(headers, values)


def _compare_maps(entity: str, expected: dict[str, dict[str, Any]], actual: dict[str, dict[str, Any]], fields: tuple[str, ...]) -> dict[str, Any]:
    mismatches = []
    for identity in sorted(set(expected) | set(actual)):
        if identity not in expected or identity not in actual:
            mismatches.append({"entity": entity, "identity": identity, "field": "__record__", "source_value": "<missing>" if identity not in actual else "<extra>", "target_value": "<extra>" if identity not in expected else "<missing>"})
            continue
        for field in fields:
            left, right = expected[identity].get(field), actual[identity].get(field)
            if field in ("raw_json", "source_raw_json"):
                try: right = json.loads(right) if isinstance(right, str) else right
                except json.JSONDecodeError: pass
            if left != right:
                mismatches.append({"entity": entity, "identity": identity, "field": field, "source_value": left, "target_value": right})
                if len(mismatches) >= 20: break
        if len(mismatches) >= 20: break
    return {"status": "PASS" if not mismatches else "FAIL", "source_records": len(expected), "target_records": len(actual), "mismatch_count": len(mismatches), "mismatches": mismatches}


def _compare_lists(entity: str, expected: list[dict[str, Any]], actual: list[dict[str, Any]], fields: tuple[str, ...]) -> dict[str, Any]:
    key = lambda record: tuple(str(record.get(field, "")) for field in fields)
    left, right, mismatches = sorted(expected, key=key), sorted(actual, key=key), []
    for index in range(max(len(left), len(right))):
        if index >= len(left) or index >= len(right):
            mismatches.append({"entity": entity, "identity": f"index:{index}", "field": "__record__", "source_value": "<missing>", "target_value": "<extra>"})
            continue
        for field in fields:
            source_value, target_value = left[index].get(field), right[index].get(field)
            if field in ("raw_json", "source_raw_json") and isinstance(target_value, str):
                try: target_value = json.loads(target_value)
                except json.JSONDecodeError: pass
            if source_value != target_value:
                mismatches.append({"entity": entity, "identity": "|".join(str(left[index].get(f, "")) for f in fields[:3]), "field": field, "source_value": source_value, "target_value": target_value})
                if len(mismatches) >= 20: return {"status": "FAIL", "source_records": len(left), "target_records": len(right), "mismatch_count": len(mismatches), "mismatches": mismatches}
    return {"status": "PASS" if not mismatches else "FAIL", "source_records": len(left), "target_records": len(right), "mismatch_count": len(mismatches), "mismatches": mismatches}


def _source_evidence_parity(rows, db, migration_id: str) -> dict[str, Any]:
    expected = {}
    for sheet, items in rows.items():
        for row_no, headers, values in items:
            raw_json = _canonical_raw_json(_row_to_json(headers, values))
            expected[(sheet, row_no)] = (raw_json, _raw_hash(raw_json))
    actual = {(row[0], row[1]): (row[2], row[3]) for row in db.execute("SELECT source_sheet,source_row_no,raw_json,raw_hash FROM source_records WHERE migration_id=?", (migration_id,)).fetchall()}
    mismatches = []
    for key in sorted(set(expected) | set(actual)):
        if key not in expected or key not in actual:
            mismatches.append({"sheet": key[0], "row_no": key[1], "reason": "MISSING_SOURCE_RECORD" if key not in actual else "EXTRA_SOURCE_RECORD"})
            continue
        expected_json, expected_hash = expected[key]
        actual_json, actual_hash = actual[key]
        try:
            actual_obj = json.loads(actual_json)
            json_ok = actual_obj == json.loads(expected_json)
            calculated_hash = _raw_hash(actual_json)
        except (TypeError, json.JSONDecodeError):
            json_ok, calculated_hash = False, ""
        if actual_hash != expected_hash or calculated_hash != actual_hash or not json_ok:
            mismatches.append({"sheet": key[0], "row_no": key[1], "reason": "RAW_HASH_MISMATCH", "source_hash": expected_hash, "target_hash": actual_hash})
    by_sheet = {}
    for sheet in rows:
        source_n = sum(1 for key in expected if key[0] == sheet)
        target_n = sum(1 for key in actual if key[0] == sheet)
        by_sheet[sheet] = {"source_rows": source_n, "target_rows": target_n, "status": "PASS" if source_n == target_n and not any(item["sheet"] == sheet for item in mismatches) else "FAIL"}
    return {"status": "PASS" if not mismatches else "FAIL", "source_rows": len(expected), "target_rows": len(actual), "exact_row_identity": not any(item["reason"] in ("MISSING_SOURCE_RECORD", "EXTRA_SOURCE_RECORD") for item in mismatches), "raw_hash_parity": not any(item["reason"] == "RAW_HASH_MISMATCH" for item in mismatches), "mismatch_count": len(mismatches), "mismatches": mismatches[:20], "by_sheet": by_sheet}


def validate_mirror(master_path: Path, db_path: Path) -> dict[str, Any]:
    rows, source, expected = _read_rows(master_path), None, None
    source, expected = _source_counts(rows), _expected(rows)
    result = {"master_path": str(master_path), "master_sha256": sha256_file(master_path), "db_path": str(db_path), "checks": {}, "source": {name: {k: v for k, v in values.items() if not isinstance(v, (set, dict))} for name, values in source.items()}}
    db = connect(db_path, read_only=True)
    try:
        metadata = dict(db.execute("SELECT key,value FROM schema_metadata").fetchall())
        product_fields = ("sku", "canonical_id", "name_es", "cat1_es", "cat2_es", "spec_es", "product_url", "current_price", "historical_min_price", "historical_max_price", "current_status_raw", "first_seen_at", "last_seen_at", "description_es", "details_es", "image_url", "source_sheet", "source_row_no", "source_raw_json")
        actual_products = {row["sku"]: dict(row) for row in db.execute("SELECT " + ",".join(product_fields) + " FROM products")}
        localization_fields = ("sku", "language", "name", "cat1", "cat2", "spec", "description", "details", "source", "source_hash", "review_status", "source_sheet", "source_row_no")
        actual_localizations = {f"{row['sku']}|{row['language']}": dict(row) for row in db.execute("SELECT " + ",".join(localization_fields) + " FROM product_localizations")}
        actual_prices = [dict(row) for row in db.execute("SELECT sku,canonical_id,observed_at,run_id,previous_price,new_price,original_price,unit_price_raw,change_type,promotion_raw,source_file,source_sheet,raw_json FROM price_history")]
        actual_events = [dict(row) for row in db.execute("SELECT sku,canonical_id,occurred_at,run_id,event_type,old_value,new_value,evidence,source_file,source_sheet FROM events")]
        run_fields = ("run_id", "run_date", "started_at", "finished_at", "git_commit", "sitemap_count", "listing_count", "current_count", "new_count", "reappeared_count", "missing_first_count", "missing_continued_count", "offline_count", "price_up_count", "price_down_count", "promo_start_count", "promo_end_count", "access_state", "observation_complete", "qa_status", "commit_status", "snapshot_path", "source_row_no", "source_raw_json")
        actual_runs = [{field: row[field] for field in run_fields} for row in db.execute("SELECT " + ",".join(run_fields) + " FROM runs")]
        review_fields = ("review_id", "sku", "review_date", "issue_type", "evidence", "candidate_value", "confidence", "suggested_action", "manual_note", "source_row_no", "source_sheet")
        actual_reviews = {row["review_id"]: dict(row) for row in db.execute("SELECT " + ",".join(review_fields) + " FROM reviews")}
        result["field_parity"] = {
            "products": _compare_maps("products", expected["products"], actual_products, product_fields),
            "localizations": _compare_maps("product_localizations", {f"{sku}|zh": value for sku, value in expected["localizations"].items()}, actual_localizations, localization_fields),
            "price_history": _compare_lists("price_history", expected["price_history"], actual_prices, ("sku", "canonical_id", "observed_at", "run_id", "previous_price", "new_price", "original_price", "unit_price_raw", "change_type", "promotion_raw", "source_file", "source_sheet", "raw_json")),
            "events": _compare_lists("events", expected["events"], actual_events, ("sku", "canonical_id", "occurred_at", "run_id", "event_type", "old_value", "new_value", "evidence", "source_file", "source_sheet")),
            "runs": _compare_lists("runs", expected["runs"], actual_runs, run_fields),
            "reviews": _compare_maps("reviews", expected["reviews"], actual_reviews, review_fields),
        }
        result["source_evidence_parity"] = _source_evidence_parity(rows, db, metadata.get("migration_id", ""))
        zh_current, es_current = source["01_SKU_ZH_CURRENT"]["sku_set"], source["02_SKU_ES_CURRENT"]["sku_set"]
        canonical_mismatches = [sku for sku, cid in source["02_SKU_ES_CURRENT"]["canonical_by_sku"].items() if cid and cid != source["08_LONG_TERM_MASTER"]["canonical_by_sku"].get(sku)]
        result["db_counts"] = {"products": len(actual_products), "localizations": len(actual_localizations), "observations": db.execute("SELECT COUNT(*) FROM observations").fetchone()[0], "price_history": len(actual_prices), "events": len(actual_events), "runs": len(actual_runs), "reviews": len(actual_reviews), "source_records": db.execute("SELECT COUNT(*) FROM source_records WHERE migration_id=?", (metadata.get("migration_id", ""),)).fetchone()[0], "source_issues": db.execute("SELECT COUNT(*) FROM migration_source_issues").fetchone()[0]}
        result["source_issue_counts"] = {row[0]: row[1] for row in db.execute("SELECT issue_code,COUNT(*) FROM migration_source_issues GROUP BY issue_code")}
        result["checks"].update({"schema_family": metadata.get("schema_family") == "ACTION_SQLITE_MIRROR", "schema_version": metadata.get("schema_version") == "1.0.0", "foreign_keys": db.execute("PRAGMA foreign_keys").fetchone()[0] == 1, "integrity_check": db.execute("PRAGMA integrity_check").fetchone()[0], "foreign_key_check": [dict(row) for row in db.execute("PRAGMA foreign_key_check")], "products_exact": set(actual_products) == source["08_LONG_TERM_MASTER"]["formal_sku_set"], "es_canonical_exact": not canonical_mismatches, "zh_es_current_equal": zh_current == es_current, "zh_db_current_equal": zh_current == {row[0] for row in db.execute("SELECT sku FROM v_db_current_skus")}, "es_db_current_equal": es_current == {row[0] for row in db.execute("SELECT sku FROM v_db_current_skus")}, "price_history_count_equal": len(actual_prices) == source["03_PRICE_HISTORY"]["formal_sku_rows"], "events_count_equal": len(actual_events) == source["04_EVENT_HISTORY"]["formal_sku_rows"], "runs_count_equal": len(actual_runs) == source["05_RUN_LOG"]["rows"], "reviews_count_equal": len(actual_reviews) == source["06_REVIEW_QUEUE"]["rows"], "master_hash_recorded": metadata.get("master_sha256") == result["master_sha256"]})
        result["checks"]["field_parity"] = all(item["status"] == "PASS" for item in result["field_parity"].values())
        result["checks"]["source_evidence_parity"] = result["source_evidence_parity"]["status"] == "PASS"
        result["status"] = "PASS" if all(value is True or (key == "integrity_check" and value == "ok") or (key == "foreign_key_check" and value == []) for key, value in result["checks"].items()) else "FAIL"
    finally:
        db.close()
    return result

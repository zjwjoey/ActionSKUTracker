"""QA Gate（规范 §40-§42、§62）。

STAGING 完成后执行。任一关键检查失败 -> FAIL，禁止更新 Master。
大规模异常（如昨日 5537 -> 今日 1800）必须 QUARANTINED，保留证据但不覆盖总表。
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import math
from typing import Any

from ..localization.hashes import canonical_json, value_hash


@dataclass
class QAReport:
    passed: bool
    state: str                       # PASS / PASS_PRESENCE_ONLY / FAIL / QUARANTINED / BLOCKED
    checks: dict[str, tuple[bool, str]] = field(default_factory=dict)
    counts: dict[str, Any] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    issues: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "state": self.state,
            "passed": self.passed,
            "checks": {k: {"ok": v[0], "message": v[1]} for k, v in self.checks.items()},
            "counts": self.counts,
            "reasons": self.reasons,
            "issues": self.issues,
        }


def _pct(diff: float, base: float) -> float:
    return (diff / base * 100.0) if base else 0.0


def run_qa(
    cfg: dict[str, Any],
    *,
    yesterday_total: int,
    today_total: int,
    sitemap_count: int,
    listing_count: int,
    new_count: int,
    missing_count: int,
    price_up: int,
    price_down: int,
    anomaly_count: int,
    products: list[dict],
    blocked: bool = False,
    observation_valid: bool = True,
    category_coverage: dict[str, bool] | None = None,
    access_state: str = "NORMAL",
    detail_access_state: str = "NORMAL",
    presence_mode: str = "FULL",
    price_anomalies: list[dict[str, Any]] | None = None,
) -> QAReport:
    q = cfg["qa"]
    checks: dict[str, tuple[bool, str]] = {}
    reasons: list[str] = []
    issues: list[dict[str, Any]] = []
    passed = True

    def issue(level: str, field_name: str, reason: str, source_value: Any,
              product: dict[str, Any] | None = None) -> None:
        sku = str((product or {}).get("sku") or (product or {}).get("official_sku") or "")
        if level == "FIELD_REVIEW" and not sku:
            # An unidentified row is already HARD_BLOCK, never a field-level
            # write permission addressed to an empty product identity.
            return
        # JSON1 rejects NaN/Infinity. Keep a readable audit display while the
        # binding still hashes the original input value, never the display.
        display_value = (str(source_value) if isinstance(source_value, float)
                         and not math.isfinite(source_value) else source_value)
        issues.append({"level": level,
                       "sku": sku,
                       "field_name": field_name, "reason": reason,
                       "source_value": display_value, "source_hash": value_hash(source_value)})

    sitemap_fallback = presence_mode == "SITEMAP_FALLBACK"

    # A successfully parsed sitemap is authoritative Presence evidence.  If a
    # later listing request is restricted, the already-frozen sitemap must not
    # be discarded.  The run is explicitly downgraded below rather than being
    # presented as a fully observed listing run.
    if blocked and not sitemap_fallback:
        checks["fetch_not_blocked"] = (False, "网站访问异常/BLOCKED")
        issue("HARD_BLOCK", "presence", "ACCESS_BLOCKED", access_state)
        passed = False
        reasons.append("网站访问被封锁")
    elif blocked:
        checks["fetch_not_blocked"] = (True, "listing 访问受限；已冻结有效 Sitemap Presence 证据")

    if access_state != "NORMAL":
        message = f"global access controller ended in {access_state}"
        if not sitemap_fallback:
            checks["access_state_complete"] = (False, message)
            issue("HARD_BLOCK", "presence", "ACCESS_STATE_INCOMPLETE", access_state)
            passed = False
            reasons.append(message)
        else:
            checks["access_state_complete"] = (True, f"{message}; 使用已冻结 Sitemap Presence 证据")
    else:
        checks["access_state_complete"] = (True, "global access controller NORMAL")

    # Detail is enrichment only.  Once complete Presence evidence has been
    # frozen, a later Detail restriction is reported but cannot invalidate the
    # daily SKU observation or advance/undo lifecycle state.
    checks["detail_access_non_authoritative"] = (
        True, f"detail enrichment ended in {detail_access_state}; presence gate is authoritative")

    if not observation_valid:
        failed = [name for name, valid in (category_coverage or {}).items() if not valid]
        message = "sitemap 无有效观测" + (f"; listing 未完整类目: {', '.join(failed)}" if failed else "")
        checks["observation_valid"] = (False, message)
        issue("HARD_BLOCK", "presence", "OBSERVATION_INVALID", observation_valid)
        passed = False
        reasons.append(message)

    # 1. 总量变化比例
    drop = _pct(max(0, yesterday_total - today_total), yesterday_total)
    rise = _pct(max(0, today_total - yesterday_total), yesterday_total)
    ok = rise <= q["max_active_increase_percent"] and drop <= q["max_active_drop_percent"]
    checks["total_change"] = (ok, f"昨日{yesterday_total} 今日{today_total} 降{drop:.1f}% 升{rise:.1f}%")
    if not ok:
        passed = False
        issue("HARD_BLOCK", "presence", "TOTAL_CHANGE_EXCEEDS_LIMIT", today_total)
        reasons.append(f"总量变化超阈值(降{drop:.1f}%>max {q['max_active_drop_percent']}% 或升{rise:.1f}%>{q['max_active_increase_percent']}%)")

    # 2/3. Sitemap / Listing 差异。Listing 已知不完整时，该差异不能用于
    # 否定完整 Sitemap 的 Presence 结果；状态会被降级为 PASS_PRESENCE_ONLY。
    gap = _pct(abs(sitemap_count - listing_count), max(sitemap_count, listing_count))
    if sitemap_fallback:
        checks["sitemap_listing_gap"] = (True, f"listing 不完整，gap {gap:.1f}% 不作为 Presence 否决条件")
    else:
        ok = gap <= q["max_sitemap_listing_gap_percent"]
        checks["sitemap_listing_gap"] = (ok, f"sitemap{sitemap_count} listing{listing_count} gap{gap:.1f}%")
        if not ok:
            passed = False
            issue("HARD_BLOCK", "presence", "SITEMAP_LISTING_GAP", gap)
            reasons.append(f"sitemap/listing 差异{gap:.1f}%>max {q['max_sitemap_listing_gap_percent']}%")

    # 4. 新增比例
    npct = _pct(new_count, max(today_total, 1))
    ok = npct <= q["max_new_sku_percent"]
    checks["new_sku"] = (ok, f"新增{new_count}({npct:.1f}%)")
    if not ok:
        passed = False
        issue("HARD_BLOCK", "presence", "NEW_SKU_RATIO", npct)
        reasons.append(f"新增占比{npct:.1f}%>max {q['max_new_sku_percent']}%")

    # 5. 消失比例
    mpct = _pct(missing_count, max(yesterday_total, 1))
    ok = mpct <= q["max_missing_percent"]
    checks["missing"] = (ok, f"缺失{missing_count}({mpct:.1f}%)")
    if not ok:
        passed = False
        issue("HARD_BLOCK", "presence", "MISSING_SKU_RATIO", mpct)
        reasons.append(f"缺失占比{mpct:.1f}%>max {q['max_missing_percent']}%")

    # 6. 价格异常数量
    ok = anomaly_count <= q["max_anomaly_count"]
    checks["anomaly_count"] = (ok, f"异常{anomaly_count}")
    if not ok:
        reasons.append(f"价格异常{anomaly_count}>max {q['max_anomaly_count']}")
        issue("WARNING", "current_price", "PRICE_ANOMALY_COUNT", anomaly_count)

    # 7/8. 重复 SKU / Canonical_ID
    seen_sku, seen_cid = {}, {}
    dup_sku = dup_cid = 0
    for p in products:
        sku = str(p.get("sku") or p.get("official_sku") or "")
        cid = str(p.get("canonical_id") or "")
        if not sku or not sku.isascii() or not sku.isdigit():
            passed = False
            issue("HARD_BLOCK", "sku", "PRODUCT_SKU_INVALID", sku, p)
        if not cid:
            passed = False
            issue("HARD_BLOCK", "canonical_id", "CANONICAL_ID_MISSING", cid, p)
        seen_sku[sku] = seen_sku.get(sku, 0) + 1
        seen_cid[cid] = seen_cid.get(cid, 0) + 1
    dup_sku = sum(1 for v in seen_sku.values() if v > 1)
    dup_cid = sum(1 for v in seen_cid.values() if v > 1)
    ok = dup_sku == 0 and dup_cid == 0
    checks["duplicates"] = (ok, f"重复SKU={dup_sku} 重复Canonical={dup_cid}")
    if not ok:
        passed = False
        reasons.append("存在重复 SKU/Canonical_ID")
        for p in products:
            sku = str(p.get("sku") or p.get("official_sku") or "")
            cid = str(p.get("canonical_id") or "")
            if seen_sku.get(sku, 0) > 1:
                issue("HARD_BLOCK", "sku", "DUPLICATE_SKU", sku, p)
            if seen_cid.get(cid, 0) > 1:
                issue("HARD_BLOCK", "canonical_id", "DUPLICATE_CANONICAL_ID", cid, p)
    checks["identity"] = (not any(i["level"] == "HARD_BLOCK" and i["field_name"] in {"sku", "canonical_id"} for i in issues),
                          "商品身份及唯一主键")

    # 9. Listing-backed records must have the authoritative lightweight
    # fields. Sitemap-only products are valid Presence evidence and may remain
    # explicitly pending until Detail retry/enrichment supplies those fields.
    has_provenance = any("listing_fields_source" in p for p in products)
    listing_products = ([p for p in products if p.get("listing_fields_source") == "LISTING_CURRENT_RUN"]
                        if has_provenance else products)
    pending_products = ([p for p in products if p.get("listing_fields_source") != "LISTING_CURRENT_RUN" and
                         (not p.get("product_url") or p.get("current_price") is None or
                          not (p.get("cat1_es") or p.get("cat2_es")))] if has_provenance else [])
    null_link = sum(1 for p in listing_products if not p.get("product_url"))
    null_price = sum(1 for p in listing_products if p.get("current_price") in (None, ""))
    null_cat = sum(1 for p in listing_products if not (p.get("cat1_es") or p.get("cat2_es")))
    ok = null_link == 0 and null_price == 0 and null_cat == 0
    checks["listing_field_completeness"] = (
        ok, f"listing空链接{null_link} 空价格{null_price} 空类目{null_cat}; 待补充{len(pending_products)}")
    if not ok:
        reasons.append("Listing 已观测商品存在关键字段缺失")
    for p in listing_products:
        if not p.get("product_url"):
            passed = False
            issue("HARD_BLOCK", "product_url", "PRODUCT_URL_MISSING", p.get("product_url"), p)
    for p in products:
        # A sitemap-only pending row also needs an explicit field review;
        # its complete Presence evidence does not manufacture missing facts.
        if p.get("current_price") in (None, ""):
            issue("FIELD_REVIEW", "current_price", "PRICE_SOURCE_MISSING", p.get("current_price"), p)
        elif ((p.get('fact_field_provenance') or {}).get('current_price') or {}).get('state') in {'HISTORY_RETAINED', 'SOURCE_MISSING'}:
            # A nonempty retained value is not observed price coverage for
            # this run and must not qualify the run as a healthy baseline.
            issue('FIELD_REVIEW', 'current_price', 'PRICE_NOT_OBSERVED_THIS_RUN', p.get('current_price'), p)
        if not (p.get("cat1_es") or p.get("cat2_es")):
            issue("FIELD_REVIEW", "cat1_es", "CATEGORY_SOURCE_MISSING", p.get("cat1_es"), p)

    # 10. 价格合法范围
    bad_price = 0
    price_issue_skus: set[str] = set()
    for p in products:
        value = p.get("current_price")
        if value in (None, ""):
            continue
        try:
            valid = not isinstance(value, bool) and math.isfinite(float(value)) and q["price_min"] <= float(value) <= q["price_max"]
        except (TypeError, ValueError, OverflowError):
            valid = False
        if not valid:
            bad_price += 1
            issue("FIELD_REVIEW", "current_price", "PRICE_INVALID_OR_OUT_OF_RANGE", value, p)
            price_issue_skus.add(str(p.get("sku") or p.get("official_sku") or ""))
    products_by_sku = {str(p.get("sku") or p.get("official_sku") or ""): p for p in products}
    for anomaly in price_anomalies or ():
        sku = str(anomaly.get("sku") or anomaly.get("SKU") or "")
        if sku and sku not in price_issue_skus:
            p = products_by_sku.get(sku) or {"sku": sku}
            value = p.get("current_price", anomaly.get("source_value", anomaly.get("evidence")))
            issue("FIELD_REVIEW", "current_price", "PRICE_ANOMALY", value, p)
            price_issue_skus.add(sku)
    checks["price_range"] = (bad_price == 0, f"超范围价格{bad_price}")
    if bad_price:
        reasons.append(f"超范围价格{bad_price}")

    # 11. 标签解析率（以产品原始标签为单位）
    tagged = sum(1 for p in products if p.get("raw_tags"))
    parsed = sum(1 for p in products if p.get("raw_tags") and _tags_parsed(p.get("raw_tags")))
    rate = (parsed / tagged * 100.0) if tagged else 100.0
    checks["tag_parse_rate"] = (rate >= 90, f"解析率{rate:.1f}%")
    if rate < 90:
        reasons.append(f"标签解析率{rate:.1f}%<90%")
        for p in products:
            if p.get("raw_tags") and not _tags_parsed(p["raw_tags"]):
                issue("WARNING", "raw_tags", "TAG_NOT_PARSED", p["raw_tags"], p)

    counts = {
        "yesterday_total": yesterday_total, "today_total": today_total,
        "sitemap_count": sitemap_count, "listing_count": listing_count,
        "new": new_count, "missing": missing_count, "price_up": price_up, "price_down": price_down,
        "anomaly_count": anomaly_count,
    }
    state = "PASS_PRESENCE_ONLY" if passed and sitemap_fallback else ("PASS" if passed else "FAIL")
    if blocked and not sitemap_fallback:
        state = "BLOCKED"
    if sitemap_fallback:
        reasons.append("Sitemap 完整；Listing 不完整，类目轻量字段仅按已观测记录更新")
    return QAReport(passed=passed, state=state, checks=checks, counts=counts, reasons=reasons, issues=issues)


def _tags_parsed(raw: str) -> bool:
    from ..products.badges import parse_badges
    b = parse_badges(raw)
    return b.promotion_active or b.action_new_badge or b.sustainable_badge or b.discount is not None


def trusted_field_scope(products: list[dict], qa: QAReport, *, run_id: str,
                        business_date: str) -> dict[str, Any]:
    """Bind independent positive facts without granting Presence authority.

    This is a field selection capability, not a replacement QA result. The
    collection marker and writer must still bind and verify its exact scope.
    """
    from collections import Counter
    from ..products.updater import FACT_FIELDS, verified_detail_url

    counts = Counter(str(p.get('sku') or '') for p in products)
    blocked = {str(i.get('sku') or '') for i in qa.issues
               if i.get('level') == 'HARD_BLOCK' and i.get('field_name') != 'presence'}
    reviewed = {(str(i.get('sku') or ''), str(i.get('field_name') or ''))
                for i in qa.issues if i.get('level') in {'FIELD_REVIEW', 'HARD_BLOCK'}}
    rows = []
    for record in sorted(products, key=lambda p: str(p.get('sku') or '')):
        sku = str(record.get('sku') or '')
        cid = str(record.get('canonical_id') or '')
        url = verified_detail_url(sku, record.get('product_url'))
        if (not sku.isdigit() or counts[sku] != 1 or sku in blocked or not url
                or cid != f'ACT{sku.zfill(7)}'
                or record.get('presence_source') not in {'BOTH', 'LISTING_ONLY', 'SITEMAP_ONLY', 'AUXILIARY_ONLY'}):
            continue
        fields = {}
        evidence = record.get('fact_field_provenance') or {}
        for name in FACT_FIELDS:
            source = evidence.get(name) or {}
            value = record.get(name)
            if (source.get('state') != 'CURRENT_VERIFIED'
                    or source.get('source') not in {'LISTING_CURRENT_RUN', 'DETAIL_CURRENT_RUN'}
                    or (sku, name) in reviewed):
                continue
            if value in (None, '') and name != 'original_price':
                continue
            if isinstance(value, float) and not math.isfinite(value):
                continue
            if name in {'current_price', 'original_price'} and value is not None:
                if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
                    continue
            fields[name] = {'value_hash': value_hash(value), 'source': source['source']}
        if 'current_price' not in fields:
            fields.pop('original_price', None)
            fields.pop('unit_price', None)
        if fields:
            rows.append({'sku': sku, 'canonical_id': cid, 'product_url': url, 'fields': fields})
    item = {'contract': 'TRUSTED_FIELD_COMMIT_V1', 'run_id': run_id,
            'business_date': business_date, 'presence_authority': False, 'rows': rows}
    return {**item, 'scope_hash': hashlib.sha256(canonical_json(item).encode('utf-8')).hexdigest()}


def validate_trusted_field_scope(scope: Any, *, run_id: str, business_date: str) -> bool:
    from ..products.updater import FACT_FIELDS, verified_detail_url

    if not isinstance(scope, dict) or set(scope) != {
            'contract', 'run_id', 'business_date', 'presence_authority', 'rows', 'scope_hash'}:
        return False
    if (scope['contract'] != 'TRUSTED_FIELD_COMMIT_V1' or scope['run_id'] != run_id
            or scope['business_date'] != business_date or scope['presence_authority'] is not False
            or not isinstance(scope['rows'], list) or not scope['rows']):
        return False
    item = {k: v for k, v in scope.items() if k != 'scope_hash'}
    if scope['scope_hash'] != hashlib.sha256(canonical_json(item).encode('utf-8')).hexdigest():
        return False
    seen = set()
    for row in scope['rows']:
        if not isinstance(row, dict) or set(row) != {'sku', 'canonical_id', 'product_url', 'fields'}:
            return False
        sku = row['sku']
        if (not isinstance(sku, str) or not sku.isdigit() or sku in seen
                or row['canonical_id'] != f'ACT{sku.zfill(7)}'
                or not verified_detail_url(sku, row['product_url'])
                or not isinstance(row['fields'], dict) or not row['fields']):
            return False
        seen.add(sku)
        for name, value in row['fields'].items():
            if (name not in FACT_FIELDS or not isinstance(value, dict)
                    or set(value) != {'value_hash', 'source'}
                    or value['source'] not in {'LISTING_CURRENT_RUN', 'DETAIL_CURRENT_RUN'}
                    or not isinstance(value['value_hash'], str) or len(value['value_hash']) != 64
                    or any(c not in '0123456789abcdef' for c in value['value_hash'])):
                return False
        if ('original_price' in row['fields'] or 'unit_price' in row['fields']) and 'current_price' not in row['fields']:
            return False
    return True


def _counts(loc: dict) -> dict:
    return {
        "yesterday_total": loc.get("yesterday_total"),
        "today_total": loc.get("today_total"),
        "sitemap_count": loc.get("sitemap_count"),
        "listing_count": loc.get("listing_count"),
    }

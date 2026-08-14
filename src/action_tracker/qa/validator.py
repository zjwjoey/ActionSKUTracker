"""QA Gate（规范 §40-§42、§62）。

STAGING 完成后执行。任一关键检查失败 -> FAIL，禁止更新 Master。
大规模异常（如昨日 5537 -> 今日 1800）必须 QUARANTINED，保留证据但不覆盖总表。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class QAReport:
    passed: bool
    state: str                       # PASS / PASS_PRESENCE_ONLY / FAIL / QUARANTINED / BLOCKED
    checks: dict[str, tuple[bool, str]] = field(default_factory=dict)
    counts: dict[str, Any] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "state": self.state,
            "passed": self.passed,
            "checks": {k: {"ok": v[0], "message": v[1]} for k, v in self.checks.items()},
            "counts": self.counts,
            "reasons": self.reasons,
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
) -> QAReport:
    q = cfg["qa"]
    checks: dict[str, tuple[bool, str]] = {}
    reasons: list[str] = []
    passed = True

    sitemap_fallback = presence_mode == "SITEMAP_FALLBACK"

    # A successfully parsed sitemap is authoritative Presence evidence.  If a
    # later listing request is restricted, the already-frozen sitemap must not
    # be discarded.  The run is explicitly downgraded below rather than being
    # presented as a fully observed listing run.
    if blocked and not sitemap_fallback:
        checks["fetch_not_blocked"] = (False, "网站访问异常/BLOCKED")
        return QAReport(passed=False, state="BLOCKED", checks=checks, counts=_counts(locals()), reasons=["网站访问被封锁"])
    if blocked:
        checks["fetch_not_blocked"] = (True, "listing 访问受限；已冻结有效 Sitemap Presence 证据")

    if access_state != "NORMAL":
        message = f"global access controller ended in {access_state}"
        if not sitemap_fallback:
            checks["access_state_complete"] = (False, message)
            return QAReport(passed=False, state="FAIL", checks=checks, counts=_counts(locals()), reasons=[message])
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
        return QAReport(passed=False, state="FAIL", checks=checks, counts=_counts(locals()), reasons=[message])

    # 1. 总量变化比例
    drop = _pct(max(0, yesterday_total - today_total), yesterday_total)
    rise = _pct(max(0, today_total - yesterday_total), yesterday_total)
    ok = rise <= q["max_active_increase_percent"] and drop <= q["max_active_drop_percent"]
    checks["total_change"] = (ok, f"昨日{yesterday_total} 今日{today_total} 降{drop:.1f}% 升{rise:.1f}%")
    if not ok:
        passed = False
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
            reasons.append(f"sitemap/listing 差异{gap:.1f}%>max {q['max_sitemap_listing_gap_percent']}%")

    # 4. 新增比例
    npct = _pct(new_count, max(today_total, 1))
    ok = npct <= q["max_new_sku_percent"]
    checks["new_sku"] = (ok, f"新增{new_count}({npct:.1f}%)")
    if not ok:
        passed = False
        reasons.append(f"新增占比{npct:.1f}%>max {q['max_new_sku_percent']}%")

    # 5. 消失比例
    mpct = _pct(missing_count, max(yesterday_total, 1))
    ok = mpct <= q["max_missing_percent"]
    checks["missing"] = (ok, f"缺失{missing_count}({mpct:.1f}%)")
    if not ok:
        passed = False
        reasons.append(f"缺失占比{mpct:.1f}%>max {q['max_missing_percent']}%")

    # 6. 价格异常数量
    ok = anomaly_count <= q["max_anomaly_count"]
    checks["anomaly_count"] = (ok, f"异常{anomaly_count}")
    if not ok:
        passed = False
        reasons.append(f"价格异常{anomaly_count}>max {q['max_anomaly_count']}")

    # 7/8. 重复 SKU / Canonical_ID
    seen_sku, seen_cid = {}, {}
    dup_sku = dup_cid = 0
    for p in products:
        sku = str(p.get("sku") or "")
        cid = str(p.get("canonical_id") or "")
        seen_sku[sku] = seen_sku.get(sku, 0) + 1
        seen_cid[cid] = seen_cid.get(cid, 0) + 1
    dup_sku = sum(1 for v in seen_sku.values() if v > 1)
    dup_cid = sum(1 for v in seen_cid.values() if v > 1)
    ok = dup_sku == 0 and dup_cid == 0
    checks["duplicates"] = (ok, f"重复SKU={dup_sku} 重复Canonical={dup_cid}")
    if not ok:
        passed = False
        reasons.append("存在重复 SKU/Canonical_ID")

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
    null_price = sum(1 for p in listing_products if p.get("current_price") is None)
    null_cat = sum(1 for p in listing_products if not (p.get("cat1_es") or p.get("cat2_es")))
    ok = null_link == 0 and null_price == 0 and null_cat == 0
    checks["listing_field_completeness"] = (
        ok, f"listing空链接{null_link} 空价格{null_price} 空类目{null_cat}; 待补充{len(pending_products)}")
    if not ok:
        passed = False
        reasons.append("Listing 已观测商品存在关键字段缺失")

    # 10. 价格合法范围
    bad_price = sum(1 for p in products if p.get("current_price") is not None and not (q["price_min"] <= p["current_price"] <= q["price_max"]))
    checks["price_range"] = (bad_price == 0, f"超范围价格{bad_price}")
    if bad_price:
        passed = False
        reasons.append(f"超范围价格{bad_price}")

    # 11. 标签解析率（以产品原始标签为单位）
    tagged = sum(1 for p in products if p.get("raw_tags"))
    parsed = sum(1 for p in products if p.get("raw_tags") and _tags_parsed(p.get("raw_tags")))
    rate = (parsed / tagged * 100.0) if tagged else 100.0
    checks["tag_parse_rate"] = (rate >= 90, f"解析率{rate:.1f}%")
    if rate < 90:
        passed = False
        reasons.append(f"标签解析率{rate:.1f}%<90%")

    counts = {
        "yesterday_total": yesterday_total, "today_total": today_total,
        "sitemap_count": sitemap_count, "listing_count": listing_count,
        "new": new_count, "missing": missing_count, "price_up": price_up, "price_down": price_down,
        "anomaly_count": anomaly_count,
    }
    state = "PASS_PRESENCE_ONLY" if passed and sitemap_fallback else ("PASS" if passed else "FAIL")
    if sitemap_fallback:
        reasons.append("Sitemap 完整；Listing 不完整，类目轻量字段仅按已观测记录更新")
    return QAReport(passed=passed, state=state, checks=checks, counts=counts, reasons=reasons)


def _tags_parsed(raw: str) -> bool:
    from ..products.badges import parse_badges
    b = parse_badges(raw)
    return b.promotion_active or b.action_new_badge or b.sustainable_badge or b.discount is not None


def _counts(loc: dict) -> dict:
    return {
        "yesterday_total": loc.get("yesterday_total"),
        "today_total": loc.get("today_total"),
        "sitemap_count": loc.get("sitemap_count"),
        "listing_count": loc.get("listing_count"),
    }

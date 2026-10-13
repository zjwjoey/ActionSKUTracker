"""Product Updater：决定哪些 SKU 进入更新，并抓取/合并。

只有变化 SKU 才进入（规范 §22）：
    NEW / REAPPEARED / PRICE_CHANGE_CANDIDATE / BADGE_CHANGE / CONTENT_CHANGE /
    MISSING_FIELD / MANUAL_REFRESH
轻量字段（价格/标签/品名/规格/图片）来自 listing；完整详情（描述/详情表）用详情页抓取。
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from ..products.badges import build_badge_state
from ..services.hashing import content_hash, price_hash
from ..services.price_evidence import merge_observed_prices, PRICE_FIELDS

log = logging.getLogger(__name__)


def _today() -> str:
    return date.today().isoformat()


def plan_updates(
    sku_statuses: dict[str, Any],
    baseline: dict[str, dict],
    today_light: dict[str, dict],
    detail_refresh_days: int = 7,
    nuevo_skus: set[str] | None = None,
    promo_skus: set[str] | None = None,
    unknown_detail_budget: int = 0,
) -> list[dict]:
    """返回需要更新的 SKU 计划列表。

    每项: {sku, canonical_id, reason, need_detail, light}

    nuevo_skus/promo_skus 是来自 /nuevo/ 与 /promocion-semanal/ 专属徽章页的
    SKU 集合——徽章检测的权威信号（sku 在该页 = 徽章开启）。BADGE_CHANGE 依据
    成员集合判定，不需要详情页确认（卡片标签不可靠）。
    """
    nuevo_skus = nuevo_skus or set()
    promo_skus = promo_skus or set()
    plans = []
    for sku, st in sku_statuses.items():
        base = baseline.get(sku)
        light = today_light.get(sku)
        status = st.status
        reason = None
        need_detail = False

        if status in ("NEW", "REAPPEARED"):
            reason = status
            need_detail = True
        elif status == "ACTIVE" and base:
            # 轻量比较
            old_price = base.get("current_price")
            new_price = (light or {}).get("current_price")
            if light and ((old_price is None) or (new_price is not None and abs(new_price - (old_price or 0)) > 1e-9)):
                reason = "PRICE_CHANGE_CANDIDATE"
            elif light.get("_price_observation_complete") is True and any(
                (base.get(k) or None) != (light.get(k) or None)
                for k in ("original_price", "discount") if k == "original_price" or k in base
            ):
                reason = "PRICE_METADATA_CHANGE"
            elif _badge_changed(base, sku in nuevo_skus, sku in promo_skus):
                # 成员集合权威，无需详情确认
                reason = "BADGE_CHANGE"
            elif light and (base.get("image_url") or "") != (light.get("image_url") or ""):
                reason = "IMAGE_CHANGE"
            # Lightweight changes must not suppress a required detail retry.
            if str(base.get("detail_status") or "").upper() in {"PENDING", "ACCESS_INTERRUPTED", "INCOMPLETE"}:
                reason = "DETAIL_PENDING"
                need_detail = True
            elif _missing_field(base) and (not base.get("detail_last_success_at") or _stale_detail(base, detail_refresh_days)):
                reason = "MISSING_FIELD"
                need_detail = True
            elif _stale_detail(base, detail_refresh_days):
                reason = "DETAIL_REFRESH"
                need_detail = True
        elif status == "ACTIVE" and not base:
            reason = "NEW"  # 防御：有 listing 但无 baseline
            need_detail = True

        if reason:
            plans.append({
                "sku": sku,
                "canonical_id": st.canonical_id,
                "reason": reason,
                "need_detail": need_detail,
                "light": light or {"product_url": (base or {}).get("product_url")},
            })
    # Bootstrap unregistered legacy freshness gradually. This is an evidence
    # probe, not a claim that their existing details are expired or missing.
    detail_planned = {p["sku"] for p in plans if p["need_detail"]}
    eligible = [sku for sku, st in sku_statuses.items()
                if st.status == "ACTIVE" and baseline.get(sku)
                and sku not in detail_planned
                and not baseline[sku].get("detail_last_success_at")
                and not _missing_field(baseline[sku])
                and ((today_light.get(sku) or {}).get("product_url") or baseline[sku].get("product_url"))]
    by_sku = {p["sku"]: p for p in plans}
    for sku in sorted(eligible)[:max(0, int(unknown_detail_budget))]:
        if sku in by_sku:
            by_sku[sku].update(reason="DETAIL_FRESHNESS_UNKNOWN", need_detail=True)
            by_sku[sku]["light"] = {"product_url": baseline[sku].get("product_url"), **(by_sku[sku].get("light") or {})}
        else:
            plans.append({"sku": sku, "canonical_id": sku_statuses[sku].canonical_id,
                          "reason": "DETAIL_FRESHNESS_UNKNOWN", "need_detail": True,
                          "light": today_light.get(sku) or {"product_url": baseline[sku].get("product_url")}})
    return plans


def _badge_changed(base: dict, in_nuevo: bool, in_promo: bool) -> bool:
    """页面成员集合派生出的徽章状态是否与基线不同。

    用 build_badge_state 合并后的状态对比，而非原始字符串：可持续等无专属页面的
    徽章保留基线，不会因无法每日检测而产生误报。
    """
    return (base.get("raw_tags") or "") != build_badge_state(base.get("raw_tags"), in_nuevo, in_promo)


def _missing_field(rec: dict) -> bool:
    details = str(rec.get("details_es") or "").strip()
    return (any(not str(rec.get(f) or "").strip() for f in ("desc_es", "details_es", "spec_es", "cat2_es"))
            or bool(re.fullmatch(r"Número del artículo\s*:\s*\d+", details)))


def _stale_detail(rec: dict, detail_refresh_days: int) -> bool:
    try:
        stamp = rec.get("detail_last_success_at")
        if not stamp:
            # Legacy freshness is unknown, not an expiry signal for a full crawl.
            return False
        if str(rec.get("detail_status") or "COMPLETE").upper() != "COMPLETE":
            return True
        last = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        if rec.get("detail_source_hash") and rec.get("source_hash") and rec["detail_source_hash"] != rec["source_hash"]:
            return True
        return (datetime.now(timezone.utc) - last).total_seconds() >= max(0, detail_refresh_days) * 86400
    except Exception:
        return True


def fetch_and_merge(
    browser,
    plans: list[dict],
    baseline: dict[str, dict],
    checkpoint_dir: Path,
    max_detail_retries: int = 5,
    nuevo_skus: set[str] | None = None,
    promo_skus: set[str] | None = None,
    access_controller=None,
    detail_evidence: list[dict] | None = None,
    detail_completed_skus: list[str] | None = None,
    evidence_context: dict | None = None,
) -> tuple[list[dict], dict[str, dict]]:
    """抓取需要详情的 SKU，合并轻量/详情字段，返回 (变化列表, 更新后记录)。

    checkpoint_dir 存详情抓取进度，重启自动跳过已抓取 SKU（复刻旧脚本断点续跑）。

    nuevo_skus/promo_skus 用于详情抓取失败时的 raw_tags 兜底：徽章状态改由
    专属徽章页成员集合派生（build_badge_state），不再依赖不可靠的卡片标签。
    """
    from .parser import fetch_product_detail

    nuevo_skus = nuevo_skus or set()
    promo_skus = promo_skus or set()
    changes: list[dict] = []
    updated: dict[str, dict] = {}
    ckpt_file = checkpoint_dir / "detail_fetch.jsonl"
    done = _read_ckpt(ckpt_file)
    detail_evidence = detail_evidence if detail_evidence is not None else []
    detail_completed_skus = detail_completed_skus if detail_completed_skus is not None else []

    for i, plan in enumerate(plans, 1):
        if access_controller and access_controller.state.value in ("PROBE", "BLOCKED"):
            detail_evidence.append({**(evidence_context or {}), "sku": plan["sku"], "url": (plan.get("light") or {}).get("product_url", ""),
                                    "stage": "PRODUCT_DETAIL", "error_type": "DETAIL_BLOCKED",
                                    "access_state_before": access_controller.state.value,
                                    "access_state_after": access_controller.state.value, "attempt": 0,
                                    "timestamp": datetime.now(timezone.utc).isoformat(),
                                    "skipped_count": len(plans) - i + 1})
            break
        sku = plan["sku"]
        base = baseline.get(sku) or {}
        rec = dict(base)
        rec.update({"sku": sku, "canonical_id": plan["canonical_id"], "last_seen": _today()})
        # Every plan handled here has authoritative Presence evidence for the
        # current run. A brand-new record has no prior status, but it must
        # still be written to the CURRENT sheets as CURRENT.
        rec["status"] = "CURRENT"

        has_detail = False
        if plan["need_detail"]:
            detail = _get_detail(browser, plan, sku, done, ckpt_file, max_detail_retries,
                                 access_controller, detail_evidence, evidence_context)
            if detail:
                has_detail = True
                detail_completed_skus.append(sku)
                merge_observed_prices(rec, detail)
                for k, v in detail.items():
                    if v is not None and v != "":
                        rec[k] = v
                _mark_ckpt(done, ckpt_file, sku, detail)
            else:
                rec["last_seen"] = _today()
                log.warning("  #%s 详情抓取失败，保留既有字段", sku)
        if plan.get("light"):
            light = plan["light"]
            detail_price_authoritative = has_detail and rec.get("_price_observation_complete") is True
            if not detail_price_authoritative:
                merge_observed_prices(rec, light)
            for k in ("current_price", "original_price", "unit_price", "discount", "raw_tags", "image_url", "spec_es", "name_es", "cat1_es", "product_url"):
                if k in PRICE_FIELDS and detail_price_authoritative:
                    continue
                v = light.get(k)
                if v is None:
                    continue
                if k == "raw_tags":
                    # 详情成功则详情 raw_tags 权威（含详情页才显示的徽章）；
                    # 否则 build_badge_state：徽章状态 = 徽章页成员集合 + 基线徽章
                    if not has_detail:
                        rec[k] = build_badge_state(rec.get("raw_tags"), sku in nuevo_skus, sku in promo_skus)
                elif v != "":
                    rec[k] = v

        changes.append({
            "sku": sku,
            "canonical_id": plan["canonical_id"],
            "reason": plan["reason"],
            "current_price": rec.get("current_price"),
            "raw_tags": rec.get("raw_tags") or "",
            "image_url": rec.get("image_url") or "",
            "content_hash": content_hash(rec),
        })
        updated[sku] = rec
        if i % 20 == 0:
            log.info("  updater 进度: %d/%d", i, len(plans))
        browser.sleep()
    return changes, updated


def _get_detail(browser, plan, sku, done, ckpt_file, max_detail_retries, access_controller=None,
                detail_evidence=None, evidence_context=None):
    cached = done.get(sku)
    if cached and (cached.get("detail") or {}).get("_detail_collected_at"):
        return cached.get("detail")
    from .parser import fetch_product_detail
    url = (plan.get("light") or {}).get("product_url") or ""
    if not url:
        return None
    state_before = access_controller.state.value if access_controller else "UNKNOWN"
    cooldown_probe_attempted = False
    try:
        detail = fetch_product_detail(browser, url, sku, max_retries=max_detail_retries)
        detail["_detail_collected_at"] = datetime.now(timezone.utc).isoformat()
        return detail
    except Exception as e:
        # A first 401/403/429/challenge moves the controller to COOLDOWN.
        # Let BrowserSession.before_navigation perform the configured wait and
        # exactly one cautious PROBE of the same SKU.  A second restriction
        # transitions PROBE -> BLOCKED and stops all remaining Detail work.
        if access_controller and access_controller.state.value == "COOLDOWN":
            cooldown_probe_attempted = True
            log.warning("  #%s 详情访问受限；冷却 %.0f 秒后单次探测", sku,
                        access_controller.cooldown_seconds)
            try:
                detail = fetch_product_detail(browser, url, sku, max_retries=max_detail_retries)
                detail["_detail_collected_at"] = datetime.now(timezone.utc).isoformat()
                return detail
            except Exception as probe_error:
                e = probe_error
        if detail_evidence is not None:
            events = access_controller.events if access_controller else []
            last_event = events[-1] if events else ""
            detail_evidence.append({**(evidence_context or {}), "sku": sku, "url": url, "stage": "PRODUCT_DETAIL",
                                    "error_type": ("HTTP_429" if last_event == "RATE_LIMITED" else
                                                   "HTTP_403_OR_CHALLENGE" if last_event in {"CHALLENGE_OR_403", "PROBE_BLOCKED"} else
                                                   "DETAIL_INCOMPLETE"),
                                    "exception_type": type(e).__name__, "http_status": 429 if last_event == "RATE_LIMITED" else None,
                                    "challenge_detected": last_event in {"CHALLENGE_OR_403", "PROBE_BLOCKED"},
                                    "access_state_before": state_before,
                                    "access_state_after": access_controller.state.value if access_controller else "UNKNOWN",
                                    "cooldown_probe_attempted": cooldown_probe_attempted,
                                    "cooldown_seconds": access_controller.cooldown_seconds if cooldown_probe_attempted else 0,
                                    "attempt": max_detail_retries, "timestamp": datetime.now(timezone.utc).isoformat()})
        log.warning("  #%s 详情失败: %s", sku, str(e)[:120])
        return None


def _read_ckpt(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            o = json.loads(line)
            out[o["sku"]] = o
        except Exception:
            continue
    return out


def _mark_ckpt(done: dict, path: Path, sku: str, detail: dict) -> None:
    done[sku] = {"sku": sku, "detail": detail}
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"sku": sku, "detail": detail}, ensure_ascii=False) + "\n")

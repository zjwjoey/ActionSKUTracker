"""Product Updater：决定哪些 SKU 进入更新，并抓取/合并。

只有变化 SKU 才进入（规范 §22）：
    NEW / REAPPEARED / PRICE_CHANGE_CANDIDATE / BADGE_CHANGE / CONTENT_CHANGE /
    MISSING_FIELD / MANUAL_REFRESH
轻量字段（价格/标签/品名/规格/图片）来自 listing；完整详情（描述/详情表）用详情页抓取。
"""
from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from ..products.badges import build_badge_state
from ..services.hashing import content_hash, price_hash

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
        elif status == "ACTIVE" and base and light:
            # 轻量比较
            old_price = base.get("current_price")
            new_price = light.get("current_price")
            if (old_price is None) or (new_price is not None and abs(new_price - (old_price or 0)) > 1e-9):
                reason = "PRICE_CHANGE_CANDIDATE"
            elif _badge_changed(base, sku in nuevo_skus, sku in promo_skus):
                # 成员集合权威，无需详情确认
                reason = "BADGE_CHANGE"
            elif (base.get("image_url") or "") != (light.get("image_url") or ""):
                reason = "IMAGE_CHANGE"
            elif _missing_field(base):
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
                "light": light,
            })
    return plans


def _badge_changed(base: dict, in_nuevo: bool, in_promo: bool) -> bool:
    """页面成员集合派生出的徽章状态是否与基线不同。

    用 build_badge_state 合并后的状态对比，而非原始字符串：可持续等无专属页面的
    徽章保留基线，不会因无法每日检测而产生误报。
    """
    return (base.get("raw_tags") or "") != build_badge_state(base.get("raw_tags"), in_nuevo, in_promo)


def _missing_field(rec: dict) -> bool:
    return not (rec.get("desc_es") or rec.get("details_es") or rec.get("spec_es"))


def _stale_detail(rec: dict, detail_refresh_days: int) -> bool:
    try:
        from ..services.normalization import parse_date
        ls = parse_date(rec.get("last_seen"))
        if not ls:
            return False
        return (date.today() - ls).days >= detail_refresh_days
    except Exception:
        return False


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
        if rec.get("status") not in (None, "MISSING_FIRST", "MISSING_CONTINUED"):
            rec["status"] = "CURRENT"

        has_detail = False
        if plan["need_detail"]:
            detail = _get_detail(browser, plan, sku, done, ckpt_file, max_detail_retries,
                                 access_controller, detail_evidence, evidence_context)
            if detail:
                has_detail = True
                detail_completed_skus.append(sku)
                for k, v in detail.items():
                    if v is not None and v != "":
                        rec[k] = v
                _mark_ckpt(done, ckpt_file, sku, detail)
            else:
                rec["last_seen"] = _today()
                log.warning("  #%s 详情抓取失败，保留既有字段", sku)
        if plan.get("light"):
            light = plan["light"]
            for k in ("current_price", "original_price", "unit_price", "discount", "raw_tags", "image_url", "spec_es", "name_es", "cat1_es", "product_url"):
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
    if cached:
        return cached.get("detail")
    from .parser import fetch_product_detail
    url = (plan.get("light") or {}).get("product_url") or ""
    if not url:
        return None
    state_before = access_controller.state.value if access_controller else "UNKNOWN"
    cooldown_probe_attempted = False
    try:
        return fetch_product_detail(browser, url, sku, max_retries=max_detail_retries)
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
                return fetch_product_detail(browser, url, sku, max_retries=max_detail_retries)
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

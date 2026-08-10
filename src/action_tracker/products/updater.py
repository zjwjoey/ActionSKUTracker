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
from datetime import date
from pathlib import Path
from typing import Any

from ..services.hashing import content_hash, price_hash

log = logging.getLogger(__name__)


def _today() -> str:
    return date.today().isoformat()


def plan_updates(
    sku_statuses: dict[str, Any],
    baseline: dict[str, dict],
    today_light: dict[str, dict],
    detail_refresh_days: int = 7,
) -> list[dict]:
    """返回需要更新的 SKU 计划列表。

    每项: {sku, canonical_id, reason, need_detail, light}
    """
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
            elif (base.get("raw_tags") or "") != (light.get("raw_tags") or ""):
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
) -> tuple[list[dict], dict[str, dict]]:
    """抓取需要详情的 SKU，合并轻量/详情字段，返回 (变化列表, 更新后记录)。

    checkpoint_dir 存详情抓取进度，重启自动跳过已抓取 SKU（复刻旧脚本断点续跑）。
    """
    from .parser import fetch_product_detail

    changes: list[dict] = []
    updated: dict[str, dict] = {}
    ckpt_file = checkpoint_dir / "detail_fetch.jsonl"
    done = _read_ckpt(ckpt_file)

    for i, plan in enumerate(plans, 1):
        sku = plan["sku"]
        base = baseline.get(sku) or {}
        rec = dict(base)
        rec.update({"sku": sku, "canonical_id": plan["canonical_id"], "last_seen": _today()})
        if rec.get("status") not in (None, "MISSING_FIRST", "MISSING_CONTINUED"):
            rec["status"] = "CURRENT"

        if plan["need_detail"]:
            detail = _get_detail(browser, plan, sku, done, ckpt_file, max_detail_retries)
            if detail:
                for k, v in detail.items():
                    if v is not None and v != "":
                        rec[k] = v
                _mark_ckpt(done, ckpt_file, sku, detail)
            else:
                rec["last_seen"] = _today()
                log.warning("  #%s 详情抓取失败，保留既有字段", sku)
        if plan.get("light"):
            light = plan["light"]
            for k in ("current_price", "original_price", "unit_price", "discount", "raw_tags", "image_url", "spec_es", "name_es"):
                if light.get(k) is not None and light.get(k) != "":
                    rec[k] = light[k]

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


def _get_detail(browser, plan, sku, done, ckpt_file, max_detail_retries):
    cached = done.get(sku)
    if cached:
        return cached.get("detail")
    from .parser import fetch_product_detail
    url = (plan.get("light") or {}).get("product_url") or ""
    if not url:
        return None
    try:
        return fetch_product_detail(browser, url, sku, max_retries=max_detail_retries)
    except Exception as e:
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

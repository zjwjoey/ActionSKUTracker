"""每日运行编排（规范 §55-§57）。

流程：
    sitemap + listing -> SKU Monitor -> Product Updater(可选详情) -> 变化事件
    -> 翻译 fallback -> QA Gate -> Snapshot + Staging -> 日报

dry-run 只做以上全部但【禁止修改 Master 与状态文件】。
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .. import state as st
from ..excel import reader as excel_reader
from ..monitor import listing as listing_mod
from ..monitor.sku_monitor import run_sku_monitor
from ..monitor.sitemap import fetch_sitemap
from ..products import updater as updater_mod
from ..qa.validator import run_qa
from ..services import change as change_mod
from ..services.browser import BrowserSession
from ..services.hashing import content_hash
from ..snapshot import write_snapshot, write_staging
from ..translation.service import apply_zh

log = logging.getLogger(__name__)

_LIGHT_FIELDS = ["current_price", "original_price", "unit_price", "discount", "raw_tags", "image_url", "spec_es", "name_es", "cat1_es"]


def _light_dict(lp) -> dict:
    d = asdict(lp) if hasattr(lp, "__dataclass_fields__") else dict(lp)
    return {k: d.get(k) for k in _LIGHT_FIELDS}


def run_daily(
    cfg: dict[str, Any],
    dry_run: bool = True,
    fetch_details: bool | None = None,
    max_categories: int | None = None,
    max_pages: int | None = None,
) -> dict:
    run_date = date.today().isoformat()
    run_id = f"{run_date}_{datetime.now().strftime('%H%M%S')}"
    do_detail = fetch_details if fetch_details is not None else cfg["run"]["dry_run_fetch_details"]

    paths: dict[str, Path] = cfg["paths"]
    snap_dir = paths["snapshots"] / run_date
    snap_dir.mkdir(parents=True, exist_ok=True)

    # ---- 基线与状态 ----
    master = paths["master"]
    baseline = excel_reader.load_current(master)
    known = st.load_known_skus(paths["state"])
    offline = st.load_offline_skus(paths["state"])
    trans = st.load_translation_state(paths["state"])
    log.info("baseline=%d known=%d offline=%d run_id=%s dry_run=%s", len(baseline), len(known), len(offline), run_id, dry_run)

    # ---- 采集 ----
    site = cfg["site"]
    categories = site["categories"]
    if max_categories:
        categories = dict(list(categories.items())[:max_categories])

    sitemap = None
    listing_map: dict[str, list] = {}
    today_light: dict[str, dict] = {}
    browser_blocked = False
    with BrowserSession(cfg["browser"], cfg["browser"].get("cookies_path")) as browser:
        # 1. 首页建立会话
        try:
            browser.goto(site["base_url"])
        except Exception as e:
            log.warning("首页访问异常: %s", e)
        # 2. sitemap
        try:
            sitemap = fetch_sitemap(browser, site["sitemap_url"])
            log.info("sitemap: %d 个商品 URL", len(sitemap.skus))
        except Exception as e:
            log.error("sitemap 失败: %s", e)
            browser_blocked = True
        # 3. listing 轻量扫描
        listing_map = listing_mod.scan_all_categories(browser, categories)
        for cat_items in listing_map.values():
            for lp in cat_items:
                today_light[str(lp.sku)] = _light_dict(lp)
        # 4. 详情抓取（只处理变化 SKU）
        # 需要等 SKU Monitor 出计划，但 browser 会话在此；先扫描完，稍后在同一会话内补详情。

    # ---- SKU Monitor ----
    if sitemap is None:
        raise RuntimeError("sitemap 获取失败，无法继续")
    statuses, today_set = run_sku_monitor(
        sitemap.skus,
        today_light,
        baseline,
        known,
        cfg["lifecycle"]["offline_confirmation_runs"],
    )
    log.info("SKU 状态统计: %s", {s: sum(1 for x in statuses.values() if x.status == s) for s in
                                   {"NEW", "ACTIVE", "REAPPEARED", "MISSING_FIRST", "MISSING_CONTINUED", "OFFLINE", "ABSENT"}})

    # ---- 计划更新 ----
    plans = updater_mod.plan_updates(statuses, baseline, today_light, cfg["run"]["detail_refresh_days"])
    log.info("需要更新的 SKU: %d (原因: %s)",
             len(plans), {r: sum(1 for p in plans if p["reason"] == r) for r in {p["reason"] for p in plans}})

    # ---- 合并今日记录 ----
    updated: dict[str, dict] = {}
    # 详情抓取放回浏览器会话内
    if do_detail:
        with BrowserSession(cfg["browser"], cfg["browser"].get("cookies_path")) as browser:
            _, updated = updater_mod.fetch_and_merge(
                browser, [p for p in plans if p["need_detail"]], baseline, snap_dir, cfg["lifecycle"]["max_detail_retries"])
    # 轻量合并（无论是否抓详情都执行）
    for plan in plans:
        sku = plan["sku"]
        base = baseline.get(sku) or {}
        rec = dict(base)
        rec.update({"sku": sku, "canonical_id": plan["canonical_id"], "last_seen": run_date, "status": "CURRENT"})
        if plan.get("light"):
            for k in _LIGHT_FIELDS:
                v = plan["light"].get(k)
                if v not in (None, ""):
                    rec[k] = v
        if sku in updated:
            rec.update({k: v for k, v in updated[sku].items() if v not in (None, "")})
        else:
            updated[sku] = rec

    # ---- 变化事件 ----
    price_events, badge_events, content_events, anomalies, review_rows = [], [], [], [], []
    for sku, rec in updated.items():
        outcome = change_mod.compute_changes(
            sku, rec.get("canonical_id"), baseline.get(sku), rec, run_date,
            cfg["qa"]["price_min"], cfg["qa"]["price_max"], run_id,
        )
        price_events += outcome.price_events
        badge_events += outcome.badge_events
        content_events += outcome.content_events
        anomalies += outcome.anomalies
        review_rows += outcome.review_rows

    # ---- 翻译 fallback ----
    translation_updates = []
    for sku, rec in updated.items():
        before_zh = rec.get("name_zh")
        rec = apply_zh(rec)
        updated[sku] = rec
        if rec.get("translation_status") in ("FALLBACK_ES", "OK"):
            pass
        if before_zh != rec.get("name_zh") or not before_zh:
            translation_updates.append({"sku": sku, "canonical_id": rec.get("canonical_id"),
                                        "translation_status": rec.get("translation_status"), "date": run_date})

    # ---- 今日全部记录（含 MISSING 保留）----
    today_records: dict[str, dict] = {}
    for sku, rec in baseline.items():
        today_records[sku] = dict(rec)
    for sku, rec in updated.items():
        today_records[sku] = rec
    for sku, stat in statuses.items():
        if stat.status in ("MISSING_FIRST", "MISSING_CONTINUED") and sku in today_records:
            today_records[sku]["status"] = stat.status
            today_records[sku]["missing_count"] = stat.missing_count
            today_records[sku]["last_seen"] = today_records[sku].get("last_seen") or run_date
        if stat.status == "OFFLINE":
            today_records[sku]["status"] = "MISSING"  # 当天仍保留，但已触发 OFFLINE 判定
            today_records[sku]["missing_count"] = stat.missing_count

    # ---- QA ----
    products_for_qa = list(today_records.values())
    counts = _counts(statuses, today_set, sitemap, listing_map, price_events, anomalies)
    qa = run_qa(
        cfg,
        yesterday_total=len(baseline),
        today_total=len(today_set),
        sitemap_count=len(sitemap.skus),
        listing_count=len(today_light),
        new_count=counts["new"],
        missing_count=counts["missing"],
        price_up=counts["price_up"],
        price_down=counts["price_down"],
        anomaly_count=len(anomalies),
        products=products_for_qa,
        blocked=browser_blocked,
    )
    log.info("QA: state=%s passed=%s", qa.state, qa.passed)

    # ---- Snapshot + Staging ----
    sku_delta_rows = [{
        "sku": s.sku, "canonical_id": s.canonical_id, "status": s.status,
        "source_flag": s.source_flag, "event": s.event or "", "missing_count": s.missing_count,
    } for s in statuses.values()]
    run_report = _run_report(cfg, run_id, run_date, dry_run, len(baseline), len(today_set), statuses,
                             price_events, badge_events, content_events, anomalies, qa, snap_dir)
    data = {
        "sitemap_raw_xml": sitemap.raw_xml,
        "sitemap_skus": sitemap.skus,
        "listing_raw": {cat: [asdict(lp) for lp in items] for cat, items in listing_map.items()},
        "listing_products": [{"sku": k, **{f: v for f, v in v.items() if f in _LIGHT_FIELDS}} for k, v in today_light.items()],
        "products_normalized": [{"sku": k, **{f: v for f, v in v.items()}} for k, v in today_records.items()],
        "sku_delta": sku_delta_rows,
        "product_updates": [{"sku": p["sku"], "reason": p["reason"], "canonical_id": p["canonical_id"]} for p in plans],
        "translation_updates": translation_updates,
        "qa_report": qa.to_dict(),
        "run_report": run_report,
    }
    write_snapshot(cfg, run_date, data)
    stage_data = {
        "sku_changes": sku_delta_rows,
        "product_changes": [{"sku": p["sku"], "reason": p["reason"], "canonical_id": p["canonical_id"]} for p in plans],
        "price_changes": price_events,
        "translation_changes": translation_updates,
        "event_changes": badge_events + content_events,
    }
    if not dry_run:
        # 阶段一：正式写 Master 前需要人确认表结构方案；此处仅保留 staging 证据
        log.warning("正式写 Master 尚未启用（先跑通 dry-run 后定表结构）。")
    write_staging(cfg, run_id, stage_data)

    _print_report(run_report, qa)
    return {"run_id": run_id, "run_report": run_report, "qa": qa.to_dict(), "snapshot_dir": str(snap_dir)}


def _counts(statuses, today_set, sitemap, listing_map, price_events, anomalies) -> dict:
    st_map = {s.status: s for s in statuses.values()}
    return {
        "new": sum(1 for s in statuses.values() if s.status == "NEW"),
        "reappeared": sum(1 for s in statuses.values() if s.status == "REAPPEARED"),
        "missing": sum(1 for s in statuses.values() if s.status in ("MISSING_FIRST", "MISSING_CONTINUED")),
        "offline": sum(1 for s in statuses.values() if s.status == "OFFLINE"),
        "price_up": sum(1 for e in price_events if e.get("变化类型") == "UP"),
        "price_down": sum(1 for e in price_events if e.get("变化类型") == "DOWN"),
        "anomalies": len(anomalies),
    }


def _run_report(cfg, run_id, run_date, dry_run, yesterday, today, statuses,
                price_events, badge_events, content_events, anomalies, qa, snap_dir) -> dict:
    from .. import __version__
    return {
        "run_id": run_id,
        "run_date": run_date,
        "dry_run": dry_run,
        "version": __version__,
        "yesterday_current": yesterday,
        "today_sku": today,
        "new": sum(1 for s in statuses.values() if s.status == "NEW"),
        "reappeared": sum(1 for s in statuses.values() if s.status == "REAPPEARED"),
        "missing_first": sum(1 for s in statuses.values() if s.status == "MISSING_FIRST"),
        "missing_continued": sum(1 for s in statuses.values() if s.status == "MISSING_CONTINUED"),
        "offline": sum(1 for s in statuses.values() if s.status == "OFFLINE"),
        "price_up": sum(1 for e in price_events if e.get("变化类型") == "UP"),
        "price_down": sum(1 for e in price_events if e.get("变化类型") == "DOWN"),
        "promo_start": sum(1 for e in badge_events if e.get("事件类型") == "PROMO_START"),
        "promo_end": sum(1 for e in badge_events if e.get("事件类型") == "PROMO_END"),
        "new_badge_on": sum(1 for e in badge_events if e.get("事件类型") == "ACTION_NEW_BADGE_ON"),
        "new_badge_off": sum(1 for e in badge_events if e.get("事件类型") == "ACTION_NEW_BADGE_OFF"),
        "content_change": len(content_events),
        "anomalies": len(anomalies),
        "qa_state": qa.state,
        "master": str(cfg["paths"]["master"]),
        "snapshot": str(snap_dir),
    }


def _print_report(rep: dict, qa) -> None:
    lines = [
        f"Action Daily Monitor {rep['run_date']}",
        f"昨日 CURRENT: {rep['yesterday_current']}",
        f"今日官网 SKU: {rep['today_sku']}",
        f"ACTIVE: {rep['today_sku'] - rep['new'] - rep['reappeared']}",
        f"NEW: {rep['new']}",
        f"REAPPEARED: {rep['reappeared']}",
        f"MISSING_FIRST: {rep['missing_first']}",
        f"MISSING_CONTINUED: {rep['missing_continued']}",
        f"OFFLINE: {rep['offline']}",
        f"价格下降: {rep['price_down']}",
        f"价格上涨: {rep['price_up']}",
        f"开始促销: {rep['promo_start']}",
        f"结束促销: {rep['promo_end']}",
        f"官网 Nuevo 开启: {rep['new_badge_on']}",
        f"官网 Nuevo 结束: {rep['new_badge_off']}",
        f"内容变化: {rep['content_change']}",
        f"异常: {rep['anomalies']}",
        f"QA: {rep['qa_state']}",
        f"Master: {rep['master']}",
        f"Snapshot: {rep['snapshot']}",
    ]
    print("=" * 56)
    print("\n".join(lines))
    print("=" * 56)
    for k, (ok, msg) in qa.checks.items():
        print(f"  QA[{k}]: {'OK ' if ok else 'X  '} {msg}")

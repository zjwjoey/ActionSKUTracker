"""每日运行编排（规范 §55-§57）。

流程：
    sitemap + listing -> SKU Monitor -> Product Updater(可选详情) -> 变化事件
    -> 翻译 fallback -> QA Gate -> Snapshot + Staging -> 日报

dry-run 只做以上全部但【禁止修改 Master 与状态文件】。
"""
from __future__ import annotations

import json
import logging
import hashlib
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .. import state as st
from ..excel import reader as excel_reader
from ..excel.writer import RUN_LOG_HEADERS, write_master
from ..monitor import listing as listing_mod
from ..monitor.sku_monitor import run_sku_monitor
from ..monitor.sitemap import fetch_sitemap
from ..monitor.structure import discover_categories
from ..products import updater as updater_mod
from ..products.badges import build_badge_state
from ..qa.validator import run_qa
from ..services import change as change_mod
from ..services.browser import BrowserSession
from ..services.access import AccessController
from ..services.runtime import RunLock, madrid_now, observation_date
from ..services.gitutil import git_commit_info
from ..services.hashing import content_hash
from ..services.review import add_review_item
from ..snapshot import write_snapshot, write_staging
from ..translation.service import apply_zh

log = logging.getLogger(__name__)

_LIGHT_FIELDS = ["current_price", "original_price", "unit_price", "discount", "raw_tags", "image_url", "spec_es", "name_es", "cat1_es", "product_url"]


def _merge_light(rec: dict, light: dict, skip_raw_tags: bool = False,
                 in_nuevo: bool = False, in_promo: bool = False) -> None:
    """把 listing 轻量字段合并进今日记录。

    raw_tags 特殊处理：徽章状态 = 徽章页成员集合 + 基线徽章（build_badge_state），
    不再依赖不可靠的卡片标签。可持续/折扣等仅详情页显示的徽章保留基线，避免误报
    "徽章消失"。skip_raw_tags=True 时跳过 raw_tags（该 SKU 已抓详情，详情页标签
    或 fetch_and_merge 已按成员集合设定）。其余字段仅在非空时覆盖，避免 listing
    缺字段误清数据。
    """
    for k in _LIGHT_FIELDS:
        v = light.get(k)
        if v is None:
            continue
        if k == "raw_tags":
            if skip_raw_tags:
                continue
            rec[k] = build_badge_state(rec.get("raw_tags"), in_nuevo, in_promo)
        elif v != "":
            rec[k] = v


def _light_dict(lp) -> dict:
    d = asdict(lp) if hasattr(lp, "__dataclass_fields__") else dict(lp)
    return {k: d.get(k) for k in _LIGHT_FIELDS}


def _persist_fatal_run_evidence(cfg: dict[str, Any], context: dict, error: BaseException) -> None:
    """Best-effort evidence persistence which never masks the collection error."""
    finished = madrid_now().isoformat()
    report = {
        "run_id": context["run_id"], "run_date": context["run_date"],
        "dry_run": context["dry_run"], "started_at": context["started_at"],
        "finished_at": finished, "run_mode": "dry-run" if context["dry_run"] else "formal",
        "git_commit": git_commit_info(), "working_tree_dirty": git_commit_info().endswith("-dirty"),
        "access_state": "UNKNOWN", "observation_complete": False, "qa_state": "NOT_REACHED",
        "fatal_error": {"type": type(error).__name__, "message": str(error)},
        "cleanup_status": "lock_release_pending", "snapshot": str(context["snap_dir"]),
    }
    manifest = {"run_id": context["run_id"], "observation_date": context["run_date"],
                "started_at": context["started_at"], "finished_at": finished,
                "run_mode": report["run_mode"], "git_commit": report["git_commit"],
                "working_tree_dirty": report["working_tree_dirty"], "fatal_error": report["fatal_error"]}
    try:
        write_snapshot(cfg, context["run_date"], {"run_manifest": manifest, "run_report": report})
    except Exception:
        log.exception("secondary failure while persisting fatal run evidence")


def _finalized_run(fn):
    def wrapped(cfg: dict[str, Any], dry_run: bool = True, **kwargs):
        start_dt = madrid_now()
        run_date = observation_date()
        run_id = f"{run_date}_{start_dt.strftime('%H%M%S')}"
        paths: dict[str, Path] = cfg["paths"]
        lock = RunLock(paths["state"], stale_minutes=cfg["run"].get("lock_stale_minutes", 180))
        context = {"run_id": run_id, "run_date": run_date, "started_at": start_dt.isoformat(),
                   "dry_run": dry_run, "snap_dir": paths["snapshots"] / run_date / run_id}
        lock.acquire(run_id, command="daily-run --dry-run" if dry_run else "daily-run")
        context["snap_dir"].mkdir(parents=True, exist_ok=True)
        try:
            return fn(cfg, dry_run=dry_run, _run_context=context, **kwargs)
        except BaseException as error:
            _persist_fatal_run_evidence(cfg, context, error)
            raise
        finally:
            lock.release()
    return wrapped


@_finalized_run
def run_daily(
    cfg: dict[str, Any],
    dry_run: bool = True,
    fetch_details: bool | None = None,
    max_categories: int | None = None,
    max_pages: int | None = None,
    _run_context: dict | None = None,
) -> dict:
    if _run_context is None:  # Defensive: public calls always enter through the decorator.
        raise RuntimeError("run context is required")
    start_dt = datetime.fromisoformat(_run_context["started_at"])
    run_date = _run_context["run_date"]
    run_id = _run_context["run_id"]
    start_time = start_dt.strftime("%H:%M:%S")
    do_detail = fetch_details if fetch_details is not None else cfg["run"]["dry_run_fetch_details"]

    paths: dict[str, Path] = cfg["paths"]
    snap_dir = _run_context["snap_dir"]

    # ---- 基线与状态 ----
    master = paths["master"]
    baseline = excel_reader.load_current(master)
    known = st.load_known_skus(paths["state"])
    offline = st.load_offline_skus(paths["state"])
    trans = st.load_translation_state(paths["state"])
    log.info("baseline=%d known=%d offline=%d run_id=%s dry_run=%s", len(baseline), len(known), len(offline), run_id, dry_run)

    # ---- 采集 ----
    site = cfg["site"]
    configured_categories = site["categories"]
    special_categories = {k: v for k, v in configured_categories.items() if k in listing_mod.BADGE_ENTRY_KEYS}
    categories = {k: v for k, v in configured_categories.items() if k not in listing_mod.BADGE_ENTRY_KEYS}
    if max_categories:
        categories = dict(list(categories.items())[:max_categories])

    sitemap = None
    listing_map: dict[str, list] = {}
    today_light: dict[str, dict] = {}
    nuevo_skus: set[str] = set()
    promo_skus: set[str] = set()
    browser_blocked = False
    detail_evidence: list[dict] = []
    detail_completed_skus: list[str] = []
    site_structure = {"discovery_status": "NOT_STARTED", "fallback_used": False, "categories": []}
    access = AccessController(cooldown_seconds=cfg["browser"].get("cooldown_seconds", 60),
                              degraded_recovery_successes=cfg["browser"].get("degraded_recovery_successes", 3))
    # Keep the initial persistent context open so detail enrichment uses the same page.
    with BrowserSession(cfg["browser"], cfg["browser"].get("cookies_path"), access_controller=access,
                        keep_open=True) as browser:
        # 1. 首页建立会话
        try:
            browser.goto(site["base_url"])
            categories, site_structure = discover_categories(browser, categories)
        except Exception as e:
            log.warning("首页访问异常: %s", e)
            site_structure = {"discovery_status": "FAILED", "fallback_used": True, "categories": []}
        categories = {**categories, **special_categories}
        # 2. sitemap
        try:
            sitemap = fetch_sitemap(browser, site["sitemap_url"])
            log.info("sitemap: %d 个商品 URL", len(sitemap.skus))
        except Exception as e:
            log.error("sitemap 失败: %s", e)
            browser_blocked = True
        # 3. listing 轻量扫描（17 个入口：15 个类目 + Nuevo + Promoción semanal）
        listing_map, category_coverage = listing_mod.scan_all_categories(
            browser, categories, max_pages=max_pages, include_coverage=True)
        for cat, cat_items in listing_map.items():
            if cat == "nuevo":
                # 专属徽章页：sku 在该页 = Nuevo 徽章开启（徽章检测的权威信号）
                nuevo_skus = {str(lp.sku) for lp in cat_items}
                continue
            if cat == "promocion-semanal":
                promo_skus = {str(lp.sku) for lp in cat_items}
                continue
            # 徽章页不进 today_light：它们不是真实类目（cat1_es 不应写成 "Nuevo"），
            # 且卡片标签不可靠，徽章状态由上面的成员集合决定
            for lp in cat_items:
                today_light[str(lp.sku)] = _light_dict(lp)
        # 4. 详情抓取（只处理变化 SKU）
        # 需要等 SKU Monitor 出计划，但 browser 会话在此；先扫描完，稍后在同一会话内补详情。

    # ---- SKU Monitor ----
    sitemap_skus = sitemap.skus if sitemap is not None else []
    primary_coverage = {
        category: valid for category, valid in category_coverage.items()
        if category not in {listing_mod.CATEGORY_LABELS["nuevo"], listing_mod.CATEGORY_LABELS["promocion-semanal"]}
    }
    observation_complete = sitemap is not None and all(primary_coverage.values()) and access.state.value == "NORMAL"
    statuses, today_set = run_sku_monitor(
        sitemap_skus,
        today_light,
        baseline,
        known,
        cfg["lifecycle"]["offline_confirmation_runs"],
        sitemap_valid=sitemap is not None,
        category_coverage=primary_coverage,
        nuevo_skus=nuevo_skus,
        promo_skus=promo_skus,
    )
    log.info("SKU 状态统计: %s", {s: sum(1 for x in statuses.values() if x.status == s) for s in
                                   {"NEW", "ACTIVE", "REAPPEARED", "MISSING_FIRST", "MISSING_CONTINUED", "OFFLINE", "UNKNOWN", "ABSENT"}})

    # ---- 计划更新 ----
    plans = updater_mod.plan_updates(
        statuses, baseline, today_light, cfg["run"]["detail_refresh_days"],
        nuevo_skus=nuevo_skus, promo_skus=promo_skus)
    log.info("需要更新的 SKU: %d (原因: %s)",
             len(plans), {r: sum(1 for p in plans if p["reason"] == r) for r in {p["reason"] for p in plans}})

    # ---- 合并今日记录 ----
    updated: dict[str, dict] = {}
    # 详情抓取放回浏览器会话内
    if do_detail:
        try:
            _, updated = updater_mod.fetch_and_merge(
                browser, [p for p in plans if p["need_detail"]], baseline, snap_dir,
                cfg["lifecycle"]["max_detail_retries"], nuevo_skus=nuevo_skus, promo_skus=promo_skus,
                access_controller=access, detail_evidence=detail_evidence,
                detail_completed_skus=detail_completed_skus,
                evidence_context={"run_id": run_id, "observation_date": run_date, **browser.manifest()})
        finally:
            browser.close()
    else:
        browser.close()
    # 轻量合并（无论是否抓详情都执行）：light 字段必须落到最终保存的记录上，
    # 否则 fetch_and_merge 的产物（缺 cat1_es/product_url）会盖掉 light 的类目/链接。
    # 已抓详情的 SKU 跳过 raw_tags（详情页标签权威），只补 cat1_es/product_url 等。
    detail_handled = set(updated.keys())
    for plan in plans:
        sku = plan["sku"]
        rec = updated.get(sku)
        if rec is None:
            base = baseline.get(sku) or {}
            rec = dict(base)
            rec.update({"sku": sku, "canonical_id": plan["canonical_id"], "last_seen": run_date, "status": "CURRENT"})
        if plan.get("light"):
            _merge_light(rec, plan["light"], skip_raw_tags=(sku in detail_handled),
                         in_nuevo=(sku in nuevo_skus), in_promo=(sku in promo_skus))
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

    # ---- REVIEW_QUEUE：来源分歧（sitemap/listing 互相不一致、身份不明） ----
    for sku, s in statuses.items():
        if s.source_flag == "SITEMAP_ONLY":
            review_rows.append(add_review_item(
                run_date, sku, "SITEMAP_ONLY",
                evidence="sitemap 有、listing 无", suggested_action="核对 listing 是否漏扫或 sitemap 残留"))
        elif s.source_flag == "LISTING_ONLY":
            review_rows.append(add_review_item(
                run_date, sku, "LISTING_ONLY",
                evidence="sitemap 无、listing 有", suggested_action="确认是否为新品或列表漂移"))
        elif s.ever_seen and s.source_flag == "NONE" and s.missing_count == 0 and not s.was_yesterday:
            # 曾认识但今天/昨天均未出现且无缺失计数 → 身份不明确
            review_rows.append(add_review_item(
                run_date, sku, "UNKNOWN",
                evidence="曾认识，今天与昨天均未出现且无缺失计数",
                suggested_action="人工核对 SKU 身份/下架情况"))

    # ---- 生命周期事件（REAPPEARED 进 EVENT_HISTORY；NEW 的 FIRST_SEEN 已由 compute_changes 产生）----
    lifecycle_events = _build_lifecycle_events(statuses, run_date, run_id)
    reappeared_skus = {e["SKU"] for e in lifecycle_events}
    # 重现 SKU 的 before=None 会误判 FIRST_SEEN，剔除（首次建档不适用已下架后重现的商品）
    content_events = [e for e in content_events
                      if not (e.get("事件类型") == "FIRST_SEEN" and e.get("SKU") in reappeared_skus)]
    event_events = badge_events + content_events + lifecycle_events

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
            # 确认下架：移出 CURRENT（进 offline_skus；下次出现时 previous_status=OFFLINE → REAPPEARED）
            today_records.pop(sku, None)

    observation_complete = sitemap is not None and all(primary_coverage.values()) and access.state.value == "NORMAL"

    # ---- QA ----
    products_for_qa = list(today_records.values())
    counts = _counts(statuses, today_set, sitemap, listing_map, price_events, badge_events, content_events, anomalies)
    qa = run_qa(
        cfg,
        yesterday_total=len(baseline),
        today_total=len(today_set),
        sitemap_count=len(sitemap_skus),
        listing_count=len(today_light),
        new_count=counts["new"],
        missing_count=counts["missing"],
        price_up=counts["price_up"],
        price_down=counts["price_down"],
        anomaly_count=len(anomalies),
        products=products_for_qa,
        blocked=browser_blocked or access.blocked,
        observation_valid=observation_complete,
        category_coverage=primary_coverage,
        access_state=access.state.value,
    )
    log.info("QA: state=%s passed=%s", qa.state, qa.passed)

    # ---- Snapshot + Staging ----
    sku_delta_rows = [{
        "sku": s.sku, "canonical_id": s.canonical_id, "status": s.status,
        "source_flag": s.source_flag, "event": s.event or "", "missing_count": s.missing_count,
    } for s in statuses.values()]
    run_report = _run_report(cfg, run_id, run_date, dry_run, len(baseline), len(today_set), statuses,
                             price_events, badge_events, content_events, anomalies, qa, snap_dir,
                             observation_complete, primary_coverage,
                             detail_planned=len([p for p in plans if p["need_detail"]]),
                             detail_completed=len(detail_completed_skus), detail_evidence=detail_evidence,
                             access_state=access.state.value, access_report=access.report())
    data = {
        "sitemap_raw_xml": sitemap.raw_xml if sitemap is not None else "",
        "sitemap_skus": sitemap_skus,
        "listing_raw": {cat: [asdict(lp) for lp in items] for cat, items in listing_map.items()},
        "listing_products": [{"sku": k, **{f: v for f, v in v.items() if f in _LIGHT_FIELDS}} for k, v in today_light.items()],
        "products_normalized": [{"sku": k, **{f: v for f, v in v.items()}} for k, v in today_records.items()],
        "sku_delta": sku_delta_rows,
        "presence_evidence": [{"sku": s.sku, "source_flag": s.source_flag,
                                 "sitemap_present": s.sitemap_present, "listing_present": s.listing_present,
                                 "nuevo_present": s.nuevo_present, "promotion_present": s.promotion_present,
                                 "observation_valid": s.observation_valid} for s in statuses.values()],
        "coverage": primary_coverage,
        "site_structure": {**site_structure, "run_id": run_id, "observation_date": run_date,
                           "access_state": str(access.state), "access_events": access.events, **access.report()},
        "run_manifest": {
            "run_id": run_id, "observation_date": run_date, "started_at": start_dt.isoformat(),
            "git_commit": git_commit_info(), "working_tree_dirty": git_commit_info().endswith("-dirty"),
            "config_hash": hashlib.sha256((cfg["project_root"] / "config" / "settings.yaml").read_bytes()).hexdigest(),
            "access_state": access.state.value,
            **access.report(),
            **browser.manifest(),
        },
        "detail_evidence": detail_evidence,
        "product_updates": [{"sku": p["sku"], "reason": p["reason"], "canonical_id": p["canonical_id"],
                             "need_detail": p["need_detail"]} for p in plans],
        "translation_updates": translation_updates,
        "qa_report": qa.to_dict(),
        "run_report": run_report,
    }
    write_snapshot(cfg, run_date, data)
    stage_data = {
        "sku_changes": sku_delta_rows,
        "product_changes": [{"sku": p["sku"], "reason": p["reason"], "canonical_id": p["canonical_id"],
                            "need_detail": p["need_detail"]} for p in plans],
        "price_changes": price_events,
        "translation_changes": translation_updates,
        "event_changes": event_events,
        "presence_evidence": data["presence_evidence"],
        "lifecycle_changes": sku_delta_rows,
    }
    write_staging(cfg, run_id, stage_data)
    # ---- 正式提交（只发生在 非 dry-run 且 QA PASS；否则 Master/known_skus/offline_skus 一律不动）----
    commit_status = "DRY_RUN"
    if not dry_run:
        if _should_commit(dry_run=dry_run, qa_passed=qa.passed, access_state=access.state.value):
            run_log_row = _run_log_row(run_id, run_date, start_time, counts, qa, dry_run,
                                       sitemap_count=len(sitemap_skus), listing_count=len(today_light))
            commit_status = _commit_phase(
                cfg, statuses=statuses, known=known, run_date=run_date, run_id=run_id,
                offline_runs=cfg["lifecycle"]["offline_confirmation_runs"],
                today_records=today_records, price_events=price_events, event_events=event_events,
                run_log_row=run_log_row, review_rows=review_rows)
        else:
            commit_status = "QA_FAIL"
            log.error("QA 未通过（%s），禁止写 Master / known_skus / offline_skus", qa.state)

    run_report["commit_status"] = commit_status
    run_report["finished_at"] = madrid_now().isoformat()
    run_report["cleanup_status"] = "lock_release_pending"
    # Commit status and completion time are produced after the main snapshot.
    # Rewrite this small, atomic report independently of QA outcome.
    write_snapshot(cfg, run_date, {"run_report": run_report})
    _print_report(run_report, qa)
    return {"run_id": run_id, "run_report": run_report, "qa": qa.to_dict(),
            "commit_status": commit_status, "snapshot_dir": str(snap_dir)}


def _counts(statuses, today_set, sitemap, listing_map, price_events, badge_events, content_events, anomalies) -> dict:
    st_map = {s.status: s for s in statuses.values()}
    return {
        "active": sum(1 for s in statuses.values() if s.status == "ACTIVE"),
        "new": sum(1 for s in statuses.values() if s.status == "NEW"),
        "reappeared": sum(1 for s in statuses.values() if s.status == "REAPPEARED"),
        "missing": sum(1 for s in statuses.values() if s.status in ("MISSING_FIRST", "MISSING_CONTINUED")),
        "missing_first": sum(1 for s in statuses.values() if s.status == "MISSING_FIRST"),
        "missing_continued": sum(1 for s in statuses.values() if s.status == "MISSING_CONTINUED"),
        "offline": sum(1 for s in statuses.values() if s.status == "OFFLINE"),
        "unknown": sum(1 for s in statuses.values() if s.status == "UNKNOWN"),
        "price_up": sum(1 for e in price_events if e.get("变化类型") == "UP"),
        "price_down": sum(1 for e in price_events if e.get("变化类型") == "DOWN"),
        "promo_start": sum(1 for e in badge_events if e.get("事件类型") == "PROMO_START"),
        "promo_end": sum(1 for e in badge_events if e.get("事件类型") == "PROMO_END"),
        "new_badge_on": sum(1 for e in badge_events if e.get("事件类型") == "ACTION_NEW_BADGE_ON"),
        "new_badge_off": sum(1 for e in badge_events if e.get("事件类型") == "ACTION_NEW_BADGE_OFF"),
        "content_change": len(content_events),
        "anomalies": len(anomalies),
    }


def _run_log_row(run_id, run_date, start_time, counts: dict, qa, dry_run: bool,
                 sitemap_count: int, listing_count: int) -> dict:
    """构造 05_RUN_LOG 一行，key 与 RUN_LOG_HEADERS 一致（writer 按表头取值）。"""
    end_time = datetime.now().strftime("%H:%M:%S")
    return {
        "Run ID": run_id,
        "运行日期": run_date,
        "开始时间": start_time,
        "结束时间": end_time,
        "Git Commit": git_commit_info(),
        "Sitemap SKU数": sitemap_count,
        "Listing SKU数": listing_count,
        "ACTIVE": counts["active"],
        "NEW": counts["new"],
        "REAPPEARED": counts["reappeared"],
        "MISSING_FIRST": counts["missing_first"],
        "MISSING_CONTINUED": counts["missing_continued"],
        "OFFLINE": counts["offline"],
        "PRICE_UP": counts["price_up"],
        "PRICE_DOWN": counts["price_down"],
        "PROMO_START": counts["promo_start"],
        "PROMO_END": counts["promo_end"],
        "NEW_BADGE_ON": counts["new_badge_on"],
        "NEW_BADGE_OFF": counts["new_badge_off"],
        "CONTENT_CHANGE": counts["content_change"],
        "异常数量": counts["anomalies"],
        "QA状态": qa.state,
        "运行状态": "SUCCESS" if qa.passed else "QA_FAIL",
        "备注": "dry-run" if dry_run else "正式写库",
    }


def _run_report(cfg, run_id, run_date, dry_run, yesterday, today, statuses,
                price_events, badge_events, content_events, anomalies, qa, snap_dir,
                observation_complete: bool, category_coverage: dict[str, bool],
                detail_planned: int = 0, detail_completed: int = 0,
                detail_evidence: list[dict] | None = None, access_state: str = "NORMAL",
                access_report: dict | None = None) -> dict:
    from .. import __version__
    detail_evidence = detail_evidence or []
    blocked = next((x for x in detail_evidence if x.get("error_type") == "DETAIL_BLOCKED"), None)
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
        "unknown": sum(1 for s in statuses.values() if s.status == "UNKNOWN"),
        "observation_complete": observation_complete,
        "category_coverage": category_coverage,
        "detail_planned": detail_planned,
        "detail_completed": detail_completed,
        "detail_incomplete": len(detail_evidence),
        "detail_skipped_due_block": blocked.get("skipped_count", 0) if blocked else 0,
        "detail_blocked_at_sku": blocked.get("sku") if blocked else None,
        "blocked_stage": "PRODUCT_DETAIL" if blocked else None,
        "access_state": access_state,
        **(access_report or {}),
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


def _build_lifecycle_events(statuses: dict, run_date: str, run_id: str) -> list[dict]:
    """REAPPEARED 等生命周期事件（进 04_EVENT_HISTORY，key 与 EVENT_HISTORY_HEADERS 对齐）。

    NEW 的 FIRST_SEEN 由 compute_changes 产生；这里只补 REPEARED（MISSING/OFFLINE 后重现）。
    旧值取上一有效生命周期状态（MISSING/OFFLINE），新值 ACTIVE；记录缺失时默认 OFFLINE。
    """
    events = []
    for sku, s in statuses.items():
        if getattr(s, "event", None) == "REAPPEARED":
            old = getattr(s, "previous_status", "") or "OFFLINE"
            events.append({
                "Canonical_ID": s.canonical_id, "SKU": sku, "日期": run_date,
                "事件类型": "REAPPEARED", "旧值": old, "新值": "ACTIVE",
                "来源文件": "Action_Master.xlsx", "备注": run_id or "daily-run",
            })
    return events


def _should_commit(dry_run: bool, qa_passed: bool, access_state: str = "NORMAL") -> bool:
    """提交门禁：非 dry-run 且 QA PASS 才允许写 Master / known_skus / offline_skus。"""
    return (not dry_run) and qa_passed and access_state == "NORMAL"


def _commit_phase(
    cfg,
    *,
    statuses: dict,
    known: dict,
    run_date: str,
    run_id: str,
    offline_runs: int,
    today_records: dict,
    price_events: list[dict],
    event_events: list[dict],
    run_log_row: dict,
    review_rows: list[dict],
) -> str:
    """QA PASS 后的统一提交：known_skus + Master 先各自暂存验证，再原子替换，最后重生成 offline_skus。

    返回 FULL_COMMIT / PARTIAL_COMMIT / STATE_WRITE_FAILED。
    任一暂存失败则什么都不提交（正式文件保持原样）。
    """
    from .. import state as st
    from ..excel.writer import commit_master, stage_master

    state_dir = cfg["paths"]["state"]
    master = cfg["paths"]["master"]
    # 1) 计算新状态并暂存 known_skus（失败则不触碰 Master）
    try:
        transition = st.apply_state_transition(known, statuses, run_date, run_id, offline_runs)
        known_tmp, known_path = st.stage_known_skus(state_dir, transition["known"])
    except Exception as e:
        log.error("STATE_WRITE_FAILED: known_skus 计算/暂存失败 %s", e)
        return "STATE_WRITE_FAILED"
    # 2) 暂存 Master（内部完成备份 + 旧表迁移 + 验证）
    try:
        master_tmp = stage_master(
            cfg, updated_records=today_records, price_events=price_events,
            event_events=event_events, run_log_row=run_log_row, review_rows=review_rows)
    except Exception as e:
        known_tmp.unlink(missing_ok=True)
        log.error("STATE_WRITE_FAILED: Master 暂存失败 %s", e)
        return "STATE_WRITE_FAILED"
    # 3) 统一提交 Master + known_skus
    try:
        commit_master(master_tmp, master)
        st.commit_state_file(known_tmp, known_path)
    except Exception as e:
        log.error("PARTIAL_COMMIT: 提交中途失败 %s（Master 或 known_skus 可能已替换，见备份）", e)
        return "PARTIAL_COMMIT"
    # 4) 由 known_skus 重生成 offline_skus（原子写）
    try:
        st.save_offline_skus(state_dir, transition["offline"])
    except Exception as e:
        log.error("STATE_WRITE_FAILED: offline_skus 重生成失败 %s", e)
        return "STATE_WRITE_FAILED"
    log.info("正式提交完成: Master + known_skus + offline_skus（FULL_COMMIT）")
    return "FULL_COMMIT"


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
        f"提交状态: {rep.get('commit_status', '-')}",
        f"Master: {rep['master']}",
        f"Snapshot: {rep['snapshot']}",
    ]
    print("=" * 56)
    print("\n".join(lines))
    print("=" * 56)
    for k, (ok, msg) in qa.checks.items():
        print(f"  QA[{k}]: {'OK ' if ok else 'X  '} {msg}")

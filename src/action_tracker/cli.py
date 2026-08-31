"""命令行入口（规范 §54）。

    python -m action_tracker daily-run [--dry-run] [--no-dry-run]
    python -m action_tracker status
    python -m action_tracker export
    python -m action_tracker qa
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="action_tracker", description="Action 西班牙站商品每日监测程序")
    sub = p.add_subparsers(dest="command")

    d = sub.add_parser("daily-run", help="每日运行")
    d.add_argument("--dry-run", action="store_true", default=None, help="只出证据不写 Master")
    d.add_argument("--no-dry-run", dest="dry_run", action="store_false", help="允许正式写 Master")
    d.add_argument("--fetch-details", action="store_true", default=None)
    d.add_argument("--no-fetch-details", dest="fetch_details", action="store_false")
    d.add_argument("--max-categories", type=int, default=None)
    d.add_argument("--max-pages", type=int, default=None)

    r = sub.add_parser("detail-retry", help="仅重试已有 snapshot 中未完成的商品详情")
    r.add_argument("--run-id", required=True, help="父 dry-run 的 run_id")

    a = sub.add_parser("detail-apply", help="将完整且通过 QA 的详情重试结果写回 Master")
    a.add_argument("--run-id", required=True, help="已正式提交的父 observation run_id")

    h = sub.add_parser("detail-backfill", help="用已验证的历史详情证据填补 CURRENT 空字段")
    h.add_argument("--run-id", required=True, help="详情已完整的历史 source run_id")

    b = sub.add_parser("init-baseline", help="从 runtime Master 建立初始状态文件")
    b.add_argument("--force", action="store_true", help="重建状态文件")

    s = sub.add_parser("status", help="查看项目状态")
    q = sub.add_parser("qa", help="查看已落盘 QA（默认最新 snapshot run）")
    q.add_argument("--run-id", help="指定 snapshot run_id")
    e = sub.add_parser("export", help="导出已正式提交的商品清单")
    e.add_argument("--lang", choices=("es", "zh"), required=True, help="导出语言")
    image_group = e.add_mutually_exclusive_group(required=True)
    image_group.add_argument("--no-images", action="store_true", help="不嵌入本地图片")
    image_group.add_argument("--with-images", dest="with_images", action="store_true", help="读取本地 250x250 图片并嵌入")
    e.add_argument("--date", required=True, help="导出业务日期（YYYY-MM-DD）")
    e.add_argument("--run-id", help="可选：指定该日期已正式提交的 run_id")
    e.add_argument("--selection-id", help="可选：仅导出已保存 Selection 的 SKU")
    x = sub.add_parser("extract", help="统一商品提取（SQLite PRIMARY 只读）")
    x.add_argument("--query-json", help="查询 JSON 文件或 JSON 字符串")
    x.add_argument("--keyword")
    x.add_argument("--canonical-id"); x.add_argument("--canonical-id-list", dest="canonical_ids", action="append")
    x.add_argument("--sku", dest="skus", action="append")
    x.add_argument("--status", dest="statuses", action="append")
    x.add_argument("--cat1", action="append"); x.add_argument("--cat2", action="append")
    x.add_argument("--min-price", type=float); x.add_argument("--max-price", type=float)
    original_group = x.add_mutually_exclusive_group()
    original_group.add_argument("--has-original-price", action="store_true")
    original_group.add_argument("--no-original-price", dest="no_original_price", action="store_true")
    x.add_argument("--promotion", action="store_true"); x.add_argument("--new", dest="new_badge", action="store_true"); x.add_argument("--sustainable", action="store_true")
    x.add_argument("--price-change", choices=("UP", "DOWN", "FLAT")); x.add_argument("--min-change-amount", type=float); x.add_argument("--min-change-percent", type=float)
    x.add_argument("--historical-low-min", type=float); x.add_argument("--historical-low-max", type=float); x.add_argument("--historical-high-min", type=float); x.add_argument("--historical-high-max", type=float)
    x.add_argument("--event-type", dest="event_types", action="append"); x.add_argument("--date-from"); x.add_argument("--date-to"); x.add_argument("--last-n-days", type=int)
    x.add_argument("--first-seen-from"); x.add_argument("--first-seen-to"); x.add_argument("--last-seen-from"); x.add_argument("--last-seen-to"); x.add_argument("--event-from"); x.add_argument("--event-to"); x.add_argument("--event-last-n-days", type=int)
    x.add_argument("--image-status", dest="image_statuses", action="append"); x.add_argument("--has-image", action="store_true"); x.add_argument("--image-ready", dest="image_ready_for_export", action="store_true")
    x.add_argument("--localization-status"); x.add_argument("--missing-field", dest="missing_fields", action="append")
    x.add_argument("--sort", default="sku"); x.add_argument("--desc", action="store_true")
    x.add_argument("--limit", type=int, default=100); x.add_argument("--offset", type=int, default=0)
    x.add_argument("--json", action="store_true"); x.add_argument("--save-selection")
    v = sub.add_parser("saved-view", help="Saved View 管理")
    v_sub = v.add_subparsers(dest="saved_view_command", required=True)
    vc = v_sub.add_parser("create"); vc.add_argument("name"); vc.add_argument("--query-json", required=True); vc.add_argument("--description", default="")
    v_sub.add_parser("list")
    vr = v_sub.add_parser("run"); vr.add_argument("view_id"); vr.add_argument("--json", action="store_true")
    vu = v_sub.add_parser("update"); vu.add_argument("view_id"); vu.add_argument("--name"); vu.add_argument("--description"); vu.add_argument("--query-json")
    vd = v_sub.add_parser("delete"); vd.add_argument("view_id")
    sset = sub.add_parser("selection", help="Selection Set 管理")
    s_sub = sset.add_subparsers(dest="selection_command", required=True)
    sc = s_sub.add_parser("create"); sc.add_argument("name"); sc.add_argument("--query-json", required=True); sc.add_argument("--description", default=""); sc.add_argument("--view-id")
    s_sub.add_parser("list")
    sg = s_sub.add_parser("get"); sg.add_argument("selection_id")
    sz = s_sub.add_parser("zip"); sz.add_argument("selection_id"); sz.add_argument("--output", required=True)
    sx = s_sub.add_parser("csv"); sx.add_argument("selection_id"); sx.add_argument("--output", required=True)
    t = sub.add_parser("export-template1", help="导出 Template 1 三表版本")
    t.add_argument("--with-images", action="store_true", help="仅在今日中文清单嵌入本地 250x250 图片")
    t.add_argument("--date", required=True, help="导出业务日期（YYYY-MM-DD）")
    t.add_argument("--run-id", help="可选：指定该日期已正式提交的 run_id")
    t.add_argument("--selection-id", help="可选：仅导出已保存 Selection 的 SKU")
    he = sub.add_parser("export-history", help="导出历史 Presence 上下架矩阵")
    he.add_argument("--date", required=True, help="导出标记日期（YYYY-MM-DD）")
    ims = sub.add_parser("image-sync", help="同步正式 CURRENT 的本地图片资产")
    ims.add_argument("--date", required=True, help="业务日期（YYYY-MM-DD）")
    ims.add_argument("--run-id", help="可选：指定正式 run")
    sub.add_parser("image-status", help="查看本地图片资产状态")
    sub.add_parser("db-status", help="查看 SQLite V2 数据库状态")
    sub.add_parser("db-validate-production", help="验证 SQLite V2 完整性和外键")
    sub.add_parser("db-cutover-check", help="只读检查 SQLite Shadow 是否满足切换前置条件")
    dbm = sub.add_parser("db-migrate-baseline", help="从只读 Master/State 建立 SQLite V2 基线")
    dbm.add_argument("--date", required=True, help="基线日期（YYYY-MM-DD）")
    dbp = sub.add_parser("db-promote-primary", help="显式将已验证的 SQLite Shadow 数据库提升为 Primary")
    dbv = sub.add_parser("db-parity", help="对账 SQLite V2 与当前 Excel/CSV 兼容投影")
    dbe = sub.add_parser("sync-exports", help="重试 SQLite 提交对应的 Excel/CSV 兼容导出确认")
    dbe.add_argument("--commit-id", help="可选：只同步指定 commit_id")
    dbr = sub.add_parser("db-repair-localization-regression", help="修复经审计确认的 PRIMARY 本地化字段回退")
    dbr.add_argument("--run-id", required=True, help="受影响的正式 run_id")
    dbr.add_argument("--trusted-snapshot", required=True, help="用于字段级恢复的正式 products_normalized.csv")
    dc = sub.add_parser("dictionary-coverage", help="统计 CURRENT 的 AI-Free 字典覆盖率")
    dc.add_argument("--date", help="业务日期（YYYY-MM-DD）")
    dc.add_argument("--run-id", help="可选：指定正式 observation run_id")
    da = sub.add_parser("dictionary-apply", help="生成字典字段应用预览；--commit 受正式 Apply Gate 保护")
    da.add_argument("--run-id", required=True, help="必须是正式提交的 observation run_id")
    da.add_argument("--dry-run", action="store_true", help="仅生成 apply 预览；默认行为")
    da.add_argument("--commit", action="store_true", help="请求正式写入；生产配置关闭时明确拒绝")
    de = sub.add_parser("dictionary-enrich", help="对已正式提交 run 的新增/变更 SKU 做增量字典标准化")
    de.add_argument("--run-id", required=True, help="必须是 FULL_COMMIT 且 QA PASS 的 observation run_id")
    rq = sub.add_parser("review-queue", help="构建或处理统一人工审核队列")
    rq_sub = rq.add_subparsers(dest="review_queue_command", required=True)
    rq_build = rq_sub.add_parser("build", help="只读汇集 Master 与字典侧当前问题")
    rq_build.add_argument("--run-id", help="可选：纳入此 run 的增量字典审核证据")
    rq_decide = rq_sub.add_parser("decide", help="保存人工审核决定；批准字典项会写入对应字典")
    rq_decide.add_argument("--review-id", required=True)
    rq_decide.add_argument("--decision", choices=("APPROVED", "REJECTED", "RESOLVED"), required=True)
    rq_decide.add_argument("--value", default="", help="批准名称/品牌/术语时的人工确认值")
    rq_decide.add_argument("--term-type", default="", help="仅 TERM_CANDIDATE：人工确认的术语类型，可覆盖候选建议")
    tc = sub.add_parser("term-candidates", help="从正式 run 的增量 SKU 提取术语候选，绝不自动入库")
    tc.add_argument("--run-id", required=True, help="必须已有同 run 的 dictionary-enrich 正式证据")
    tc.add_argument("--min-sku-count", type=int, default=2, help="候选至少覆盖的 SKU 数，默认 2")
    pr = sub.add_parser("production-run", help="统一生产运行入口（运营编排层）")
    pr.add_argument("--date", help="业务日期；默认 Europe/Madrid 当日")
    pr.add_argument("--resume", action="store_true")
    pr.add_argument("--run-id", help="恢复时指定精确的 operations run ID")
    pr.add_argument("--from-step", choices=("PREFLIGHT", "BACKUP", "COLLECTION", "QA", "DB_COMMIT", "EXPORT", "IMAGE", "KNOWLEDGE", "AI", "AUTO_APPROVAL", "REVIEW", "REPORT"))
    pr.add_argument("--dry-run", action="store_true")
    pr.add_argument("--no-network", action="store_true")
    du = sub.add_parser("data-update", help="每日数据更新主链（production-run 兼容别名）")
    du.add_argument("--date"); du.add_argument("--resume", action="store_true"); du.add_argument("--run-id")
    du.add_argument("--dry-run", action="store_true"); du.add_argument("--no-network", action="store_true")
    ops = sub.add_parser("ops", help="本机运营状态/控制台")
    ops_sub = ops.add_subparsers(dest="ops_command", required=True)
    ops_sub.add_parser("status"); ops_sub.add_parser("health"); ops_sub.add_parser("runs"); ops_run = ops_sub.add_parser("run"); ops_run.add_argument("run_id")
    ops_serve = ops_sub.add_parser("serve"); ops_serve.add_argument("--host", default="127.0.0.1"); ops_serve.add_argument("--port", type=int, default=8787)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    from .config import ensure_runtime_dirs, load_settings
    from .log import setup_logging

    cfg = load_settings()
    ensure_runtime_dirs(cfg)
    setup_logging(cfg["paths"]["logs"])

    if args.command in (None, "status"):
        return _status(cfg)
    if args.command == "daily-run":
        dry_run = True if args.dry_run is None else args.dry_run
        from .database.integration import storage_mode
        if not dry_run and storage_mode(cfg) == "SQLITE_PRIMARY":
            print(json.dumps({"error": "FORMAL_RUN_REQUIRES_DATA_UPDATE"}, ensure_ascii=False), file=sys.stderr)
            return 2
        from .orchestrator.daily import run_daily
        res = run_daily(
            cfg,
            dry_run=dry_run,
            fetch_details=args.fetch_details,
            max_categories=args.max_categories,
            max_pages=args.max_pages,
        )
        print(json.dumps({"run_id": res["run_id"], "qa": res["qa"]["state"]}, ensure_ascii=False))
        return 0
    if args.command == "detail-retry":
        from .orchestrator.detail_retry import run_detail_retry
        res = run_detail_retry(cfg, args.run_id)
        print(json.dumps(res, ensure_ascii=False))
        return 0
    if args.command == "detail-apply":
        from .orchestrator.detail_retry import apply_detail_retry
        res = apply_detail_retry(cfg, args.run_id)
        print(json.dumps(res, ensure_ascii=False))
        return 0
    if args.command == "detail-backfill":
        from .orchestrator.detail_retry import backfill_missing_details
        res = backfill_missing_details(cfg, args.run_id)
        print(json.dumps(res, ensure_ascii=False))
        return 0
    if args.command == "init-baseline":
        from . import baseline
        try:
            res = baseline.build_baseline(cfg, force=args.force)
        except (RuntimeError, OSError, ValueError) as exc:
            print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
            return 2
        print(json.dumps(res, ensure_ascii=False))
        return 0
    if args.command == "qa":
        return _qa(cfg, run_id=args.run_id)
    if args.command == "export":
        from .exporting.service import ExportValidationError, export_catalog
        try:
            result = export_catalog(
                cfg, language=args.lang, export_date=args.date,
                no_images=not args.with_images, run_id=args.run_id, selection_id=args.selection_id,
            )
        except ExportValidationError as exc:
            print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
            return 2
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if args.command == "export-template1":
        from .exporting.template1_service import export_template1
        try:
            result = export_template1(
                cfg, export_date=args.date, run_id=args.run_id,
                with_images=bool(args.with_images), selection_id=args.selection_id,
            )
        except (ValueError, OSError) as exc:
            print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
            return 2
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if args.command == "export-history":
        from .exporting.history_export import HistoryExportError, export_history
        try:
            result = export_history(cfg, export_date=args.date)
        except (HistoryExportError, OSError, ValueError) as exc:
            print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
            return 2
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if args.command == "image-sync":
        from .images.service import sync_formal_current
        try:
            result = sync_formal_current(cfg, export_date=args.date, run_id=args.run_id)
        except (ValueError, OSError) as exc:
            print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
            return 2
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if args.command == "image-status":
        from .images.service import image_status
        print(json.dumps(image_status(cfg), ensure_ascii=False))
        return 0
    if args.command == "db-status":
        from .database.production import database_status
        db_path = Path((cfg.get("storage") or {}).get("db_path") or Path(cfg["project_root"]) / "runtime" / "db" / "action_tracker.db")
        if not db_path.is_absolute():
            db_path = Path(cfg["project_root"]) / db_path
        print(json.dumps(database_status(db_path), ensure_ascii=False))
        return 0
    if args.command == "db-validate-production":
        from .database.production import ProductionDatabaseError, validate_production_database
        db_path = Path((cfg.get("storage") or {}).get("db_path") or Path(cfg["project_root"]) / "runtime" / "db" / "action_tracker.db")
        if not db_path.is_absolute():
            db_path = Path(cfg["project_root"]) / db_path
        try:
            result = validate_production_database(db_path)
        except ProductionDatabaseError as exc:
            print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
            return 2
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if args.command == "db-cutover-check":
        from .database.integration import database_path
        from .database.production import ProductionDatabaseError, cutover_preflight
        try:
            if str((cfg.get("storage") or {}).get("mode") or "EXCEL_PRIMARY").upper() != "EXCEL_PRIMARY":
                raise ProductionDatabaseError("CUTOVER_CONFIG_MUST_REMAIN_EXCEL_PRIMARY")
            state_dir = Path(cfg["paths"]["state"])
            result = cutover_preflight(
                database_path(cfg), master=Path(cfg["paths"]["master"]),
                known=state_dir / "known_skus.csv", offline=state_dir / "offline_skus.csv",
            )
        except (ProductionDatabaseError, OSError, ValueError) as exc:
            print(json.dumps({"status": "FAIL", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
            return 2
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if args.command == "db-migrate-baseline":
        from .database.production import ProductionDatabaseError, import_legacy_baseline_v2
        db_path = Path((cfg.get("storage") or {}).get("db_path") or Path(cfg["project_root"]) / "runtime" / "db" / "action_tracker.db")
        if not db_path.is_absolute():
            db_path = Path(cfg["project_root"]) / db_path
        try:
            commit_id = import_legacy_baseline_v2(db_path, master_path=Path(cfg["paths"]["master"]), state_dir=Path(cfg["paths"]["state"]), observed_at=args.date)
        except (ProductionDatabaseError, ValueError, OSError) as exc:
            print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
            return 2
        print(json.dumps({"database": str(db_path), "commit_id": commit_id}, ensure_ascii=False))
        return 0
    if args.command == "sync-exports":
        from .database.integration import database_path, regenerate_pending_exports
        from .database.production import ProductionDatabaseError
        db_path = database_path(cfg)
        try:
            result = regenerate_pending_exports(cfg, commit_id=args.commit_id)
        except (ProductionDatabaseError, OSError) as exc:
            print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
            return 2
        print(json.dumps({"database": str(db_path), "results": result}, ensure_ascii=False))
        return 0
    if args.command == "db-repair-localization-regression":
        from .database.integration import database_path
        from .database.production import ProductionDatabaseError, repair_primary_localization_regression
        try:
            result = repair_primary_localization_regression(
                database_path(cfg), trusted_snapshot=Path(args.trusted_snapshot), run_id=args.run_id,
            )
        except (ProductionDatabaseError, OSError, ValueError) as exc:
            print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
            return 2
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if args.command == "db-promote-primary":
        from .database.integration import database_path
        from .database.production import ProductionDatabaseError, promote_database_role
        try:
            result = promote_database_role(database_path(cfg))
        except ProductionDatabaseError as exc:
            print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
            return 2
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if args.command == "db-parity":
        from .database.parity import compare_with_legacy_files
        from .database.repository import ProductionRepositoryError
        try:
            result = compare_with_legacy_files(cfg)
        except (ProductionRepositoryError, OSError, ValueError) as exc:
            print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
            return 2
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result.get("status") == "PASS" else 3
    if args.command == "dictionary-coverage":
        from .dictionary_coverage import dictionary_coverage
        try:
            result = dictionary_coverage(cfg, export_date=args.date, run_id=args.run_id)
        except (ValueError, OSError) as exc:
            print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
            return 2
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if args.command == "dictionary-apply":
        from .dictionary_apply import DictionaryApplyError, dictionary_apply
        try:
            if args.dry_run and args.commit:
                raise DictionaryApplyError("DICTIONARY_APPLY_MUTUALLY_EXCLUSIVE_FLAGS")
            result = dictionary_apply(cfg, run_id=args.run_id, dry_run=not args.commit)
        except (DictionaryApplyError, ValueError, OSError) as exc:
            print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
            return 2
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if args.command == "dictionary-enrich":
        from .dictionary_enrichment import DictionaryEnrichmentError, enrich_dictionary
        try:
            result = enrich_dictionary(cfg, run_id=args.run_id)
        except DictionaryEnrichmentError as exc:
            print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
            return 2
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if args.command == "review-queue":
        from .review_queue import ReviewQueueError, build_review_queue, decide_review
        try:
            if args.review_queue_command == "build":
                result = build_review_queue(cfg, run_id=args.run_id)
            else:
                result = decide_review(
                    cfg, review_id=args.review_id, decision=args.decision, value=args.value, term_type=args.term_type,
                )
        except ReviewQueueError as exc:
            print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
            return 2
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if args.command == "term-candidates":
        from .term_candidates import TermCandidateError, extract_term_candidates
        try:
            result = extract_term_candidates(cfg, run_id=args.run_id, min_sku_count=args.min_sku_count)
        except TermCandidateError as exc:
            print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
            return 2
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if args.command == "extract":
        from .database.integration import database_path
        from .extraction import ExtractionQuery, ExtractionService, SelectionService, SavedViewService
        import json as _json
        payload = {}
        if args.query_json:
            source = Path(args.query_json)
            payload = _json.loads(source.read_text(encoding="utf-8") if source.exists() else args.query_json)
        for key in ("keyword", "canonical_id", "min_price", "max_price", "sort", "limit", "offset", "price_change", "min_change_amount", "min_change_percent", "historical_low_min", "historical_low_max", "historical_high_min", "historical_high_max", "date_from", "date_to", "last_n_days", "first_seen_from", "first_seen_to", "last_seen_from", "last_seen_to", "event_from", "event_to", "event_last_n_days", "localization_status"):
            value = getattr(args, key, None)
            if value is not None: payload[key] = value
        for key in ("canonical_ids", "skus", "statuses", "cat1", "cat2", "event_types", "image_statuses", "missing_fields"):
            value = getattr(args, key, None)
            if value: payload[key] = tuple(value)
        if args.has_original_price: payload["has_original_price"] = True
        if args.no_original_price: payload["has_original_price"] = False
        if args.promotion: payload["promotion"] = True
        if args.new_badge: payload["new_badge"] = True
        if args.sustainable: payload["sustainable"] = True
        if args.has_image: payload["has_image"] = True
        if args.image_ready_for_export: payload["image_ready_for_export"] = True
        if args.desc: payload["descending"] = True
        result = ExtractionService(database_path(cfg)).execute(ExtractionQuery.from_dict(payload))
        if args.save_selection:
            saved = SelectionService(database_path(cfg)).create(args.save_selection, result.query)
            output = {"selection": saved}
        else: output = result.as_dict() if args.json else {"query_hash": result.query_hash, "matched_count": result.matched_count, "returned": len(result.items), "source_commit_id": result.source_commit_id}
        print(_json.dumps(output, ensure_ascii=False, default=str)); return 0
    if args.command == "saved-view":
        from .database.integration import database_path
        from .extraction import SavedViewService
        import json as _json
        svc = SavedViewService(database_path(cfg))
        if args.saved_view_command == "list": print(_json.dumps(svc.list(), ensure_ascii=False)); return 0
        if args.saved_view_command == "run":
            view = svc.get(args.view_id)
            if not view:
                print(_json.dumps({"error": "VIEW_NOT_FOUND"}, ensure_ascii=False), file=sys.stderr); return 2
            from .extraction import ExtractionService
            result = ExtractionService(database_path(cfg)).execute(view["query"])
            print(_json.dumps(result.as_dict() if args.json else {"view_id": args.view_id, "query_hash": result.query_hash, "matched_count": result.matched_count, "returned": len(result.items)}, ensure_ascii=False, default=str)); return 0
        if args.saved_view_command == "delete":
            svc.delete(args.view_id); print(_json.dumps({"status": "DELETED", "view_id": args.view_id}, ensure_ascii=False)); return 0
        if args.saved_view_command == "update":
            payload = None
            if args.query_json:
                source = Path(args.query_json)
                payload = _json.loads(source.read_text(encoding="utf-8") if source.exists() else args.query_json)
            result = svc.update(args.view_id, name=args.name, description=args.description, query=payload)
            print(_json.dumps(result, ensure_ascii=False)); return 0
        payload = _json.loads(Path(args.query_json).read_text(encoding="utf-8") if Path(args.query_json).exists() else args.query_json)
        print(_json.dumps(svc.create(args.name, payload, args.description), ensure_ascii=False)); return 0
    if args.command == "selection":
        from .database.integration import database_path
        from .extraction import SelectionService
        import json as _json
        svc = SelectionService(database_path(cfg))
        if args.selection_command == "list": print(_json.dumps(svc.list(), ensure_ascii=False)); return 0
        if args.selection_command == "get": print(_json.dumps(svc.get(args.selection_id), ensure_ascii=False)); return 0
        if args.selection_command == "zip":
            from .delivery import ArtifactService
            image_root = Path(cfg["paths"]["images"]) / "derivatives" / "excel_250"
            print(_json.dumps(ArtifactService(database_path(cfg)).build_image_zip(args.selection_id, image_root, Path(args.output)), ensure_ascii=False)); return 0
        if args.selection_command == "csv":
            from .delivery import ArtifactService
            print(_json.dumps(ArtifactService(database_path(cfg)).build_csv(args.selection_id, Path(args.output)), ensure_ascii=False)); return 0
        payload = _json.loads(Path(args.query_json).read_text(encoding="utf-8") if Path(args.query_json).exists() else args.query_json)
        print(_json.dumps(svc.create(args.name, payload, description=args.description, view_id=args.view_id), ensure_ascii=False)); return 0
    if args.command in ("production-run", "data-update"):
        from .operations.entry import run_production
        from .services.runtime import observation_date
        try:
            result = run_production(cfg, business_date=args.date or observation_date(), resume=args.resume, run_id=args.run_id, from_step=getattr(args, "from_step", None), dry_run=args.dry_run, no_network=args.no_network)
        except Exception as exc:
            print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr); return 30
        print(json.dumps(result, ensure_ascii=False)); return int(result.get("exit_code") or 0)
    if args.command == "ops":
        from .database.integration import database_path
        from .operations.service import OperationsService
        svc = OperationsService(database_path(cfg), reports_root=Path(cfg["paths"]["temp"]).parent / "reports" / "daily", lock_path=Path(cfg["paths"]["state"]) / "daily-run.lock", config=cfg)
        if args.ops_command == "status": print(json.dumps(svc.system_status(), ensure_ascii=False)); return 0
        if args.ops_command == "health": print(json.dumps(svc.health(), ensure_ascii=False)); return 0
        if args.ops_command == "runs": print(json.dumps(svc.run_history(), ensure_ascii=False)); return 0
        if args.ops_command == "run": print(json.dumps(svc.run_detail(args.run_id), ensure_ascii=False)); return 0
        from .operations.server import serve
        serve(svc, host=args.host, port=args.port); return 0
    return 0


def _status(cfg) -> int:
    from . import state as st
    from .database.connection import connect
    from .database.integration import database_path, storage_mode
    from .database.production import database_status, validate_production_database
    from .excel.reader import load_current

    master = cfg["paths"]["master"]
    cur = load_current(master) if master.exists() else {}
    known = st.load_known_skus(cfg["paths"]["state"])
    offline = st.load_offline_skus(cfg["paths"]["state"])
    mode = storage_mode(cfg)
    print("项目根: ", cfg["project_root"])
    print("Storage Mode: ", mode)
    if mode == "SQLITE_PRIMARY":
        db_path = database_path(cfg)
        status = database_status(db_path)
        validation = validate_production_database(db_path)
        with connect(db_path) as db:
            counts = {str(row[0]): int(row[1]) for row in db.execute("SELECT status,COUNT(*) FROM products GROUP BY status")}
            latest_run = db.execute("SELECT run_id,run_date,qa_state FROM runs ORDER BY started_at DESC LIMIT 1").fetchone()
        db_current = counts.get("CURRENT", 0)
        compatibility = "IN_SYNC" if (len(cur) == db_current and len(known) == status.get("lifecycle") and status.get("pending_export_sync", 0) == 0) else "OUT_OF_SYNC"
        print("Database Role: ", status.get("metadata", {}).get("database_role"))
        print("Database Path: ", db_path)
        print("SQLite HEAD Commit: ", (status.get("latest_commit") or {}).get("commit_id"))
        print("Latest Formal Run: ", dict(latest_run) if latest_run else None)
        for name in ("CURRENT", "MISSING", "OFFLINE", "HISTORICAL"):
            print(f"{name}: ", counts.get(name, 0))
        print("Integrity: ", validation["integrity"])
        print("Foreign Keys: ", validation["foreign_keys"])
        print("Pending Export Sync: ", status.get("pending_export_sync", 0))
        print("Compatibility Projection Status: ", compatibility)
        print("Master Current Count: ", len(cur))
        print("Known Current Count: ", sum(1 for item in known.values() if str(item.get("last_status") or item.get("current_status") or "").upper() in {"ACTIVE", "CURRENT"}))
        print("Offline Projection Count: ", len(offline))
    else:
        print("Master: ", master, "存在" if master.exists() else "缺失")
        print("CURRENT SKU: ", len(cur))
        print("known_skus: ", len(known))
        print("offline_skus: ", len(offline))
    print("git: ", _git_head())
    return 0


def _qa(cfg, *, run_id: str | None = None) -> int:
    root = cfg["paths"]["snapshots"]
    if not root.exists():
        print("没有可用 snapshot")
        return 1
    candidates = list(root.glob(f"*/{run_id}/qa_report.json")) if run_id else list(root.glob("*/*/qa_report.json"))
    if not candidates:
        print("没有可用 snapshot")
        return 1
    # Windows filesystems can assign identical/coarse mtimes to two snapshot
    # trees created in one test/run.  Date and run-id are the authoritative
    # ordering; mtime is only a final tie-breaker for legacy layouts.
    qf = max(candidates, key=lambda path: (
        path.parent.parent.name,
        path.parent.name,
        path.stat().st_mtime_ns,
    ))
    if not qf.exists():
        print(f"snapshot {qf.parent} 无 qa_report.json")
        return 1
    data = json.loads(qf.read_text(encoding="utf-8"))
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


def _git_head() -> str:
    try:
        import subprocess
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=Path(__file__).resolve().parent.parent.parent)
        return r.stdout.strip() or "未提交"
    except Exception:
        return "不可用"


def run():
    sys.exit(main())

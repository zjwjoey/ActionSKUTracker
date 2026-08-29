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
    q = sub.add_parser("qa", help="重跑 QA（基于最近 snapshot）")
    e = sub.add_parser("export", help="导出已正式提交的商品清单")
    e.add_argument("--lang", choices=("es", "zh"), required=True, help="导出语言")
    image_group = e.add_mutually_exclusive_group(required=True)
    image_group.add_argument("--no-images", action="store_true", help="不嵌入本地图片")
    image_group.add_argument("--with-images", dest="with_images", action="store_true", help="读取本地 250x250 图片并嵌入")
    e.add_argument("--date", required=True, help="导出业务日期（YYYY-MM-DD）")
    e.add_argument("--run-id", help="可选：指定该日期已正式提交的 run_id")
    t = sub.add_parser("export-template1", help="导出 Template 1 三表版本")
    t.add_argument("--date", required=True, help="导出业务日期（YYYY-MM-DD）")
    t.add_argument("--run-id", help="可选：指定该日期已正式提交的 run_id")
    he = sub.add_parser("export-history", help="导出历史 Presence 上下架矩阵")
    he.add_argument("--date", required=True, help="导出标记日期（YYYY-MM-DD）")
    ims = sub.add_parser("image-sync", help="同步正式 CURRENT 的本地图片资产")
    ims.add_argument("--date", required=True, help="业务日期（YYYY-MM-DD）")
    ims.add_argument("--run-id", help="可选：指定正式 run")
    sub.add_parser("image-status", help="查看本地图片资产状态")
    sub.add_parser("db-status", help="查看 SQLite V2 数据库状态")
    sub.add_parser("db-validate-production", help="验证 SQLite V2 完整性和外键")
    dbm = sub.add_parser("db-migrate-baseline", help="从只读 Master/State 建立 SQLite V2 基线")
    dbm.add_argument("--date", required=True, help="基线日期（YYYY-MM-DD）")
    dbp = sub.add_parser("db-promote-primary", help="显式将已验证的 SQLite Shadow 数据库提升为 Primary")
    dbv = sub.add_parser("db-parity", help="对账 SQLite V2 与当前 Excel/CSV 兼容投影")
    dbe = sub.add_parser("sync-exports", help="重试 SQLite 提交对应的 Excel/CSV 兼容导出确认")
    dbe.add_argument("--commit-id", help="可选：只同步指定 commit_id")
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
        res = baseline.build_baseline(cfg, force=args.force)
        print(json.dumps(res, ensure_ascii=False))
        return 0
    if args.command == "qa":
        return _qa(cfg)
    if args.command == "export":
        from .exporting.service import ExportValidationError, export_catalog
        try:
            result = export_catalog(
                cfg, language=args.lang, export_date=args.date,
                no_images=not args.with_images, run_id=args.run_id,
            )
        except ExportValidationError as exc:
            print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
            return 2
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if args.command == "export-template1":
        from .exporting.template1_service import export_template1
        try:
            result = export_template1(cfg, export_date=args.date, run_id=args.run_id)
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
        from .database.integration import database_path
        from .database.production import ProductionDatabaseError, sync_pending_exports
        db_path = database_path(cfg)
        state_dir = Path(cfg["paths"]["state"])
        try:
            result = sync_pending_exports(
                db_path,
                master=Path(cfg["paths"]["master"]),
                known=state_dir / "known_skus.csv",
                offline=state_dir / "offline_skus.csv",
                commit_id=args.commit_id,
            )
        except (ProductionDatabaseError, OSError) as exc:
            print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
            return 2
        print(json.dumps({"database": str(db_path), "results": result}, ensure_ascii=False))
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
    return 0


def _status(cfg) -> int:
    from . import state as st
    from .excel.reader import load_current

    master = cfg["paths"]["master"]
    cur = load_current(master) if master.exists() else {}
    known = st.load_known_skus(cfg["paths"]["state"])
    offline = st.load_offline_skus(cfg["paths"]["state"])
    print("项目根: ", cfg["project_root"])
    print("Master: ", master, "存在" if master.exists() else "缺失")
    print("CURRENT SKU: ", len(cur))
    print("known_skus: ", len(known))
    print("offline_skus: ", len(offline))
    print("git: ", _git_head())
    return 0


def _qa(cfg) -> int:
    snaps = sorted((cfg["paths"]["snapshots"] / "2026-01-01").parent.iterdir(), reverse=True) if cfg["paths"]["snapshots"].exists() else []
    if not snaps:
        print("没有可用 snapshot")
        return 1
    latest = snaps[0]
    qf = latest / "qa_report.json"
    if not qf.exists():
        print(f"最近 snapshot {latest} 无 qa_report.json")
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

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
    e.add_argument("--no-images", action="store_true", required=True, help="当前仅支持不嵌图的导出")
    e.add_argument("--date", required=True, help="导出业务日期（YYYY-MM-DD）")
    e.add_argument("--run-id", help="可选：指定该日期已正式提交的 run_id")
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
                no_images=args.no_images, run_id=args.run_id,
            )
        except ExportValidationError as exc:
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

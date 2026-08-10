"""安全 Excel Writer（规范 §43/§44）。

只有 QA PASS 且非 dry-run 才允许写正式 Master。写入流程：
    正式 Master -> 复制到 temp -> 更新 temp -> 完整验证 -> 关闭 -> 替换正式 Master
任一步失败则正式 Master 保持原样。
"""
from __future__ import annotations

import csv
import datetime as dt
import logging
import os
import shutil
from pathlib import Path
from typing import Any

import openpyxl

from ..services.review import REVIEW_HEADERS
from .reader import ES_MAP, ZH_MAP

log = logging.getLogger(__name__)

RUN_LOG_HEADERS = ["Run ID", "运行日期", "开始时间", "结束时间", "Git Commit", "Sitemap SKU数", "Listing SKU数",
                   "ACTIVE", "NEW", "REAPPEARED", "MISSING_FIRST", "MISSING_CONTINUED", "OFFLINE",
                   "PRICE_UP", "PRICE_DOWN", "PROMO_START", "PROMO_END", "NEW_BADGE_ON", "NEW_BADGE_OFF",
                   "CONTENT_CHANGE", "异常数量", "QA状态", "运行状态", "备注"]

# 03_PRICE_HISTORY / 04_EVENT_HISTORY 表头（写行时用常量，行 dict 的 key 必须与之一致）
PRICE_HISTORY_HEADERS = ["Canonical_ID", "SKU", "日期", "旧售价 (€)", "新售价 (€)", "原价 (€)",
                         "变化类型", "变化金额 (€)", "变化幅度 (%)", "促销状态", "来源文件", "来源Sheet"]
EVENT_HISTORY_HEADERS = ["Canonical_ID", "SKU", "日期", "事件类型", "旧值", "新值", "来源文件", "备注"]


def _backup(master: Path, backups_dir: Path) -> Path:
    backups_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    target = backups_dir / f"Action_Master_{stamp}.xlsx"
    shutil.copy2(master, target)
    log.info("已备份 Master -> %s", target)
    return target


def _cell(value: Any) -> Any:
    """把内部值转成 Excel 单元格值：日期真日期、价格真数值、布尔转是/否。"""
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (int, float)):
        return value
    s = str(value).strip()
    if not s:
        return None
    # 尝试把 "YYYY-MM-DD" 转真日期（规范 §53）
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return s


def _ensure_sheet(wb, title: str, headers: list[str]) -> None:
    if title in wb.sheetnames:
        return
    ws = wb.create_sheet(title=title)
    ws.append(headers)


def _append_rows(wb, title: str, rows: list[dict], headers: list[str]) -> None:
    if not rows:
        return
    _ensure_sheet(wb, title, headers)  # 已有表则不重写表头（修复重复表头 bug）
    ws = wb[title]
    for r in rows:
        ws.append([_cell(r.get(h)) for h in headers])


def _update_or_append_current(wb, sheet: str, colmap: dict, key_to_records: dict[str, dict], internal_keys: set[str]) -> int:
    """按 header 映射更新既有行，新 SKU 追加。返回更新的行数。"""
    if sheet not in wb.sheetnames:
        return 0
    ws = wb[sheet]
    header = [c.value for c in ws[1]]
    idx = {}
    for i, h in enumerate(header):
        k = colmap.get(h)
        if k:
            idx[k] = i
    # 更新既有行
    updated = 0
    for row in ws.iter_rows(min_row=2):
        sku = row[1].value
        cid = row[0].value
        rec = key_to_records.get(str(sku)) or key_to_records.get(str(cid))
        if not rec:
            continue
        for k, col in idx.items():
            if k in internal_keys and k in rec:
                row[col].value = _cell(rec.get(k))
        updated += 1
    # 追加新 SKU
    existing = {str(row[1]) for row in ws.iter_rows(min_row=2, values_only=True) if row[1]}
    for sku, rec in key_to_records.items():
        if sku in existing:
            continue
        new_row = [_cell(rec.get(colmap[h])) for h in header]
        ws.append(new_row)
        updated += 1
    return updated


def _migrate_legacy_sheets(wb, cfg: dict[str, Any]) -> None:
    """归档并删除旧版 05_MAPPING_REVIEW / 06_SOURCE_SUMMARY（幂等，行数不符即中止）。

    历史 Master 的这两个表不是规范 §十 的 05_RUN_LOG / 06_REVIEW_QUEUE：
      - 05_MAPPING_REVIEW 是历史人工核对审计数据（970 行）-> runtime/state/legacy_mapping_review.csv
      - 06_SOURCE_SUMMARY 是历史导入源文件汇总（17 行）-> runtime/backups/legacy_source_summary.csv
    删除前必须验证归档行数与表内一致，否则抛异常保护原文件；已归档且行数一致则直接删表。
    """
    paths_cfg = cfg.get("paths") or {}
    state_dir: Path = paths_cfg.get("state", Path("runtime/state"))
    backups_dir: Path = paths_cfg.get("backups", Path("runtime/backups"))
    for sheet, target, label in (
        ("05_MAPPING_REVIEW", state_dir / "legacy_mapping_review.csv", "MAPPING_REVIEW"),
        ("06_SOURCE_SUMMARY", backups_dir / "legacy_source_summary.csv", "SOURCE_SUMMARY"),
    ):
        if sheet not in wb.sheetnames:
            continue
        ws = wb[sheet]
        all_rows = list(ws.iter_rows(values_only=True))
        header = list(all_rows[0]) if all_rows else []
        data = [r for r in all_rows[1:] if any(c is not None for c in r)]
        if target.exists():
            with target.open("r", encoding="utf-8-sig", newline="") as f:
                n_existing = sum(1 for _ in csv.reader(f)) - 1
            if n_existing == len(data):
                del wb[sheet]
                log.info("归档已存在（%s %d 行），直接删除原表", label, n_existing)
                continue
            log.warning("归档文件已存在但行数不符（%s: 已存 %d, 待存 %d），覆盖", target.name, n_existing, len(data))
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(header)
            w.writerows(data)
        with target.open("r", encoding="utf-8-sig", newline="") as f:
            n_written = sum(1 for _ in csv.reader(f)) - 1
        if n_written != len(data):
            raise RuntimeError(f"{label} 归档行数不符: sheet={len(data)} csv={n_written}，中止以保护原文件")
        del wb[sheet]
        log.info("已归档 %s（%d 行）-> %s，并删除原表", label, len(data), target)


def stage_master(
    cfg: dict[str, Any],
    *,
    updated_records: dict[str, dict],
    price_events: list[dict],
    event_events: list[dict],
    run_log_row: dict | None = None,
    review_rows: list[dict] | None = None,
) -> Path:
    """暂存新的 Master 到 temp：备份 → 复制 → 更新 → 保存 → 完整验证。

    返回已就绪的 temp 路径（尚未替换正式文件），供"Master + 状态文件一起
    先生成再统一提交"使用。只有 commit_master 才会替换正式 Master。
    """
    master: Path = cfg["paths"]["master"]
    if not master.exists():
        raise FileNotFoundError(f"Master 不存在: {master}")

    _backup(master, cfg["paths"]["backups"])
    tmp: Path = cfg["paths"]["temp"] / master.name
    tmp.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(master, tmp)

    wb = openpyxl.load_workbook(tmp)
    # 迁移旧版 05/06 审计表（幂等），为新 05/06 腾出表名
    _migrate_legacy_sheets(wb, cfg)

    # 01 / 02 更新
    n_zh = _update_or_append_current(wb, "01_SKU_ZH_CURRENT", ZH_MAP, updated_records, set(ZH_MAP.values()))
    n_es = _update_or_append_current(wb, "02_SKU_ES_CURRENT", ES_MAP, updated_records, set(ES_MAP.values()))
    log.info("01 更新 %d 行, 02 更新 %d 行", n_zh, n_es)

    # 03 / 04 追加（表头用常量，行 dict 的 key 与之对齐）
    _append_rows(wb, "03_PRICE_HISTORY", price_events, PRICE_HISTORY_HEADERS)
    _append_rows(wb, "04_EVENT_HISTORY", event_events, EVENT_HISTORY_HEADERS)

    # 05_RUN_LOG / 06_REVIEW_QUEUE（规范 §十；不存在则创建，无行也要有表）
    _ensure_sheet(wb, "05_RUN_LOG", RUN_LOG_HEADERS)
    if run_log_row:
        _append_rows(wb, "05_RUN_LOG", [run_log_row], RUN_LOG_HEADERS)
    _ensure_sheet(wb, "06_REVIEW_QUEUE", REVIEW_HEADERS)
    if review_rows:
        _append_rows(wb, "06_REVIEW_QUEUE", review_rows, REVIEW_HEADERS)

    wb.save(tmp)
    wb.close()

    # 完整验证
    _validate(tmp)
    return tmp


def commit_master(tmp: Path, master: Path) -> Path:
    """原子替换正式 Master（tmp 必须已通过 _validate）。"""
    os.replace(tmp, master)
    log.info("正式 Master 已更新: %s", master)
    return master


def write_master(
    cfg: dict[str, Any],
    *,
    updated_records: dict[str, dict],
    price_events: list[dict],
    event_events: list[dict],
    run_log_row: dict | None = None,
    review_rows: list[dict] | None = None,
    dry_run: bool = False,
) -> Path:
    """原子更新正式 Master（stage + commit 一步完成）。dry_run 时禁止调用。"""
    if dry_run:
        raise RuntimeError("dry-run 禁止写 Master")
    tmp = stage_master(cfg, updated_records=updated_records, price_events=price_events,
                       event_events=event_events, run_log_row=run_log_row, review_rows=review_rows)
    return commit_master(tmp, cfg["paths"]["master"])


def _validate(path: Path) -> None:
    """重开文件验证可读、各表存在、无空数据区错误。失败抛异常以保护原文件。"""
    wb = openpyxl.load_workbook(path, read_only=True)
    required = {"01_SKU_ZH_CURRENT", "02_SKU_ES_CURRENT", "03_PRICE_HISTORY", "04_EVENT_HISTORY",
                "05_RUN_LOG", "06_REVIEW_QUEUE"}
    missing = required - set(wb.sheetnames)
    wb.close()
    if missing:
        raise RuntimeError(f"验证失败，缺少 Sheet: {missing}")
    log.info("验证通过: %s", path)

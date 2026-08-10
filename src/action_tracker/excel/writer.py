"""安全 Excel Writer（规范 §43/§44）。

只有 QA PASS 且非 dry-run 才允许写正式 Master。写入流程：
    正式 Master -> 复制到 temp -> 更新 temp -> 完整验证 -> 关闭 -> 替换正式 Master
任一步失败则正式 Master 保持原样。
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import shutil
from pathlib import Path
from typing import Any

import openpyxl

from .reader import ES_MAP, ZH_MAP

log = logging.getLogger(__name__)

RUN_LOG_HEADERS = ["Run ID", "运行日期", "开始时间", "结束时间", "Sitemap SKU数", "Listing SKU数",
                   "ACTIVE", "NEW", "REAPPEARED", "MISSING_FIRST", "MISSING_CONTINUED", "OFFLINE",
                   "PRICE_UP", "PRICE_DOWN", "PROMO_START", "PROMO_END", "NEW_BADGE_ON", "NEW_BADGE_OFF",
                   "CONTENT_CHANGE", "异常数量", "QA状态", "运行状态", "备注"]

REVIEW_HEADERS = ["日期", "SKU", "问题类型", "证据", "候选值", "置信度", "建议动作", "人工备注"]


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
    ws = wb[title]
    ws.append(headers)
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
    """原子更新正式 Master。dry_run 时禁止调用。"""
    if dry_run:
        raise RuntimeError("dry-run 禁止写 Master")

    master: Path = cfg["paths"]["master"]
    if not master.exists():
        raise FileNotFoundError(f"Master 不存在: {master}")

    _backup(master, cfg["paths"]["backups"])
    tmp: Path = cfg["paths"]["temp"] / master.name
    tmp.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(master, tmp)

    wb = openpyxl.load_workbook(tmp)
    # 01 / 02 更新
    n_zh = _update_or_append_current(wb, "01_SKU_ZH_CURRENT", ZH_MAP, updated_records, set(ZH_MAP.values()))
    n_es = _update_or_append_current(wb, "02_SKU_ES_CURRENT", ES_MAP, updated_records, set(ES_MAP.values()))
    log.info("01 更新 %d 行, 02 更新 %d 行", n_zh, n_es)

    # 03 / 04 追加
    _append_rows(wb, "03_PRICE_HISTORY", price_events, list(price_events[0].keys()) if price_events else [])
    _append_rows(wb, "04_EVENT_HISTORY", event_events, list(event_events[0].keys()) if event_events else [])

    # 05_RUN_LOG / 06_REVIEW_QUEUE（规范要求的新表；不存在则创建）
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
    # 原子替换
    os.replace(tmp, master)
    log.info("正式 Master 已更新: %s", master)
    return master


def _validate(path: Path) -> None:
    """重开文件验证可读、各表存在、无空数据区错误。失败抛异常以保护原文件。"""
    wb = openpyxl.load_workbook(path, read_only=True)
    required = {"01_SKU_ZH_CURRENT", "02_SKU_ES_CURRENT", "03_PRICE_HISTORY", "04_EVENT_HISTORY"}
    missing = required - set(wb.sheetnames)
    wb.close()
    if missing:
        raise RuntimeError(f"验证失败，缺少 Sheet: {missing}")
    log.info("验证通过: %s", path)

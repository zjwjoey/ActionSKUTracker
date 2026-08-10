"""读取正式 Action_Master.xlsx（现有结构），映射为内部字段。

现有 Master 的中文列头是业务术语，内部用英文 snake_case。
读取时保持源数据不动；仅用于 baseline 与每日对比。
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import openpyxl

from ..services.normalization import fmt_date, parse_bool_zh, parse_price

# 01_SKU_ZH_CURRENT 列头 -> 内部键
ZH_MAP = {
    "Canonical_ID": "canonical_id",
    "SKU": "sku",
    "中文品名": "name_zh",
    "西班牙语品名": "name_es",
    "当前售价 (€)": "current_price",
    "原价 (€)": "original_price",
    "上次售价 (€)": "last_price",
    "最近一次变价方向": "price_direction",
    "本期价格变化": "price_change",
    "变化金额 (€)": "change_amount",
    "变化幅度 (%)": "change_percent",
    "最近变价日期": "last_change_date",
    "历史最低价 (€)": "price_min",
    "历史最高价 (€)": "price_max",
    "一级类目（中文）": "cat1_zh",
    "二级类目（中文）": "cat2_zh",
    "一级类目（西语）": "cat1_es",
    "二级类目（西语）": "cat2_es",
    "规格（中文）": "spec_zh",
    "规格（西语）": "spec_es",
    "单价": "unit_price",
    "新品": "is_new_badge",
    "促销": "promotion",
    "可持续": "sustainable",
    "折扣": "discount",
    "原始标签": "raw_tags",
    "当前状态": "status",
    "首次发现日期": "first_seen",
    "最后确认存在日期": "last_seen",
    "中文描述": "desc_zh",
    "中文产品详情": "details_zh",
    "商品链接": "product_url",
    "图片链接": "image_url",
    "翻译状态": "translation_status",
    "匹配状态": "match_status",
}

# 02_SKU_ES_CURRENT 列头 -> 内部键（西语事实数据）
ES_MAP = {
    "Canonical_ID": "canonical_id",
    "SKU": "sku",
    "西班牙语品名": "name_es",
    "当前售价 (€)": "current_price",
    "原价 (€)": "original_price",
    "上次售价 (€)": "last_price",
    "最近一次变价方向": "price_direction",
    "本期价格变化": "price_change",
    "变化金额 (€)": "change_amount",
    "变化幅度 (%)": "change_percent",
    "最近变价日期": "last_change_date",
    "历史最低价 (€)": "price_min",
    "历史最高价 (€)": "price_max",
    "一级类目（西语）": "cat1_es",
    "二级类目（西语）": "cat2_es",
    "规格（西语）": "spec_es",
    "单价": "unit_price",
    "新品": "is_new_badge",
    "促销": "promotion",
    "可持续": "sustainable",
    "折扣": "discount",
    "原始标签": "raw_tags",
    "当前状态": "status",
    "首次发现日期": "first_seen",
    "最后确认存在日期": "last_seen",
    "描述（西语）": "desc_es",
    "产品详情（西语）": "details_es",
    "商品链接": "product_url",
    "图片链接": "image_url",
    "匹配状态": "match_status",
}


def _sheet_rows(path: Path, sheet: str):
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb[sheet]
        it = ws.iter_rows(values_only=True)
        header = next(it, None)
        yield list(header), it
    finally:
        wb.close()


def _map_row(raw_row: tuple, header: list, colmap: dict, numeric: set) -> dict:
    rec: dict = {}
    for i, h in enumerate(header):
        key = colmap.get(h)
        if not key or i >= len(raw_row):
            continue
        v = raw_row[i]
        if key in numeric:
            rec[key] = parse_price(v)
        elif key in {"first_seen", "last_seen", "last_change_date"}:
            rec[key] = fmt_date(v)
        elif key in {"is_new_badge", "promotion", "sustainable"}:
            rec[key] = parse_bool_zh(v)
        else:
            rec[key] = v
    return rec


def read_sheet_as_records(path: Path, sheet: str, colmap: dict) -> list[dict]:
    numeric = {
        "current_price", "original_price", "last_price", "change_amount", "change_percent",
        "price_min", "price_max", "discount",
    }
    out = []
    for header, it in _sheet_rows(path, sheet):
        for row in it:
            rec = _map_row(row, header, colmap, numeric)
            if rec.get("sku"):
                out.append(rec)
    return out


def load_current(path: Path) -> dict[str, dict]:
    """合并 01(ZH) + 02(ES)，返回 {sku: 完整内部记录}。ES 为事实来源，优先覆盖。"""
    zh = read_sheet_as_records(path, "01_SKU_ZH_CURRENT", ZH_MAP)
    es = read_sheet_as_records(path, "02_SKU_ES_CURRENT", ES_MAP)
    merged: dict[str, dict] = {}
    for r in zh:
        sku = str(r.get("sku"))
        merged[sku] = r
    for r in es:
        sku = str(r.get("sku"))
        if sku not in merged:
            merged[sku] = {"sku": sku, "canonical_id": r.get("canonical_id")}
        for k, v in r.items():
            if v is not None:
                merged[sku][k] = v
    # 补充 canonical_id（确定性生成）
    from ..services.normalization import canonical_id
    for sku, rec in merged.items():
        rec.setdefault("canonical_id", canonical_id(sku))
    return merged


def read_price_history(path: Path) -> list[dict]:
    """03_PRICE_HISTORY 全部行（只读，不解析回内部键，保留原始列）。"""
    out = []
    for header, it in _sheet_rows(path, "03_PRICE_HISTORY"):
        for row in it:
            out.append({h: row[i] for i, h in enumerate(header) if i < len(row)})
    return out


def read_event_history(path: Path) -> list[dict]:
    out = []
    for header, it in _sheet_rows(path, "04_EVENT_HISTORY"):
        for row in it:
            out.append({h: row[i] for i, h in enumerate(header) if i < len(row)})
    return out

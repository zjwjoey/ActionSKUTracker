"""REVIEW_QUEUE 统一入口（规范 §五十 / §十 06_REVIEW_QUEUE）。

所有需要人工复核的疑点必须通过 add_review_item 生成，统一中文表头与问题类型，
禁止在业务代码里手拼 dict，避免 key 与 06 sheet 表头不一致。
"""
from __future__ import annotations

from typing import Any

# 06_REVIEW_QUEUE 表头（与 excel/writer.REVIEW_HEADERS 一致，此处为权威定义）
REVIEW_HEADERS = ["日期", "SKU", "问题类型", "证据", "候选值", "置信度", "建议动作", "人工备注"]

# 允许的问题类型全集（扩展即在此追加）
REVIEW_ISSUE_TYPES = (
    "SITEMAP_ONLY",              # 只在 sitemap、不在 listing
    "LISTING_ONLY",              # 只在 listing、不在 sitemap
    "UNKNOWN",                   # 曾认识但今日来源不明确
    "SKU_MATCH_CONFLICT",        # SKU 匹配冲突
    "IDENTITY_CONFLICT",         # 商品身份冲突
    "PRICE_ANOMALY",             # 价格异常（超阈值）
    "LABEL_PARSE_FAILED",        # 标签解析失败
    "DETAIL_FETCH_FAILED",       # 详情页抓取失败
    "DETAIL_PARSE_FAILED",       # 详情页解析失败
    "TRANSLATION_FAILED",        # 翻译失败
    "TRANSLATION_LOW_CONFIDENCE",  # 翻译置信度低
    "CATEGORY_CONFLICT",         # 类目冲突
    "IMAGE_MISSING",             # 图片缺失
    "DATA_INCONSISTENCY",        # 数据不一致
)


def add_review_item(
    date: str,
    sku: str,
    issue_type: str,
    evidence: str = "",
    candidates: Any = None,
    confidence: float | None = None,
    suggested_action: str = "人工核对",
    note: str = "",
) -> dict:
    """生成一条 REVIEW_QUEUE 记录，key 与 06 sheet 中文表头一致。"""
    if issue_type not in REVIEW_ISSUE_TYPES:
        raise ValueError(f"未知问题类型: {issue_type!r}，可选: {REVIEW_ISSUE_TYPES}")
    return {
        "日期": date,
        "SKU": sku,
        "问题类型": issue_type,
        "证据": evidence,
        "候选值": candidates if candidates is not None else "",
        "置信度": confidence,
        "建议动作": suggested_action,
        "人工备注": note,
    }

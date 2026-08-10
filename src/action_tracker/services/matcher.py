"""SKU 身份匹配（规范 §6 / §52 REVIEW_QUEUE）。

阶段一：以官网商品链接中的 /p/<id>/ 为唯一身份，SKU 精确匹配。
无法验证身份的历史记录只进 REVIEW_QUEUE，绝不猜测 SKU（AGENTS.md 规则 4）。
"""
from __future__ import annotations

import re

_ID_RE = re.compile(r"/p/(\d+)/")


def extract_sku_from_url(url: str) -> str | None:
    m = _ID_RE.search(url or "")
    return m.group(1) if m else None


def match_light_to_baseline(light: dict, baseline: dict[str, dict]) -> tuple[str | None, str]:
    """返回 (匹配到的 SKU 或 None, 匹配方法)。未命中即 REVIEW。"""
    sku = str(light.get("sku") or "").strip()
    if sku and sku in baseline:
        return sku, "SKU_EXACT"
    if sku:
        return None, "SKU_UNKNOWN"  # 官网有 ID 但系统未认识 -> NEW（不是冲突）
    return None, "NO_SKU"

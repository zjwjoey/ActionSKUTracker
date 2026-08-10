"""SKU Monitor：结合 sitemap / listing / 昨日CURRENT / known_skus，判定每日状态。

只回答"今天这个 SKU 是否存在/什么状态"，不负责详情、翻译、图片、Excel（规范 §14）。
"""
from __future__ import annotations

from dataclasses import dataclass

from ..services.lifecycle import classify


@dataclass
class SkuStatus:
    sku: str
    canonical_id: str
    status: str                  # NEW/ACTIVE/REAPPEARED/MISSING_FIRST/MISSING_CONTINUED/OFFLINE/ABSENT
    source_flag: str             # BOTH / SITEMAP_ONLY / LISTING_ONLY / NONE
    sitemap_present: bool
    listing_present: bool
    was_yesterday: bool
    ever_seen: bool
    first_seen: str | None
    missing_count: int
    event: str | None            # 需要写 EVENT_HISTORY 的事件
    light: object | None = None  # 今日 listing 轻量字段（可选携带）


def run_sku_monitor(
    sitemap_skus: list[str],
    listing_light: dict[str, object],
    yesterday_records: dict[str, dict],
    known: dict[str, dict],
    offline_runs: int = 3,
) -> tuple[dict[str, SkuStatus], set[str]]:
    """执行 SKU 集合核对，返回 {sku: SkuStatus} 与今天的存在集合。"""
    today_set = set(sitemap_skus) | set(listing_light.keys())
    yesterday_set = set(yesterday_records.keys())
    all_skus = today_set | yesterday_set | set(known.keys())

    result: dict[str, SkuStatus] = {}
    for sku in all_skus:
        today_present = sku in today_set
        was_yesterday = sku in yesterday_set
        k = known.get(sku)
        ever_seen = k is not None
        first_seen = k.get("first_seen_date") if k else None
        missing_count = int(k.get("missing_count") or 0) if k else 0
        if today_present and was_yesterday and not ever_seen:
            # 昨天 CURRENT 但 known 缺失（理论上不会；防御）
            ever_seen = True

        cls = classify(
            today_present=today_present,
            was_yesterday=was_yesterday,
            ever_seen=ever_seen,
            missing_count=missing_count,
            offline_runs=offline_runs,
        )
        # 来源标注
        s_flag = "BOTH"
        if today_present:
            if sku in set(sitemap_skus) and sku not in listing_light:
                s_flag = "SITEMAP_ONLY"
            elif sku in listing_light and sku not in set(sitemap_skus):
                s_flag = "LISTING_ONLY"
        else:
            s_flag = "NONE"

        cid = f"ACT{sku.zfill(7)}"
        result[sku] = SkuStatus(
            sku=sku,
            canonical_id=cid,
            status=cls.status,
            source_flag=s_flag,
            sitemap_present=sku in set(sitemap_skus),
            listing_present=sku in listing_light,
            was_yesterday=was_yesterday,
            ever_seen=ever_seen,
            first_seen=first_seen,
            missing_count=cls.missing_count,
            event=cls.event,
            light=listing_light.get(sku),
        )
    return result, today_set

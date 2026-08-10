"""SKU 生命周期状态判定（规范 §14-§18，测试 §60 第 1-7 项）。

状态机输入：
    today_present   今天是否存在（sitemap ∪ listing）
    was_yesterday   昨天 baseline CURRENT 里是否有
    ever_seen       known_skus 历史是否出现过（FIRST_SEEN 已记录）
    missing_count   当前连续缺失次数（0 起）
    offline_runs    阈值，达到即 OFFLINE

保证约束：
    - FIRST_SEEN 当天不可能 REAPPEARED（两者判定条件互斥）。
    - 抓不到不等于下架：缺失要连续达到阈值才 OFFLINE。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Classification:
    status: str                 # NEW/ACTIVE/REAPPEARED/MISSING_FIRST/MISSING_CONTINUED/OFFLINE/ABSENT
    missing_count: int          # 更新后的连续缺失次数
    event: str | None           # 需要写入 EVENT_HISTORY 的事件类型（无则 None）


def classify(
    today_present: bool,
    was_yesterday: bool,
    ever_seen: bool,
    missing_count: int = 0,
    offline_runs: int = 3,
) -> Classification:
    # ---- 今天存在 ----
    if today_present and not ever_seen:
        return Classification("NEW", 0, "FIRST_SEEN")
    if today_present and was_yesterday:
        return Classification("ACTIVE", 0, None)
    if today_present and not was_yesterday and ever_seen:
        return Classification("REAPPEARED", 0, "REAPPEARED")

    # ---- 今天不存在 ----
    if (was_yesterday or missing_count > 0) and ever_seen:
        new_missing = missing_count + 1
        if new_missing >= offline_runs:
            return Classification("OFFLINE", new_missing, "OFFLINE_CONFIRMED")
        if missing_count == 0:
            return Classification("MISSING_FIRST", new_missing, "MISSING_FIRST")
        return Classification("MISSING_CONTINUED", new_missing, "MISSING_CONTINUED")
    # 从不在历史 / 已 offline 且仍缺失 → 不再推进缺失计数
    return Classification("ABSENT", missing_count, None)

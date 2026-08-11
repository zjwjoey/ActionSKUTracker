"""SKU 生命周期状态判定（规范 §14-§18，测试 §60 第 1-7 项）。

状态机输入：
    today_present   今天是否存在（sitemap ∪ listing）
    previous_status 上一有效生命周期状态（known_skus.last_status）：ACTIVE/MISSING/OFFLINE
    ever_seen       known_skus 历史是否出现过（FIRST_SEEN 已记录）
    missing_count   当前连续缺失次数（0 起）
    offline_runs    阈值，达到即 OFFLINE

REAPPEARED 判定（事件，不是长期 status）：
    today_present ∧ ever_seen ∧ previous_status ∈ {MISSING, OFFLINE}
    —— 上一有效状态是 ACTIVE → ACTIVE；历史上从未出现 → NEW/FIRST_SEEN。
    不以"是否在上一期 CURRENT"为条件（CURRENT 会保留 MISSING 中的 SKU）。

保证约束：
    - FIRST_SEEN 当天不可能 REAPPEARED（两者判定条件互斥）。
    - 抓不到不等于下架：缺失要连续达到阈值才 OFFLINE。
"""
from __future__ import annotations

from dataclasses import dataclass

REAPPEARING_STATES = ("MISSING", "OFFLINE")


@dataclass(frozen=True)
class Classification:
    status: str                 # NEW/ACTIVE/REAPPEARED/MISSING_FIRST/MISSING_CONTINUED/OFFLINE/ABSENT
    missing_count: int          # 更新后的连续缺失次数
    event: str | None           # 需要写入 EVENT_HISTORY 的事件类型（无则 None）


def classify(
    today_present: bool,
    previous_status: str,
    ever_seen: bool,
    missing_count: int = 0,
    offline_runs: int = 3,
) -> Classification:
    # ---- 今天存在 ----
    if today_present and not ever_seen:
        return Classification("NEW", 0, "FIRST_SEEN")
    if today_present:
        # 上一有效状态是 MISSING/OFFLINE → 重现；否则（ACTIVE/未知）连续在售 → ACTIVE
        if previous_status in REAPPEARING_STATES:
            return Classification("REAPPEARED", 0, "REAPPEARED")
        return Classification("ACTIVE", 0, None)

    # ---- 今天不存在 ----
    if not ever_seen:
        return Classification("ABSENT", missing_count, None)
    if previous_status == "OFFLINE":
        # 已确认下架且仍缺失：计数冻结，不再推进缺失计数
        return Classification("ABSENT", missing_count, None)
    new_missing = missing_count + 1
    if new_missing >= offline_runs:
        return Classification("OFFLINE", new_missing, "OFFLINE_CONFIRMED")
    if missing_count == 0:
        return Classification("MISSING_FIRST", new_missing, "MISSING_FIRST")
    return Classification("MISSING_CONTINUED", new_missing, "MISSING_CONTINUED")

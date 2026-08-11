"""规范 §60 测试 1-7、24：SKU 生命周期。

REAPPEARED 核心规则（事件，不是长期 status）：
    today_present ∧ ever_seen ∧ previous_status ∈ {MISSING, OFFLINE} → REAPPEARED。
上一有效状态 ACTIVE → ACTIVE；历史从未出现 → NEW/FIRST_SEEN。
不以"是否在上一期 CURRENT"为条件（CURRENT 会保留 MISSING 中的 SKU）。
"""
import pytest

from action_tracker.services.lifecycle import classify
from action_tracker.monitor.sku_monitor import run_sku_monitor


# ---- 测试 1：上一有效状态 ACTIVE、今天有 -> ACTIVE ----
def test_t1_active_today_present_active():
    c = classify(today_present=True, previous_status="ACTIVE", ever_seen=True)
    assert c.status == "ACTIVE"
    assert c.event is None


# ---- 测试 2：历史从没出现、今天有 -> NEW ----
def test_t2_never_seen_today_present_new():
    c = classify(today_present=True, previous_status="", ever_seen=False)
    assert c.status == "NEW"
    assert c.event == "FIRST_SEEN"


# ---- 测试 3：FIRST_SEEN 当天不能 REAPPEARED ----
def test_t3_first_seen_not_reappeared():
    c = classify(today_present=True, previous_status="", ever_seen=False)
    assert c.status == "NEW"
    assert c.event != "REAPPEARED"


# ---- 测试 4：历史以前有、中间缺失、今天重现 -> REAPPEARED ----
def test_t4_reappeared_from_missing():
    c = classify(today_present=True, previous_status="MISSING", ever_seen=True)
    assert c.status == "REAPPEARED"
    assert c.event == "REAPPEARED"
    assert c.missing_count == 0


# ---- 测试 4b：OFFLINE 后重现 -> REAPPEARED ----
def test_t4b_reappeared_from_offline():
    c = classify(today_present=True, previous_status="OFFLINE", ever_seen=True)
    assert c.status == "REAPPEARED"
    assert c.event == "REAPPEARED"
    assert c.missing_count == 0


# ---- 测试 5：第一次消失 -> MISSING_FIRST ----
def test_t5_first_missing():
    c = classify(today_present=False, previous_status="ACTIVE", ever_seen=True, missing_count=0)
    assert c.status == "MISSING_FIRST"
    assert c.missing_count == 1


# ---- 测试 6：未达到阈值 -> 不 OFFLINE ----
def test_t6_not_offline_before_threshold():
    c = classify(today_present=False, previous_status="MISSING", ever_seen=True, missing_count=1, offline_runs=3)
    assert c.status == "MISSING_CONTINUED"
    assert c.missing_count == 2
    assert c.status != "OFFLINE"


# ---- 测试 7：达到阈值 -> OFFLINE ----
def test_t7_offline_at_threshold():
    c = classify(today_present=False, previous_status="MISSING", ever_seen=True, missing_count=2, offline_runs=3)
    assert c.status == "OFFLINE"
    assert c.missing_count == 3
    assert c.event == "OFFLINE_CONFIRMED"


# ---- 已 OFFLINE 且仍缺失：计数冻结，不推进 ----
def test_offline_absent_frozen_count():
    c = classify(today_present=False, previous_status="OFFLINE", ever_seen=True, missing_count=3)
    assert c.status == "ABSENT"
    assert c.missing_count == 3
    assert c.event is None


# ---- 测试 24：CURRENT 不包含 OFFLINE 商品 ----
def test_t24_offline_excluded_from_current():
    # 昨日 CURRENT 有 A；今天缺 A，连续缺 2 次后达阈值 -> OFFLINE
    yesterday = {"1001": {"sku": "1001", "canonical_id": "ACT0001001"}}
    known = {"1001": {"official_sku": "1001", "first_seen_date": "2026-01-01", "missing_count": "2"}}
    statuses, today_set = run_sku_monitor(
        sitemap_skus=[],
        listing_light={},
        yesterday_records=yesterday,
        known=known,
        offline_runs=3,
    )
    assert statuses["1001"].status == "OFFLINE"
    # OFFLINE 商品从 CURRENT 剔除：daily 阶段只保留 MISSING/ACTIVE 在 CURRENT
    current_statuses = {s for s in statuses.values() if s.status in ("ACTIVE", "MISSING_FIRST", "MISSING_CONTINUED")}
    assert "1001" not in current_statuses


# ---- 端到端：SKU Monitor 组合 ----
def test_monitor_new_active_missing():
    yesterday = {"A": {"sku": "A"}, "B": {"sku": "B"}}
    known = {
        "A": {"official_sku": "A", "last_status": "ACTIVE"},
        "B": {"official_sku": "B", "last_status": "ACTIVE"},
        "C": {"official_sku": "C", "last_status": "OFFLINE", "missing_count": "3"},
    }
    statuses, today_set = run_sku_monitor(
        sitemap_skus=["A", "C"],
        listing_light={"A": {}, "C": {}},
        yesterday_records=yesterday,
        known=known,
        offline_runs=3,
    )
    assert statuses["A"].status == "ACTIVE"
    assert statuses["B"].status == "MISSING_FIRST"
    assert statuses["C"].status == "REAPPEARED"
    assert today_set == {"A", "C"}


# ==================== REAPPEARED 新规则 ====================

# ---- 场景 1/2：商品在上一期 CURRENT 仍保留（MISSING 中），今天重现 -> REAPPEARED 而非 ACTIVE ----
def test_reappeared_even_if_still_in_previous_current():
    # 关键：was_yesterday（在上一期 CURRENT）不再决定 ACTIVE。
    # previous_status=MISSING 今天重现 → REAPPEARED，missing_count 清零。
    c = classify(today_present=True, previous_status="MISSING", ever_seen=True)
    assert c.status == "REAPPEARED"
    assert c.event == "REAPPEARED"
    assert c.missing_count == 0


# ---- 场景 1 集成：Day1 ACTIVE → Day2 MISSING_FIRST（仍在 CURRENT）→ Day3 PRESENT ----
def test_day1_active_day2_missing_day3_present_reappeared():
    known = {"1001": {"official_sku": "1001", "first_seen_date": "2026-08-11",
                      "last_status": "MISSING", "missing_count": "1"}}
    # 上一期 CURRENT 仍保留 Day2 的 MISSING_FIRST 商品
    yesterday = {"1001": {"sku": "1001", "status": "MISSING_FIRST"}}
    statuses, _ = run_sku_monitor(["1001"], {"1001": {}}, yesterday, known, 3)
    s = statuses["1001"]
    assert s.status == "REAPPEARED"
    assert s.event == "REAPPEARED"
    assert s.missing_count == 0
    assert s.previous_status == "MISSING"


# ---- 场景 2 集成：Day1 ACTIVE → Day2 MISSING_CONTINUED（仍在 CURRENT）→ Day3 PRESENT ----
def test_day1_active_day2_missing_continued_day3_present_reappeared():
    known = {"1001": {"official_sku": "1001", "first_seen_date": "2026-08-11",
                      "last_status": "MISSING", "missing_count": "2"}}
    yesterday = {"1001": {"sku": "1001", "status": "MISSING_CONTINUED"}}
    statuses, _ = run_sku_monitor(["1001"], {"1001": {}}, yesterday, known, 3)
    s = statuses["1001"]
    assert s.status == "REAPPEARED"
    assert s.event == "REAPPEARED"
    assert s.missing_count == 0


# ---- 场景 3：OFFLINE 后重新出现 -> REAPPEARED + ACTIVE ----
def test_reappeared_after_offline():
    known = {"1001": {"official_sku": "1001", "first_seen_date": "2026-01-09",
                      "last_status": "OFFLINE", "missing_count": "3",
                      "offline_date": "2026-08-10", "ever_offline": "true"}}
    yesterday = {}  # OFFLINE 商品已移出 CURRENT
    statuses, _ = run_sku_monitor(["1001"], {"1001": {}}, yesterday, known, 3)
    s = statuses["1001"]
    assert s.status == "REAPPEARED"
    assert s.event == "REAPPEARED"
    assert s.missing_count == 0
    assert s.previous_status == "OFFLINE"


# ---- 场景 4：连续 ACTIVE -> 不产生 REAPPEARED ----
def test_consecutive_active_no_reappeared():
    c = classify(today_present=True, previous_status="ACTIVE", ever_seen=True)
    assert c.status == "ACTIVE"
    assert c.event is None
    assert c.event != "REAPPEARED"


# ---- 场景 5：首次出现 -> FIRST_SEEN，不得 REAPPEARED ----
def test_first_seen_never_reappeared():
    c = classify(today_present=True, previous_status="", ever_seen=False)
    assert c.status == "NEW"
    assert c.event == "FIRST_SEEN"
    assert c.event != "REAPPEARED"

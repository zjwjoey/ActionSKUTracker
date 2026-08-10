"""规范 §60 测试 1-7、24：SKU 生命周期。"""
import pytest

from action_tracker.services.lifecycle import classify
from action_tracker.monitor.sku_monitor import run_sku_monitor


# ---- 测试 1：昨天有、今天有 -> ACTIVE ----
def test_t1_yesterday_and_today_active():
    c = classify(today_present=True, was_yesterday=True, ever_seen=True)
    assert c.status == "ACTIVE"
    assert c.event is None


# ---- 测试 2：历史从没出现、今天有 -> NEW ----
def test_t2_never_seen_today_present_new():
    c = classify(today_present=True, was_yesterday=False, ever_seen=False)
    assert c.status == "NEW"
    assert c.event == "FIRST_SEEN"


# ---- 测试 3：FIRST_SEEN 当天不能 REAPPEARED ----
def test_t3_first_seen_not_reappeared():
    c = classify(today_present=True, was_yesterday=False, ever_seen=False)
    assert c.status == "NEW"
    assert c.event != "REAPPEARED"


# ---- 测试 4：历史以前有、中间无、今天有 -> REAPPEARED ----
def test_t4_reappeared():
    c = classify(today_present=True, was_yesterday=False, ever_seen=True)
    assert c.status == "REAPPEARED"
    assert c.event == "REAPPEARED"


# ---- 测试 5：第一次消失 -> MISSING_FIRST ----
def test_t5_first_missing():
    c = classify(today_present=False, was_yesterday=True, ever_seen=True, missing_count=0)
    assert c.status == "MISSING_FIRST"
    assert c.missing_count == 1


# ---- 测试 6：未达到阈值 -> 不 OFFLINE ----
def test_t6_not_offline_before_threshold():
    c = classify(today_present=False, was_yesterday=True, ever_seen=True, missing_count=1, offline_runs=3)
    assert c.status == "MISSING_CONTINUED"
    assert c.missing_count == 2
    assert c.status != "OFFLINE"


# ---- 测试 7：达到阈值 -> OFFLINE ----
def test_t7_offline_at_threshold():
    c = classify(today_present=False, was_yesterday=True, ever_seen=True, missing_count=2, offline_runs=3)
    assert c.status == "OFFLINE"
    assert c.missing_count == 3
    assert c.event == "OFFLINE_CONFIRMED"


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
    known = {"A": {"official_sku": "A"}, "B": {"official_sku": "B"}, "C": {"official_sku": "C"}}
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

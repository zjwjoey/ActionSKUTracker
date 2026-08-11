"""规范 §60 测试 18-19：QA Gate。"""
import pytest

from action_tracker.qa.validator import run_qa


def _cfg():
    return {
        "qa": {
            "max_active_drop_percent": 15,
            "max_active_increase_percent": 20,
            "max_new_sku_percent": 5,
            "max_missing_percent": 5,
            "max_sitemap_listing_gap_percent": 5,
            "price_min": 0.01,
            "price_max": 1000.0,
            "max_anomaly_count": 20,
        }
    }


def _products(n, **kw):
    return [{"sku": str(i), "canonical_id": f"ACT{i:07d}", "product_url": "u", "current_price": 1.0, "cat1_es": "c", "raw_tags": ""} for i in range(n)]


# ---- 测试 18：大量 SKU 消失 -> QA FAIL ----
def test_t18_mass_drop_qa_fail():
    qa = run_qa(_cfg(), yesterday_total=5537, today_total=1800, sitemap_count=1800, listing_count=1800,
                new_count=0, missing_count=3737, price_up=0, price_down=0, anomaly_count=0, products=_products(1800))
    assert qa.passed is False
    assert qa.state == "FAIL"
    assert qa.checks["total_change"][0] is False


# ---- 测试 18b：正常波动 -> QA PASS ----
def test_normal_day_qa_pass():
    qa = run_qa(_cfg(), yesterday_total=5537, today_total=5500, sitemap_count=5500, listing_count=5480,
                new_count=5, missing_count=20, price_up=30, price_down=40, anomaly_count=0, products=_products(5500))
    assert qa.passed is True
    assert qa.state == "PASS"


# ---- 测试 19：QA FAIL -> Master 完全不变（写 Master 被拒）----
def test_t19_qa_fail_prevents_master_write():
    from action_tracker.excel.writer import write_master
    qa = run_qa(_cfg(), yesterday_total=5537, today_total=1800, sitemap_count=1800, listing_count=1800,
                new_count=0, missing_count=3737, price_up=0, price_down=0, anomaly_count=0, products=_products(1800))
    assert qa.passed is False
    # dry-run / FAIL 状态一律禁止写 Master
    with pytest.raises(RuntimeError):
        write_master({}, updated_records={}, price_events=[], event_events=[], dry_run=True)


# ---- 测试 19b：BLOCKED 直接 FAIL ----
def test_blocked_qa_fail():
    qa = run_qa(_cfg(), yesterday_total=5537, today_total=0, sitemap_count=0, listing_count=0,
                new_count=0, missing_count=0, price_up=0, price_down=0, anomaly_count=0, products=[], blocked=True)
    assert qa.state == "BLOCKED"
    assert qa.passed is False


def test_incomplete_observation_fails_qa_before_lifecycle_commit():
    qa = run_qa(_cfg(), yesterday_total=10, today_total=10, sitemap_count=10, listing_count=10,
                new_count=0, missing_count=0, price_up=0, price_down=0, anomaly_count=0,
                products=_products(10), observation_valid=False, category_coverage={"Hogar": False})
    assert qa.state == "FAIL"
    assert qa.checks["observation_valid"][0] is False


@pytest.mark.parametrize("access_state", ["COOLDOWN", "PROBE", "DEGRADED"])
def test_non_normal_access_state_fails_even_with_complete_coverage(access_state):
    qa = run_qa(_cfg(), yesterday_total=10, today_total=10, sitemap_count=10, listing_count=10,
                new_count=0, missing_count=0, price_up=0, price_down=0, anomaly_count=0,
                products=_products(10), access_state=access_state)
    assert qa.passed is False and qa.state == "FAIL"
    assert qa.checks["access_state_complete"][0] is False

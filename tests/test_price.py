"""规范 §60 测试 8-13：价格。"""
from action_tracker.services.price import compare_price, is_price_anomaly, update_hist_min_max
from action_tracker.services.change import compute_changes
from action_tracker.services.normalization import fmt_date, parse_price


def _rec(**kw):
    base = {"sku": "1001", "canonical_id": "ACT0001001", "current_price": 3.99, "raw_tags": ""}
    base.update(kw)
    return base


# ---- 测试 8：价格不变 -> UNCHANGED ----
def test_t8_unchanged():
    assert compare_price(3.99, 3.99).change_type == "UNCHANGED"


# ---- 测试 9：价格上涨 -> UP ----
def test_t9_up():
    pc = compare_price(3.99, 4.99)
    assert pc.change_type == "UP"
    assert pc.amount == 1.0


# ---- 测试 10：价格下降 -> DOWN ----
def test_t10_down():
    pc = compare_price(4.99, 3.99)
    assert pc.change_type == "DOWN"
    assert pc.amount == -1.0


# ---- 测试 11：今天没有价格变化时，不能继续显示旧 DOWN/UP ----
def test_t11_no_change_no_stale_direction():
    before = _rec(current_price=4.99)
    after = _rec(current_price=4.99)
    out = compute_changes("1001", "ACT0001001", before, after, "2026-08-10", 0.01, 1000)
    assert out.price_events == []  # 不产生 DOWN/UP，不会把旧方向带进今天


# ---- 测试 12：历史最低/最高正确 ----
def test_t12_hist_min_max():
    before = _rec(current_price=0.79, price_min=0.79, price_max=0.79)
    after = _rec(current_price=0.69)
    out = compute_changes("1001", "ACT0001001", before, after, "2026-08-10", 0.01, 1000)
    assert after["price_min"] == 0.69
    assert after["price_max"] == 0.79
    assert out.price_events[0]["变化类型"] == "DOWN"


# ---- 测试 13：异常价格不污染统计 ----
def test_t13_anomaly_not_in_stats():
    for bad in (0, -1, 5000):
        before = _rec(current_price=3.99, price_min=3.99, price_max=3.99)
        after = _rec(current_price=bad)
        out = compute_changes("1001", "ACT0001001", before, after, "2026-08-10", 0.01, 1000)
        assert out.price_events == []
        assert after["price_min"] == 3.99
        assert after["price_max"] == 3.99
        assert len(out.anomalies) == 1
        assert is_price_anomaly(bad, 0.01, 1000)


# ---- 价格解析健壮性 ----
def test_price_parsing():
    assert parse_price("3,99 €") == 3.99
    assert parse_price("3,99 €/ud.") == 3.99
    assert parse_price("4,95") == 4.95
    assert parse_price("18") == 18.0
    assert parse_price("") is None
    assert parse_price("N/A") is None
    assert parse_price("1.234,56 €") == 1234.56


def test_fmt_date_parses_iso_datetime_from_master():
    assert fmt_date("2026-08-25T00:00:00") == "2026-08-25"
    assert fmt_date("2026-08-25T00:00:00+02:00") == "2026-08-25"

"""规范 §60 测试 14-16：官网标签解析。"""
from action_tracker.products.badges import parse_badges


# ---- 测试 14：Nuevo 标签正确解析 ----
def test_t14_nuevo():
    b = parse_badges("Nuevo")
    assert b.action_new_badge is True
    assert b.promotion_active is False


# ---- 测试 15：Promoción semanal 正确解析 ----
def test_t15_promo():
    raw = "Promoción semanal 05/08 – 11/08 | Una opción más sostenible | Nuevo | -21%"
    b = parse_badges(raw, run_year=2026)
    assert b.promotion_active is True
    assert b.promotion_start.isoformat() == "2026-08-05"
    assert b.promotion_end.isoformat() == "2026-08-11"
    assert b.discount == -0.21


# ---- 测试 16：可持续标签正确解析 ----
def test_t16_sustainable():
    b = parse_badges("Una opción más sostenible")
    assert b.sustainable_badge is True


# ---- 空标签 ----
def test_empty_badges():
    b = parse_badges(None)
    assert not b.action_new_badge
    assert not b.promotion_active
    assert not b.sustainable_badge

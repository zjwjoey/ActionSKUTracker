"""规范 §60 测试 14-16：官网标签解析。"""
from action_tracker.products.badges import build_badge_state, parse_badges


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


# ---- build_badge_state：成员集合派生今日徽章状态 ----
def test_badge_state_nuevo_on():
    assert build_badge_state(None, in_nuevo=True, in_promo=False) == "Nuevo"


def test_badge_state_promo_on_keeps_dated():
    base = "Promoción semanal 05/08 – 11/08 | -21%"
    out = build_badge_state(base, in_nuevo=False, in_promo=True)
    # 保留基线带日期的促销标签，保留折扣
    assert "Promoción semanal 05/08 – 11/08" in out
    assert "-21%" in out


def test_badge_state_promo_off_drops_discount():
    base = "Promoción semanal 05/08 – 11/08 | -21% | Una opción más sostenible"
    out = build_badge_state(base, in_nuevo=False, in_promo=False)
    # 促销结束：移除促销标签 + 折扣标签，可持续徽章保留基线
    assert "Promoción" not in out
    assert "-21%" not in out
    assert "Una opción más sostenible" in out


def test_badge_state_membership_authoritative():
    # 基线有 Nuevo 但今日不在 /nuevo/ 页 → 移除；基线无促销但今日在促销页 → 加入
    base = "Nuevo"
    out = build_badge_state(base, in_nuevo=False, in_promo=True)
    assert "Nuevo" not in out
    assert out.startswith("Promoción")


def test_badge_state_both_memberships():
    out = build_badge_state(None, in_nuevo=True, in_promo=True)
    assert "Nuevo" in out
    assert "Promoción" in out

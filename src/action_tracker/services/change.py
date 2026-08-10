"""价格/标签事件生成（规范 §26-§29、§48-§50）。

只追加真实事件：INITIAL / UP / DOWN、PROMO_START/END、NEW_BADGE_ON/OFF、SUSTAINABLE_ON/OFF。
价格没变不生成任何事件（不逐日记录相同价格）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..products.badges import Badges, parse_badges
from .price import PriceChange, compare_price, is_price_anomaly, update_hist_min_max


@dataclass
class ChangeOutcome:
    price_events: list[dict] = field(default_factory=list)
    badge_events: list[dict] = field(default_factory=list)
    content_events: list[dict] = field(default_factory=list)
    anomalies: list[dict] = field(default_factory=list)
    review_rows: list[dict] = field(default_factory=list)


def compute_changes(
    sku: str,
    canonical_id: str,
    before: dict | None,
    after: dict,
    run_date: str,
    price_min: float,
    price_max: float,
    run_id: str = "",
) -> ChangeOutcome:
    """对比 before（昨日/基线）与 after（今日），产出事件。after 已合并轻量/详情字段。"""
    out = ChangeOutcome()
    old_price = (before or {}).get("current_price")
    new_price = after.get("current_price")

    # ---- 价格事件：只记录真实变化 ----
    if is_price_anomaly(new_price, price_min, price_max):
        out.anomalies.append({
            "date": run_date, "sku": sku, "problem": "PRICE_ANOMALY",
            "evidence": f"price={new_price}", "suggested_action": "人工核对",
        })
        out.review_rows.append({
            "date": run_date, "sku": sku, "problem_type": "异常价格",
            "evidence": f"price={new_price}", "candidate": None, "confidence": None,
            "suggested_action": "人工核对",
        })
    else:
        pc: PriceChange = compare_price(old_price, new_price)
        if pc.change_type in ("NEW", "UP", "DOWN"):
            out.price_events.append({
                "Canonical_ID": canonical_id,
                "SKU": sku,
                "日期": run_date,
                "旧售价 (€)": old_price,
                "新售价 (€)": new_price,
                "原价 (€)": after.get("original_price"),
                "变化类型": pc.change_type,
                "变化金额 (€)": pc.amount,
                "变化幅度 (%)": pc.percent,
                "促销状态": _promo_label(after),
                "来源": run_id or "daily-run",
            })

    # ---- 历史最低/最高更新 ----
    after["price_min"], after["price_max"] = update_hist_min_max(
        (before or {}).get("price_min"),
        (before or {}).get("price_max"),
        new_price,
        price_min,
        price_max,
    )

    # ---- 标签事件 ----
    old_b = parse_badges((before or {}).get("raw_tags") if before else None)
    new_b: Badges = parse_badges(after.get("raw_tags"))
    _badge_events(out, sku, canonical_id, old_b, new_b, run_date, run_id)

    # ---- 内容哈希变化 ----
    from .hashing import content_hash
    if before is not None:
        if content_hash(before) != content_hash(after):
            out.content_events.append({
                "Canonical_ID": canonical_id,
                "SKU": sku,
                "日期": run_date,
                "事件类型": "CONTENT_CHANGE",
                "旧值": content_hash(before)[:12],
                "新值": content_hash(after)[:12],
                "来源": run_id or "daily-run",
            })
    else:
        out.content_events.append({
            "Canonical_ID": canonical_id,
            "SKU": sku,
            "日期": run_date,
            "事件类型": "FIRST_SEEN",
            "旧值": None,
            "新值": None,
            "来源": run_id or "daily-run",
        })
    return out


def _promo_label(rec: dict) -> str | None:
    b = parse_badges(rec.get("raw_tags"))
    return "是" if b.promotion_active else ("否" if rec.get("raw_tags") else None)


def _badge_events(out: ChangeOutcome, sku, cid, old: Badges, new: Badges, run_date, run_id):
    src = run_id or "daily-run"
    if old.action_new_badge != new.action_new_badge:
        out.badge_events.append({
            "Canonical_ID": cid, "SKU": sku, "日期": run_date,
            "事件类型": "ACTION_NEW_BADGE_ON" if new.action_new_badge else "ACTION_NEW_BADGE_OFF",
            "旧值": "TRUE" if old.action_new_badge else None,
            "新值": "TRUE" if new.action_new_badge else None,
            "来源": src,
        })
    if old.promotion_active != new.promotion_active:
        out.badge_events.append({
            "Canonical_ID": cid, "SKU": sku, "日期": run_date,
            "事件类型": "PROMO_START" if new.promotion_active else "PROMO_END",
            "旧值": "TRUE" if old.promotion_active else None,
            "新值": "TRUE" if new.promotion_active else None,
            "来源": src,
        })
    if old.sustainable_badge != new.sustainable_badge:
        out.badge_events.append({
            "Canonical_ID": cid, "SKU": sku, "日期": run_date,
            "事件类型": "SUSTAINABLE_BADGE_ON" if new.sustainable_badge else "SUSTAINABLE_BADGE_OFF",
            "旧值": "TRUE" if old.sustainable_badge else None,
            "新值": "TRUE" if new.sustainable_badge else None,
            "来源": src,
        })

"""价格比较与异常检测（规范 §26-§29）。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PriceChange:
    change_type: str          # NEW/UP/DOWN/UNCHANGED
    amount: float | None
    percent: float | None


def compare_price(old: float | None, new: float | None) -> PriceChange:
    """比较昨日与今日售价。old 为空 -> NEW（首次有效价格）。"""
    if old is None and new is not None:
        return PriceChange("NEW", None, None)
    if old is None or new is None:
        return PriceChange("UNCHANGED", None, None)
    if abs(new - old) < 1e-9:
        return PriceChange("UNCHANGED", 0.0, 0.0)
    amount = round(new - old, 4)
    percent = round(amount / old, 6) if old else None
    return PriceChange("UP" if new > old else "DOWN", amount, percent)


def is_price_anomaly(price: float | None, price_min: float, price_max: float) -> bool:
    """价格异常：0、负数、超上限。异常价格禁止进入统计与历史最低/最高。"""
    if price is None:
        return False
    if price <= 0:
        return True
    if price > price_max:
        return True
    return False


def update_hist_min_max(
    hist_min: float | None, hist_max: float | None, price: float | None, price_min: float, price_max: float
) -> tuple[float | None, float | None]:
    """用有效价格更新历史最低/最高；异常价格忽略。"""
    if is_price_anomaly(price, price_min, price_max):
        return hist_min, hist_max
    if price is None:
        return hist_min, hist_max
    nmin = price if hist_min is None else min(hist_min, price)
    nmax = price if hist_max is None else max(hist_max, price)
    return nmin, nmax

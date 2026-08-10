"""官网标签解析（规范 §24/§25/§48）。

输入 raw_tags（原文），例如：
    "Promoción semanal 05/08 – 11/08 | Una opción más sostenible | Nuevo | -21%"

输出：
    action_new_badge   官网是否显示 Nuevo（官网新品）
    promotion_active   是否促销中
    sustainable_badge  可持续标识
    discount           折扣（负百分比，如 -0.21）
    promotion_start/end  促销起止日期（DD/MM 结合运行年份）
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

from ..services.normalization import parse_discount_percent

_PROMO_RE = re.compile(r"promoci[oó]n", re.I)
_NUEVO_RE = re.compile(r"(^|[\s|])(nuevo)([\s|]|$)", re.I)
_SOST_RE = re.compile(r"sostenible", re.I)
_DISCOUNT_RE = re.compile(r"([\-−–]?\d{1,3}(?:[.,]\d{1,2})?)\s*%", re.I)
_DATE_RANGE_RE = re.compile(r"(\d{1,2})[/.](\d{1,2})\s*[–\-—]\s*(\d{1,2})[/.](\d{1,2})")


@dataclass
class Badges:
    raw: str
    action_new_badge: bool = False
    promotion_active: bool = False
    sustainable_badge: bool = False
    discount: float | None = None
    promotion_start: dt.date | None = None
    promotion_end: dt.date | None = None

    def to_dict(self) -> dict:
        return {
            "action_new_badge": self.action_new_badge,
            "promotion_active": self.promotion_active,
            "sustainable_badge": self.sustainable_badge,
            "discount": self.discount,
            "promotion_start": self.promotion_start.isoformat() if self.promotion_start else None,
            "promotion_end": self.promotion_end.isoformat() if self.promotion_end else None,
        }


def _date_ddmm(day: str, month: str, run_year: int) -> dt.date:
    try:
        return dt.date(run_year, int(month), int(day))
    except ValueError:
        return None


def parse_badges(raw: str | None, run_year: int | None = None) -> Badges:
    run_year = run_year or dt.date.today().year
    raw = (raw or "").strip()
    out = Badges(raw=raw)
    if not raw:
        return out
    out.promotion_active = bool(_PROMO_RE.search(raw))
    out.action_new_badge = bool(_NUEVO_RE.search(raw))
    out.sustainable_badge = bool(_SOST_RE.search(raw))
    dm = _DISCOUNT_RE.search(raw)
    if dm:
        out.discount = parse_discount_percent(dm.group(1))
    m = _DATE_RANGE_RE.search(raw)
    if m:
        out.promotion_start = _date_ddmm(m.group(1), m.group(2), run_year)
        out.promotion_end = _date_ddmm(m.group(3), m.group(4), run_year)
    return out

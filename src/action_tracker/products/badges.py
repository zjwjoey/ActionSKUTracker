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


_NUEVO_TAG = "Nuevo"
_PROMO_TAG_PREFIX = "Promoción semanal"


def build_badge_state(base_tags: str | None, in_nuevo: bool, in_promo: bool) -> str:
    """由"专属徽章页成员集合" + 基线徽章，构造今日权威 raw_tags。

    徽章检测的权威信号是页面成员集合（sku 出现在 /nuevo/ 页 = 有 Nuevo 徽章；
    出现在 /promocion-semanal/ 页 = 促销中）。实测卡片标签不可靠（同一页面有的卡
    片漏显徽章），不能作为徽章判定依据。

      - Nuevo：in_nuevo=True 时加入
      - 促销：in_promo=True 时加入（基线带日期范围则保留，维持 promotion_start/end）
      - 可持续/折扣等无专属页面的徽章：保留基线（无法每日检测，详情抓取时更新）
      - 促销结束（in_promo=False）时移除基线折扣百分比标签（折扣随促销走）
    """
    base_toks = [t.strip() for t in (base_tags or "").split("|") if t.strip()]
    kept = [t for t in base_toks if t != _NUEVO_TAG and not t.startswith(_PROMO_TAG_PREFIX)]
    if not in_promo:
        kept = [t for t in kept if not _DISCOUNT_RE.search(t)]
    if in_nuevo:
        kept.append(_NUEVO_TAG)
    if in_promo:
        dated = next((t for t in base_toks if t.startswith(_PROMO_TAG_PREFIX) and _DATE_RANGE_RE.search(t)), None)
        kept.append(dated or _PROMO_TAG_PREFIX)
    return " | ".join(kept)


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

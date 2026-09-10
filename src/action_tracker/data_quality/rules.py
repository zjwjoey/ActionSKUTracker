from __future__ import annotations

"""Shared deterministic data-quality text rules.

Historical audit and the CURRENT Master gate must classify the same text in
the same way.  This module intentionally contains no database access and no
translation logic; it only classifies transport residue and promotion-field
semantics.
"""

from enum import StrEnum
import re
from typing import Any


HTML_RE = re.compile(r"<\/?[A-Za-z][^>]*>", re.I)
UNIT_PRICE_RE = re.compile(r"(?:€/|€\s*/|/\s*(?:kg|l|ud\.?|unidad))", re.I)
PROMOTION_TEXT_COLUMNS = frozenset({
    "promotion", "promotion_status", "promotion_label", "promotion_text", "promotion_note",
})
UI_TEXT = frozenset({"añadir a tus favoritos", "leer más", "descripción"})


class PromotionTextClass(StrEnum):
    CLEAN = "CLEAN"
    VALID_PROMOTION = "VALID_PROMOTION"
    CONTAMINATED_UNIT_PRICE = "CONTAMINATED_UNIT_PRICE"
    CONTAMINATED_PROCESS_NOTE = "CONTAMINATED_PROCESS_NOTE"
    CONTAMINATED_BADGE_LEAKAGE = "CONTAMINATED_BADGE_LEAKAGE"


def contains_html_markup(value: Any) -> bool:
    text = str(value or "")
    return bool(HTML_RE.search(text) or "</" in text or re.search(r"<[^>]*>", text))


def contains_ui_transport_text(value: Any) -> bool:
    return str(value or "").strip().casefold() in UI_TEXT


def classify_promotion_text(
    value: Any,
    *,
    field_name: str | None = None,
    include_badges: bool = False,
) -> PromotionTextClass:
    """Classify promotion-channel text without rejecting valid discounts.

    ``raw_badges`` is an evidence channel: ``Nuevo`` and sustainability
    badges are valid there.  Explicit promotion fields accept normal offer
    language (``descuento``, ``rebaja``, ``oferta`` and ``discount``), but
    reject unit-price/process-note leakage and badge-only labels.
    """
    text = str(value or "").strip()
    if not text:
        return PromotionTextClass.CLEAN
    folded = text.casefold()
    if UNIT_PRICE_RE.search(text):
        return PromotionTextClass.CONTAMINATED_UNIT_PRICE
    if "workflow" in folded or "本期详情" in folded:
        return PromotionTextClass.CONTAMINATED_PROCESS_NOTE
    if include_badges or str(field_name or "").casefold() == "raw_badges":
        return PromotionTextClass.CLEAN
    if any(token in folded for token in ("descuento", "discount", "rebaja", "oferta", "promotion")):
        return PromotionTextClass.VALID_PROMOTION
    if any(token in folded for token in ("nuevo", "sostenible", "sostenibilidad", "sustainability")):
        return PromotionTextClass.CONTAMINATED_BADGE_LEAKAGE
    return PromotionTextClass.CLEAN


def promotion_text_is_contaminated(
    value: Any,
    *,
    field_name: str | None = None,
    include_badges: bool = False,
) -> bool:
    return classify_promotion_text(
        value, field_name=field_name, include_badges=include_badges,
    ) in {
        PromotionTextClass.CONTAMINATED_UNIT_PRICE,
        PromotionTextClass.CONTAMINATED_PROCESS_NOTE,
        PromotionTextClass.CONTAMINATED_BADGE_LEAKAGE,
    }


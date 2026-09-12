"""Offline safety checks for model-authored Chinese translations.

The guard is intentionally a *rejector*, not an auto-corrector.  It verifies
that a model did not change immutable numeric facts or move facts between
fields.  Callers can route rejected rows to the existing review queue; the
guard never writes Master, the dictionary, or a model cache.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from collections import Counter
from typing import Iterable, Mapping


NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
_CJK = re.compile(r"[\u3400-\u9fff]")

FIXED_CAT1 = frozenset({
    "DIY五金", "办公文具", "宠物用品", "厨房餐具", "服饰鞋包", "个人美容", "家居布置",
    "家务清洁", "旅行用品", "食品饮料", "数码影音", "玩具", "兴趣手作", "园艺户外", "运动用品",
})

# Canonical units are deliberately conservative.  Values are compared as a
# multiset so duplicated measurements remain facts.  Equivalent Spanish,
# symbol and Chinese renderings share one canonical value; conversions (for
# example 1 L -> 1000 ml) are not attempted by this reject-only guard.
_UNIT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("percent", re.compile(r"%|\bpor\s+ciento\b|百分之", re.IGNORECASE)),
    ("kg", re.compile(r"(?<![A-Za-z])kg(?![A-Za-z])|\bkilogramos?\b|千克|公斤", re.IGNORECASE)),
    ("g", re.compile(r"(?<![A-Za-z])g(?![A-Za-z])|\bgramos?\b|(?<!千)克", re.IGNORECASE)),
    ("ml", re.compile(r"(?<![A-Za-z])ml(?![A-Za-z])|\bmililitros?\b|毫升", re.IGNORECASE)),
    ("cl", re.compile(r"(?<![A-Za-z])cl(?![A-Za-z])|\bcentilitros?\b|厘升", re.IGNORECASE)),
    ("l", re.compile(r"(?<![A-Za-z])l(?![A-Za-z])|\blitros?\b|(?<!毫)(?<!厘)升", re.IGNORECASE)),
    ("km", re.compile(r"(?<![A-Za-z])km(?![A-Za-z])|\bkil[oó]metros?\b|千米|公里", re.IGNORECASE)),
    ("mm", re.compile(r"(?<![A-Za-z])mm(?![A-Za-z])|\bmil[ií]metros?\b|毫米", re.IGNORECASE)),
    ("cm", re.compile(r"(?<![A-Za-z])cm(?![A-Za-z])|\bcent[ií]metros?\b|厘米", re.IGNORECASE)),
    ("m", re.compile(r"(?<![A-Za-z])m(?![A-Za-z])|\bmetros?\b|(?<!毫)(?<!厘)(?<!千)米", re.IGNORECASE)),
    ("v", re.compile(r"(?<![A-Za-z])v(?![A-Za-z])|\bvoltios?\b|伏特|伏", re.IGNORECASE)),
    ("w", re.compile(r"(?<![A-Za-z])w(?![A-Za-z])|\bvatios?\b|瓦特|瓦", re.IGNORECASE)),
    ("a", re.compile(r"(?<=\d)\s*A\b|\bA\s*(?=\d)|\b[Aa]mperios?\b|安培|安")),
    ("mah", re.compile(r"(?<![A-Za-z])ma?h(?![A-Za-z])|毫安时", re.IGNORECASE)),
    ("lm", re.compile(r"(?<![A-Za-z])lm(?![A-Za-z])|\b(?:l[uú]menes?|lumen)\b|流明", re.IGNORECASE)),
    ("celsius", re.compile(r"°\s*c\b|\bgrados?\s+celsius\b|摄氏度", re.IGNORECASE)),
    ("hour", re.compile(r"\b(?:h|horas?)\b|小时", re.IGNORECASE)),
    ("minute", re.compile(r"\b(?:min|minutos?)\b|分钟", re.IGNORECASE)),
    ("second", re.compile(r"\b(?:s|segundos?)\b|秒", re.IGNORECASE)),
    ("serving", re.compile(r"\braciones?\b|份", re.IGNORECASE)),
)

# Product codes and standards are immutable facts, not translation style.
# Avoid matching ordinary title-case words or measurement symbols here.
_TECH_TOKEN = re.compile(
    r"(?<![A-Za-z0-9])(?:"
    r"USB(?:[\s-]?[A-Z])?|LED|PEFC|FSC|HDMI|NFC|RFID|"
    r"WI[\s-]?FI|BLUETOOTH|AAA|AA|PD|QC(?:\d+(?:\.\d+)?)?|"
    r"IP\d{2,3}|[A-Z]{1,5}[/-]?[A-Z]*\d+[A-Z0-9./-]*"
    r")(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_TECH_EXCLUDED = frozenset({"G", "KG", "ML", "CL", "L", "MM", "CM", "M", "KM", "V", "W", "A", "MAH", "LM"})

# A bare Spanish article must never generally be treated as a number.  The
# following narrow pattern is only for an explicit singular *quantity* phrase
# (for example ``un bote de espray``).  It can match a faithful Chinese
# container rendering such as ``一罐`` or ``1罐`` without weakening ordinary
# same-field numeric checks.
SPANISH_SINGLE_QUANTITY = re.compile(
    r"\b(?:un|una)\s+(?P<noun>bote|lata|botella|caja|bolsa|tubo|rollo|paquete|pack|pieza|unidad|par|juego|set|frasco|tarro|cápsula|capsula|pastilla)\b",
    re.IGNORECASE,
)
SPANISH_QUANTITY_TO_CHINESE_MEASURES = {
    "bote": ("罐", "瓶"), "lata": ("罐",), "botella": ("瓶",),
    "caja": ("盒",), "bolsa": ("袋", "包"), "tubo": ("管",),
    "rollo": ("卷",), "paquete": ("包",), "pack": ("包",),
    "pieza": ("件", "个"), "unidad": ("件", "个"), "par": ("双", "对"),
    "juego": ("套",), "set": ("套",), "frasco": ("罐", "瓶"),
    "tarro": ("罐", "瓶"), "cápsula": ("粒", "颗"), "capsula": ("粒", "颗"),
    "pastilla": ("片", "粒"),
}

# These are ordinary Spanish words that should not survive in a Chinese
# field.  Brand/model phrases are removed by ``allowed_brand_phrases`` first;
# this keeps a confirmed brand such as ``La Sonata`` legal while still
# catching a Spanish colour such as ``Antracita``.
SPANISH_TOKENS = {
    "el", "la", "los", "las", "para", "con", "sin", "del", "de", "y", "en",
    "color", "tamaño", "producto", "material", "cantidad", "contenido", "piezas", "juego", "juegos",
    "gramos", "litros", "antracita", "blanco", "blanca", "negro", "negra", "gris",
    "rojo", "roja", "verde", "azul", "amarillo", "amarilla", "rosa", "marrón",
}
ENGLISH_TOKENS = {
    "a", "an", "and", "are", "as", "be", "by", "clean", "contains", "for", "free",
    "from", "in", "of", "on", "or", "package", "paper", "plastics", "product", "the",
    "these", "this", "to", "with", "without", "your",
}
TOKEN = re.compile(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+")
_SINGLE_LETTER_TECHNICAL = set("ABCDEFGKLMNOPRSTVWXYZ")


def _is_technical_token(value: str, start: int, end: int, token: str) -> bool:
    """Recognize technical symbols without allowing ordinary English prose."""

    if not token.isupper():
        return False
    if len(token) > 1:
        # Acronyms such as USB/LED/PEFC and standards such as ACEA are valid
        # product facts; they are not English sentences.
        return True
    if token not in _SINGLE_LETTER_TECHNICAL:
        return False
    left = value[start - 1] if start > 0 else ""
    right = value[end] if end < len(value) else ""
    if left.isdigit() or right.isdigit():
        return True
    if "\u3400" <= left <= "\u9fff" or "\u3400" <= right <= "\u9fff":
        return True
    # Vitamin/technical notation may be separated by punctuation, e.g.
    # ``维生素A、D3和C``.  Require nearby CJK context so ``A product`` remains
    # an English residual.
    context = value[max(0, start - 3) : min(len(value), end + 3)]
    return any("\u3400" <= char <= "\u9fff" for char in context)


@dataclass(frozen=True)
class ModelOutputCheck:
    """Result of validating one model output."""

    accepted: bool
    reasons: tuple[str, ...]
    field_reasons: Mapping[str, tuple[str, ...]]


def numeric_tokens(value: object) -> list[str]:
    """Return normalized numeric tokens, retaining duplicate occurrences."""

    return sorted(token.replace(",", ".") for token in NUMBER.findall(str(value or "")))


def unit_tokens(value: object) -> list[str]:
    """Return canonical unit tokens, retaining duplicate occurrences."""

    text = str(value or "")
    tokens: list[str] = []
    for canonical, pattern in _UNIT_PATTERNS:
        tokens.extend(canonical for _ in pattern.finditer(text))
    return sorted(tokens)


def technical_tokens(value: object) -> list[str]:
    """Return normalized technical/model tokens, retaining duplicates."""

    output: list[str] = []
    for match in _TECH_TOKEN.finditer(str(value or "")):
        token = re.sub(r"[\s-]+", "-", match.group().upper())
        if token not in _TECH_EXCLUDED:
            output.append(token)
    return sorted(output)


def numeric_fact_counters(source: object, prediction: object) -> tuple[Counter[str], Counter[str]]:
    """Return aligned numeric fact counters for one Spanish field and Chinese output.

    Explicit digits are always compared exactly. The only non-digit
    equivalence is a source ``un/una`` followed by a known physical
    container/unit and a matching Chinese ``一/1`` plus the mapped measure.
    A generic article, or ``1`` with a different measure, remains a numeric
    hallucination. This function does not rewrite either value.
    """

    source_text = str(source or "")
    output_text = str(prediction or "")
    expected = Counter(numeric_tokens(source_text))
    actual = Counter(numeric_tokens(output_text))
    source_nouns = Counter(match.group("noun").casefold() for match in SPANISH_SINGLE_QUANTITY.finditer(source_text))
    for noun, source_count in source_nouns.items():
        measures = SPANISH_QUANTITY_TO_CHINESE_MEASURES[noun]
        measure_pattern = "|".join(re.escape(measure) for measure in measures)
        arabic = len(re.findall(rf"(?<!\d)1\s*(?:{measure_pattern})", output_text))
        chinese = len(re.findall(rf"一\s*(?:{measure_pattern})", output_text))
        aligned_arabic = min(source_count, arabic)
        aligned_chinese = min(source_count - aligned_arabic, chinese)
        expected["1"] += aligned_arabic + aligned_chinese
        # Arabic ``1`` is already in ``actual``. Add only Chinese one.
        actual["1"] += aligned_chinese
    return expected, actual


def _without_brands(value: str, allowed_brand_phrases: Iterable[str]) -> str:
    result = value
    for phrase in sorted((str(item).strip() for item in allowed_brand_phrases if str(item).strip()), key=len, reverse=True):
        result = re.sub(re.escape(phrase), " ", result, flags=re.IGNORECASE)
    return result


def _has_spanish_residual(value: str, allowed_brand_phrases: Iterable[str]) -> bool:
    cleaned = _without_brands(value, allowed_brand_phrases)
    return any(token.casefold() in SPANISH_TOKENS for token in TOKEN.findall(cleaned))


def _has_english_residual(value: str, allowed_brand_phrases: Iterable[str]) -> bool:
    cleaned = _without_brands(value, allowed_brand_phrases)
    # Upper-case acronyms (USB/LED/PEFC) and mixed technical model tokens are
    # valid in Chinese exports; ordinary lower-case prose is not.
    for match in TOKEN.finditer(cleaned):
        token = match.group()
        if _is_technical_token(cleaned, match.start(), match.end(), token):
            continue
        if token.casefold() in ENGLISH_TOKENS:
            return True
    return False


def validate_model_output(
    source: Mapping[str, object],
    prediction: Mapping[str, object] | None,
    *,
    expected_fields: Iterable[str] | None = None,
    allowed_brand_phrases: Iterable[str] = (),
) -> ModelOutputCheck:
    """Validate a model result against same-field Spanish source facts.

    A number in ``details`` is never allowed to appear in ``description``:
    comparison is per field, so an extra token in the latter is rejected even
    when that number exists elsewhere in the source object.  The function does
    not fill missing text or rewrite values.
    """

    fields = tuple(expected_fields or source.keys())
    if prediction is None:
        return ModelOutputCheck(False, ("JSON_PARSE",), {})
    if set(prediction) != set(fields) or any(not isinstance(prediction.get(field), str) for field in fields):
        return ModelOutputCheck(False, ("SCHEMA",), {})

    by_field: dict[str, tuple[str, ...]] = {}
    for field in fields:
        reasons: list[str] = []
        source_value = str(source.get(field, "") or "")
        predicted_value = str(prediction.get(field, "") or "")
        if source_value.strip() and not predicted_value.strip():
            reasons.append("EMPTY_REQUIRED_FIELD")
        expected, actual = numeric_fact_counters(source_value, predicted_value)
        if list((expected - actual).elements()):
            reasons.append("NUMERIC_DROPPED")
        if list((actual - expected).elements()):
            reasons.append("NUMERIC_HALLUCINATED")
        expected_units = Counter(unit_tokens(source_value))
        actual_units = Counter(unit_tokens(predicted_value))
        if list((expected_units - actual_units).elements()):
            reasons.append("UNIT_DROPPED")
        if list((actual_units - expected_units).elements()):
            reasons.append("UNIT_HALLUCINATED")
        expected_technical = Counter(technical_tokens(source_value))
        actual_technical = Counter(technical_tokens(predicted_value))
        if list((expected_technical - actual_technical).elements()):
            reasons.append("TECH_TOKEN_DROPPED")
        if list((actual_technical - expected_technical).elements()):
            reasons.append("TECH_TOKEN_HALLUCINATED")
        if field == "cat1" and predicted_value not in FIXED_CAT1:
            reasons.append("INVALID_CATEGORY")
        if field == "cat2" and predicted_value and not _CJK.search(predicted_value):
            reasons.append("INVALID_CATEGORY")
        if _has_spanish_residual(predicted_value, allowed_brand_phrases):
            reasons.append("SPANISH_RESIDUAL")
        if _has_english_residual(predicted_value, allowed_brand_phrases):
            reasons.append("ENGLISH_RESIDUAL")
        if reasons:
            by_field[field] = tuple(reasons)

    reasons = tuple(sorted({reason for field_reasons in by_field.values() for reason in field_reasons}))
    return ModelOutputCheck(not reasons, reasons, by_field)


__all__ = [
    "ModelOutputCheck",
    "numeric_fact_counters",
    "numeric_tokens",
    "technical_tokens",
    "unit_tokens",
    "validate_model_output",
]

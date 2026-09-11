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

# These are ordinary Spanish words that should not survive in a Chinese
# field.  Brand/model phrases are removed by ``allowed_brand_phrases`` first;
# this keeps a confirmed brand such as ``La Sonata`` legal while still
# catching a Spanish colour such as ``Antracita``.
SPANISH_TOKENS = {
    "el", "la", "los", "las", "para", "con", "sin", "del", "de", "y", "en",
    "color", "tamaño", "producto", "material", "cantidad", "contenido", "piezas",
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
        expected = Counter(numeric_tokens(source.get(field, "")))
        actual = Counter(numeric_tokens(prediction.get(field, "")))
        if list((expected - actual).elements()):
            reasons.append("NUMERIC_DROPPED")
        if list((actual - expected).elements()):
            reasons.append("NUMERIC_HALLUCINATED")
        if _has_spanish_residual(str(prediction.get(field, "")), allowed_brand_phrases):
            reasons.append("SPANISH_RESIDUAL")
        if _has_english_residual(str(prediction.get(field, "")), allowed_brand_phrases):
            reasons.append("ENGLISH_RESIDUAL")
        if reasons:
            by_field[field] = tuple(reasons)

    reasons = tuple(sorted({reason for field_reasons in by_field.values() for reason in field_reasons}))
    return ModelOutputCheck(not reasons, reasons, by_field)


__all__ = ["ModelOutputCheck", "numeric_tokens", "validate_model_output"]

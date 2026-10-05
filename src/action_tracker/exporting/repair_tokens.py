"""Source-bound token normalization for the Chinese export repair boundary.

The source catalogue uses several equivalent spellings for the same technical
fact (``PZ 2``/``PZ2``, ``2100 mAh``/``2100mAh``).  This module keeps those
equivalences local to the export repair and audit path; it does not weaken the
translation protection rules globally.
"""
from __future__ import annotations

import re
from typing import Iterable


_MODEL_WITH_DIGITS_RE = re.compile(
    r"(?<![A-Za-z0-9])([A-Z]{1,8})\s*[- ]?\s*(\d{1,8})(?![A-Za-z0-9])",
)


def canonical_fact_token(value: str | None) -> str:
    """Return a conservative comparison form for a technical token."""
    text = str(value or "").casefold().strip()
    text = text.replace("–", "-").replace("—", "-").replace("×", "x")
    # Decimal commas and thousands separators are formatting differences, not
    # different product facts (``1,5 V``/``1.5V`` and ``10.000 mAh``/``10000mAh``).
    text = re.sub(r"(?<=\d),(?=\d)", ".", text)
    text = re.sub(r"(?<=\d)\.(?=\d{3}(?:\D|$))", "", text)
    return re.sub(r"[\s\-_/]+", "", text)


def source_model_tokens(source: str | None) -> tuple[str, ...]:
    """Return source-visible model tokens, including spaced model forms."""
    text = str(source or "")
    found: list[str] = []
    for match in _MODEL_WITH_DIGITS_RE.finditer(text):
        # A spaced one-letter prefix is usually a measurement or a nutrient
        # name (``Vitamina D 10 mcg``), not a model.  Keep compact forms such
        # as ``A4``/``K2`` and multi-letter forms such as ``PZ 2``.
        if len(match.group(1)) == 1 and re.search(r"\s", match.group(0)):
            continue
        token = f"{match.group(1)}{match.group(2)}"
        if token.casefold() not in {item.casefold() for item in found}:
            found.append(token)
    return tuple(found)


def token_is_preserved(source_token: str, target: str | None) -> bool:
    """Check whether a target keeps a source token with formatting changes."""
    compact_source = canonical_fact_token(source_token)
    compact_target = canonical_fact_token(target)
    return bool(compact_source and compact_source in compact_target)


def repair_source_model_fragments(value: str | None, source: str | None) -> str | None:
    """Restore a model when a legacy translation kept only its numeric suffix.

    Examples: ``参数：4`` + source ``A4`` becomes ``参数：A4`` and
    ``清洁剂｜27`` + source ``GS27`` becomes ``清洁剂｜GS27``.  The rule is
    intentionally limited to an explicit label/separator so ordinary numbers
    are never guessed as model identifiers.
    """
    text = "" if value is None else str(value).strip()
    source_text = str(source or "")
    if not text or not source_text:
        return text or value
    for token in source_model_tokens(source_text):
        prefix = re.match(r"[A-Za-z]+", token)
        suffix = re.search(r"\d+$", token)
        if not prefix or not suffix or token_is_preserved(token, text):
            continue
        number = suffix.group(0)
        # Keep the existing Chinese wording and replace only the isolated
        # suffix produced by the stale translation.
        patterns = (
            (rf"((?:参数|型号)\s*[:：]\s*){re.escape(number)}\b", rf"\g<1>{token}"),
            (rf"([｜|；;]\s*){re.escape(number)}\b", rf"\g<1>{token}"),
        )
        for pattern, replacement in patterns:
            updated, count = re.subn(pattern, replacement, text, count=1, flags=re.IGNORECASE)
            if count:
                text = updated
                break
    return text


def numeric_token_is_embedded_in_model(source: str | None, number: str) -> bool:
    """Return true for values such as ``3M`` where ``M`` is not a unit."""
    value = str(source or "")
    return bool(re.search(rf"(?<![A-Za-z0-9]){re.escape(str(number))}[A-Z](?![A-Za-z0-9])", value))


def source_model_token_set(source: str | None) -> set[str]:
    return {canonical_fact_token(token) for token in source_model_tokens(source)}

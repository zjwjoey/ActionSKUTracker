"""Versioned deterministic rules used by the export repair engine.

The legacy rule bodies remain private during the migration so the established
source-bound behavior is preserved.  Export code calls this module's public
rule names only; future rule changes are made here and carry ``RULE_VERSION``.
"""
from __future__ import annotations

from typing import Any
import re

from .repair_tokens import repair_source_model_fragments


RULE_VERSION = "1.0"
_ORDINARY_SOURCE_TOKENS = {
    "PARA", "FONO", "MESA", "SOPORTE", "AUXILIAR", "TEL", "DEL", "COCHES", "AGUA",
    "LIMPIEZA", "BOTELLA", "PRODUCTO", "PINTURA", "CALIENTE", "MADERA", "ELECTR",
    "BASE", "CANDADO", "JUGUETE", "PILAS", "COLOR", "MATERIAL", "TIPO", "DIFERENTES",
    "VARIANTES", "COLORES", "UNIDADES", "CONTENIDO", "INCLUYE", "CALENTADOR",
}
_TOKEN_ALIASES = {
    "mdf": ("中密度纤维板", "纤维板"), "uv": ("紫外线",), "xl": ("加大", "超大", "特大"),
    "co2": ("二氧化碳", "碳中和"), "wc": ("马桶", "洁厕"), "gsm": ("克重", "克/平方米"),
    "a4": ("A4", "A4纸"), "b5": ("B5", "B5纸"), "usb-c": ("Type-C", "C型接口"),
    "usb-a": ("Type-A", "A型接口"),
}


def repair_unit_price(value: str | None, source: str | None = None) -> str | None:
    from .dictionary_join import _repair_export_unit_price
    return _repair_export_unit_price(value)


def repair_title(value: str | None, source: str | None) -> str | None:
    from .dictionary_join import _repair_export_title
    return repair_source_model_fragments(_repair_export_title(value, source), source)


def repair_title_with_context(value: str | None, source: str | None, *, excluded_tokens: set[str] = ()) -> str | None:
    text = repair_title(value, source)
    source_text = str(source or "")
    if not text or not source_text:
        return text
    excluded = {str(token).casefold() for token in excluded_tokens}
    for token in dict.fromkeys(re.findall(r"(?<![A-Za-z0-9])[A-Z][A-Z0-9-]{2,}(?![A-Za-z0-9])", source_text)):
        if token.upper() in _ORDINARY_SOURCE_TOKENS or token.casefold() in excluded:
            continue
        if token.casefold() in str(text).casefold():
            continue
        aliases = _TOKEN_ALIASES.get(token.casefold(), ())
        if any(alias.casefold() in str(text).casefold() for alias in aliases):
            continue
        text = f"{text}｜{token}"
    return text


def repair_spec(value: str | None, source: str | None, *, force_source_facts: bool = False) -> str | None:
    from .dictionary_join import _repair_export_spec
    repaired = _repair_export_spec(value, source, force_source_facts=force_source_facts)
    return repair_source_model_fragments(repaired, source)


def repair_description(value: str | None, source: str | None = None) -> str | None:
    from .dictionary_join import _repair_export_description
    return repair_source_model_fragments(_repair_export_description(value, source), source)


def repair_content_with_context(
    value: str | None, source: str | None = None, *,
    excluded_tokens: set[str] = (),
) -> str | None:
    text = repair_description(value, source)
    text = repair_source_model_fragments(text, source)
    source_text = str(source or "")
    if not text or not source_text:
        return text
    excluded = {str(token).casefold() for token in excluded_tokens}
    target = str(text).casefold()
    for token in dict.fromkeys(re.findall(r"(?<![A-Za-z0-9])[A-Z][A-Z0-9-]{2,}(?![A-Za-z0-9])", source_text)):
        if token.upper() in _ORDINARY_SOURCE_TOKENS or token.casefold() in excluded:
            continue
        if token.casefold() in target:
            continue
        aliases = _TOKEN_ALIASES.get(token.casefold(), ())
        if any(alias.casefold() in target for alias in aliases):
            continue
        text = f"{text}；{token}"
        target = text.casefold()
    return text


def repair_details(value: str | None, source: str | None) -> str | None:
    from .dictionary_join import _repair_export_details
    return repair_source_model_fragments(_repair_export_details(value, source), source)


def repair_details_with_context(
    value: str | None, source: str | None = None, *,
    excluded_tokens: set[str] = (),
) -> str | None:
    text = repair_details(value, source)
    text = repair_source_model_fragments(text, source)
    source_text = str(source or "")
    if not text or not source_text:
        return text
    excluded = {str(token).casefold() for token in excluded_tokens}
    target = str(text).casefold()
    for token in dict.fromkeys(re.findall(r"(?<![A-Za-z0-9])[A-Z][A-Z0-9-]{2,}(?![A-Za-z0-9])", source_text)):
        if token.upper() in _ORDINARY_SOURCE_TOKENS or token.casefold() in excluded:
            continue
        if token.casefold() in target:
            continue
        aliases = _TOKEN_ALIASES.get(token.casefold(), ())
        if any(alias.casefold() in target for alias in aliases):
            continue
        text = f"{text}；{token}"
        target = text.casefold()
    return text


def repair_categories(value: Any, source: str | None) -> Any:
    # Category values are already resolved through the reviewed category
    # dictionary.  This explicit rule is kept as a no-op so category changes
    # are still recorded by the same repair boundary.
    return value

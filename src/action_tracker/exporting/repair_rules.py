"""Versioned deterministic rules used by the export repair engine.

The legacy rule bodies remain private during the migration so the established
source-bound behavior is preserved.  Export code calls this module's public
rule names only; future rule changes are made here and carry ``RULE_VERSION``.
"""
from __future__ import annotations


def clean_review_notes(value, *, remarks=False, language="zh"):
    """Remove bounded internal delivery annotations, preserving ordinary facts.

    This is a display projection only. Callers must retain before/after evidence
    and audit the resulting Chinese fields; removing a placeholder is not approval.
    """
    import re
    if not isinstance(value, str) or not value:
        return value
    if not remarks and re.match(r"^\s*(?:待复核|待审核|待人工核验|待核验|待确认)\s*[:：]", value):
        return None
    internal = re.compile(
        r"^\s*【?(?:原有来源待核|详情待核|复核(?:[·：:]|】)|来源异常|原文异常|来源物态异常|物态来源异常|"
        r"来源冲突|来源待核|详情待核|来源不确定|未发布旧中文|核对依据|表内已修正|字段已修|"
        r"(?:中文品名|中文分类[12]|中文规格|中文单价|中文描述|中文产品详情)待审核)", re.I)
    spanish = re.compile(r"^\s*(?:Revisar texto de origen|pendiente de (?:revisi[oó]n|verificaci[oó]n)|no disponible en el registro fuente)\b", re.I)
    continuation = re.compile(r"^\s*(?:完整异常原文已保留|不得据此推断)")
    review_block = re.compile(
        r"待核|待审核|来源异常|来源冲突|复核官方来源|"
        r"(?:来源|字段|译文|描述|详情|规格|品名|类目|原文).{0,80}复核|"
        r"【(?:待复核|[^】]{0,20}(?:复核|待确认|缺源|西语原文))|"
        r"^(?:西语原文[:：]|不将品牌符号|不晋升字典|原西语类目保持|"
        r"纸叶与单双面页数的来源口径|标准中文与准确纤维材质|旧.*表述已修正)")
    business_note = re.compile(
        r"^\s*(?:(?:在售|不在售|新品|促销|可持续)(?:$|[:：])|在售状态[:：]|官网官方标签[:：]|"
        r"折扣[:：]|警告[:：]|注意[:：]|不得用于|材质[:：]|颜色[:：]|型号[:：]|数量[:：])")
    parts = re.split(r"([;；|｜\r\n]+)", value)
    kept = []; delimiter = ""; removed = False; following_note = False; in_review_block = False
    for i, part in enumerate(parts):
        if i % 2:
            delimiter = part
            continue
        is_note = bool(internal.match(part)) if language == "zh" else bool(spanish.match(part))
        if remarks and language == "zh":
            if business_note.match(part.strip()):
                in_review_block = False
            elif is_note or review_block.search(part.strip()) or in_review_block:
                in_review_block = True
                is_note = True
        if (remarks or language == "zh") and (is_note or (following_note and continuation.match(part))):
            removed = following_note = True
            continue
        if part.strip():
            kept.append((delimiter if kept else "") + part)
            following_note = False
    return ("".join(kept).strip() or None) if removed else value

from typing import Any
import re

from .repair_tokens import repair_source_model_fragments, source_model_tokens, token_is_preserved


RULE_VERSION = "1.0"
_ORDINARY_SOURCE_TOKENS = {
    "PARA", "FONO", "MESA", "SOPORTE", "AUXILIAR", "TEL", "DEL", "COCHES", "AGUA",
    "LIMPIEZA", "BOTELLA", "PRODUCTO", "PINTURA", "CALIENTE", "MADERA", "ELECTR",
    "BASE", "CANDADO", "JUGUETE", "PILAS", "COLOR", "MATERIAL", "TIPO", "DIFERENTES",
    "VARIANTES", "COLORES", "UNIDADES", "CONTENIDO", "INCLUYE", "CALENTADOR",
    # Uppercase fragments emitted by the legacy detail parser.  They are
    # ordinary Spanish labels/values, not models or certifications.
    "APARATO", "CALCULADORA", "CALEFACCI", "CÁMPING", "EDRED", "MANOS",
    "MICO", "MPING", "PERCHERO", "QU", "QUÍMICO", "SOMBREROS", "TABURETE",
}
_TOKEN_ALIASES = {
    "mdf": ("中密度纤维板", "纤维板"), "uv": ("紫外线",), "xl": ("加大", "超大", "特大"),
    "co2": ("二氧化碳", "碳中和"), "wc": ("马桶", "洁厕"), "gsm": ("克重", "克/平方米"),
    "a4": ("A4", "A4纸"), "b5": ("B5", "B5纸"), "usb-c": ("Type-C", "C型接口"),
    "usb-a": ("Type-A", "A型接口"),
}
_LEGACY_DESCRIPTION_TAILS = {
    "glp", "lpg", "lux", "tcx", "gs4", "hss", "fab", "elle5", "tv9",
    "ubs-c", "agradables", "family", "tv10",
}


def _strip_legacy_description_tail(value: str | None) -> str | None:
    text = "" if value is None else str(value).strip()
    if not text:
        return value
    match = re.search(r"(?:[；;|｜]\s*)([A-Za-z][A-Za-z0-9²³+./-]*)\s*$", text)
    if not match:
        return text
    token = match.group(1).casefold()
    if token not in _LEGACY_DESCRIPTION_TAILS and not re.fullmatch(r"(?:pet|uv|gsm|tv|gs|elle)\d+", token):
        return text
    return text[:match.start()].rstrip("；;|｜ ")


def _strip_detail_after_article_number(value: str | None) -> str | None:
    text = "" if value is None else str(value).strip()
    if not text:
        return value
    marker = re.search(r"商品编号\s*[:：]\s*\d+", text)
    if not marker:
        return text
    suffix = text[marker.end():].lstrip("；;|｜ ").strip()
    if suffix.startswith("来源异常：官网字段"):
        return text[:marker.end()] + "；" + suffix
    return text[:marker.end()].rstrip("；;|｜ ")


def repair_unit_price(value: str | None, source: str | None = None) -> str | None:
    from .dictionary_join import _repair_export_unit_price
    return _repair_export_unit_price(value)


def repair_title(value: str | None, source: str | None) -> str | None:
    from .dictionary_join import _repair_export_title
    return _remove_brand_numeric_fragments(
        repair_source_model_fragments(_repair_export_title(value, source), source), source
    )


_TECHNICAL_ALNUM_PREFIXES = {
    "a", "b", "cr", "f", "h", "ip", "k", "lr", "ps", "r", "t", "usb",
}


def _remove_brand_numeric_fragments(value: str | None, source: str | None) -> str | None:
    """Remove numeric suffixes left after a no-brand title cleanup.

    ``Lab31``/``Cool2Party`` are brand-shaped source spans.  A legacy title
    projection can leave ``｜31``/``｜2`` after removing the brand.  Model and
    interface tokens such as ``A4``, ``F48`` and ``PS4`` are retained.
    """
    text = "" if value is None else str(value).strip()
    source_text = str(source or "")
    if not text or not source_text:
        return value
    for token in dict.fromkeys(re.findall(r"(?<![A-Za-z0-9])[A-Za-z][A-Za-z0-9&+./-]*\d[A-Za-z0-9&+./-]*(?![A-Za-z0-9])", source_text)):
        lower = token.casefold()
        if not any(ch.islower() for ch in token):
            continue
        prefix = re.match(r"[A-Za-z]+", token)
        if prefix and prefix.group(0).casefold() in _TECHNICAL_ALNUM_PREFIXES:
            continue
        if lower.startswith(("series", "modelo", "model")):
            continue
        # Remove the complete copied brand token first; then remove an
        # isolated numeric suffix if the model-repair pass already restored
        # only the token's alphanumeric part.
        text = re.sub(rf"(?i)(?<![A-Za-z0-9]){re.escape(token)}(?![A-Za-z0-9])", "", text)
        digits = re.findall(r"\d+", token)
        for number in digits:
            text = re.sub(rf"(?:[｜|；;、,，]\s*){re.escape(number)}(?=\s*(?:[｜|；;、,，]|$))", "", text)
            text = re.sub(rf"(?<!\d){re.escape(number)}(?=\s*(?:[｜|；;、,，]|$))", "", text)
    text = re.sub(r"[｜|；;、,，]\s*(?=[｜|；;、,，]|$)", "", text)
    text = re.sub(r"\s{2,}", " ", text).strip("｜|；;、,， ")
    return text


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
    return _strip_legacy_description_tail(
        repair_source_model_fragments(_repair_export_description(value, source), source)
    )


def repair_content_with_context(
    value: str | None, source: str | None = None, *,
    excluded_tokens: set[str] = (),
) -> str | None:
    text = repair_description(value, source)
    text = repair_source_model_fragments(text, source)
    source_text = str(source or "")
    if not text or not source_text:
        return text
    # Short source models such as A4/B5 are intentionally below the generic
    # uppercase-token threshold. Preserve them in descriptions when the
    # legacy candidate dropped them; they remain source-bound facts rather
    # than inferred numbers.
    for model in source_model_tokens(source_text):
        if not token_is_preserved(model, text):
            text = f"{text}；{model}"
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
    # Lowercase source descriptions still carry technical facts that the
    # uppercase-token pass cannot see. Keep these two common interfaces when
    # the Chinese sentence did not already render an accepted equivalent.
    for token, pattern, aliases in (
        ("LED", r"\bled\b", ("led", "发光二极管")),
        ("PC", r"\bpc\b", ("pc", "电脑")),
    ):
        if re.search(pattern, source_text, flags=re.I) and not any(alias.casefold() in target for alias in aliases):
            text = f"{text}；{token}"
            target = text.casefold()
    # NO_BRAND is a title policy. Body identities are source facts; removing
    # dictionary tokens here can even truncate a complete name (Capri-Sun).
    return _strip_legacy_description_tail(text)


def repair_details(value: str | None, source: str | None) -> str | None:
    from .dictionary_join import _repair_export_details
    return _strip_detail_after_article_number(
        repair_source_model_fragments(_repair_export_details(value, source), source)
    )


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
    return _strip_detail_after_article_number(text)


def repair_categories(value: Any, source: str | None) -> Any:
    # Category values are already resolved through the reviewed category
    # dictionary.  This explicit rule is kept as a no-op so category changes
    # are still recorded by the same repair boundary.
    return value

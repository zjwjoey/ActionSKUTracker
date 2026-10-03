"""Lossless key/value parsing for official product details."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class StructuredDetail:
    key: str
    value: str
    source_text: str
    context_key: str
    value_type: str = "TEXT"

    def as_dict(self) -> dict[str, str]:
        return {"key": self.key, "value": self.value, "source_text": self.source_text, "context_key": self.context_key, "value_type": self.value_type}


_KEY_MAP = {
    "material": ("material", "TEXT"), "材质": ("material", "TEXT"), "color": ("color", "TEXT"), "颜色": ("color", "TEXT"), "tamaño": ("size", "DIMENSION"),
    "tamano": ("size", "DIMENSION"), "peso": ("weight", "WEIGHT"), "cantidad": ("quantity", "QUANTITY"),
    "contenido": ("content", "QUANTITY"), "potencia": ("power", "POWER"), "voltaje": ("voltage", "VOLTAGE"),
    "número del artículo": ("article_number", "IDENTIFIER"), "numero del articulo": ("article_number", "IDENTIFIER"),
    "tipo": ("type", "TEXT"), "longitud del cable": ("cable_length", "DIMENSION"),
}


def _type_for(key: str, value: str) -> tuple[str, str]:
    normalized = " ".join(key.casefold().split())
    if normalized in _KEY_MAP:
        return _KEY_MAP[normalized]
    value = str(value or "")
    if re.search(r"\b(?:cm|mm|m|pulgadas?)\b", value, re.I):
        return normalized, "DIMENSION"
    if re.search(r"\b(?:g|kg|ml|l|gramos?|litros?)\b", value, re.I):
        return normalized, "MEASURE"
    return normalized, "TEXT"


def parse_structured_details(text: str) -> tuple[StructuredDetail, ...]:
    """Parse semicolon/newline-delimited ``key: value`` pairs.

    The split is deliberately conservative: a colon inside a value is kept,
    and a duplicated colon in a source key is normalized without dropping
    either the key or value.
    """
    raw = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    parts = [item.strip() for item in re.split(r"[;\n]+", raw) if item.strip()]
    result: list[StructuredDetail] = []
    for part in parts:
        match = re.match(r"^\s*([^:：]+?)\s*[:：]{1,2}\s*(.*?)\s*$", part)
        if not match:
            continue
        key, value = match.group(1).strip(), match.group(2).strip()
        context_key, value_type = _type_for(key, value)
        result.append(StructuredDetail(key, value, part, context_key, value_type))
    return tuple(result)


def detail_contexts(text: str) -> tuple[str, ...]:
    return tuple(item.context_key for item in parse_structured_details(text))


def iter_detail_pairs(text: str) -> Iterable[tuple[str, str]]:
    for item in parse_structured_details(text):
        yield item.key, item.value

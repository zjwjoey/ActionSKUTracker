from __future__ import annotations

import re

POLICY_VERSION = "CHINESE_LOCALIZATION_STANDARD_V1"
FIXED_CAT1 = (
    "DIY五金", "办公文具", "宠物用品", "厨房餐具", "服饰鞋包", "个人美容",
    "家居布置", "家务清洁", "旅行用品", "食品饮料", "数码影音", "玩具",
    "兴趣手作", "园艺户外", "运动用品",
)
_SPANISH_WORDS = re.compile(r"\b(?:para|con|sin|varios?|varias?|diferentes?|unidades?|colores?|negro|blanco|rojo|azul|verde|de|del|la|el|y|o|en|tipo|tamaño|material|contenido|cantidad|incluye|lavable|resistente)\b", re.I)
_LATIN = re.compile(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]")
_TECH = re.compile(r"^(?:USB(?:-[A-Z])?|LED|LCD|DIY|FSC|E27|A\d+|D\d+|XL?|[A-Z]{1,6}\d{2,}[A-Z0-9-]*|[A-Z]{2,6}|\d+(?:mg|mcg|mAh|V|W|D))$")


def has_ordinary_spanish(value: str, *, allowed_tokens: set[str] | None = None) -> bool:
    allowed = {t.lower() for t in (allowed_tokens or set())}
    for token in _LATIN.findall(value or ""):
        _ = token
    for word in re.findall(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+", value or ""):
        if word.lower() in allowed or _TECH.match(word):
            continue
        if _SPANISH_WORDS.search(word) or len(word) > 2:
            return True
    return False


def classify_latin_token(token: str, *, known_tokens: set[str] | None = None) -> str:
    if token in (known_tokens or set()):
        return "BRAND_TOKEN"
    if _TECH.match(token):
        return "TECH_TOKEN"
    if re.fullmatch(r"[A-Z0-9][A-Z0-9-]{1,15}", token):
        return "MODEL_TOKEN"
    return "ORDINARY_SPANISH"


def map_cat1(value: str, mappings: dict[str, str] | None = None) -> str:
    if value in FIXED_CAT1:
        return value
    mapped = (mappings or {}).get(value, "")
    # 个人美容 is the sole canonical C06 label.  Older configs may still
    # expose the historical alias 个人护理; normalize it at the boundary.
    return "个人美容" if mapped in {"个人美容", "个人护理"} else mapped

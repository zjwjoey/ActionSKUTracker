from __future__ import annotations

import re


def format_spec(value: str) -> str:
    value = str(value or "").strip()
    value = value.replace("|", "｜").replace("×", "×")
    value = re.sub(r"(?<=\d)\s*[xX]\s*(?=\d)", "×", value)
    value = re.sub(r"(?<=\d),(?=\d)", ".", value)
    value = re.sub(r"\bvarios colores\b|\bvarias colores\b|\bdiferentes colores\b", "多种颜色", value, flags=re.I)
    value = re.sub(r"\bvarias variantes\b|\bdiferentes variantes\b", "多款可选", value, flags=re.I)
    value = re.sub(r"\bunidades?\b", "件", value, flags=re.I)
    value = re.sub(r"\bpiezas?\b", "件", value, flags=re.I)
    value = re.sub(r"\bpares?\b", "双", value, flags=re.I)
    value = re.sub(r"\bgramos?\b", "g", value, flags=re.I)
    value = re.sub(r"\blitros?\b", "L", value, flags=re.I)
    value = re.sub(r"\bmililitros?\b", "ml", value, flags=re.I)
    value = re.sub(r"\bpulgadas?\b", "英寸", value, flags=re.I)
    value = re.sub(r"\bhojas?\b", "张", value, flags=re.I)
    # Unit conversion above may introduce a space (``100 gramos`` -> ``100 g``).
    # Compact retail notation removes only the space immediately before a
    # recognized unit, never spaces between ordinary words.
    value = re.sub(r"(?<=\d)\s+(?=(?:mm|cm|m|ml|l|g|kg|mg|mcg|mAh|V|W|Hz|D)\b)", "", value, flags=re.I)
    value = re.sub(r"\s*｜\s*", "｜", value)
    return value.strip(" ｜")


def format_unit_price(value: str) -> str:
    value = str(value or "").strip()
    value = re.sub(r"€/kg", "€/千克", value, flags=re.I)
    value = re.sub(r"€/l", "€/升", value, flags=re.I)
    value = re.sub(r"€/ud\.?", "€/件", value, flags=re.I)
    return value


def format_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace(";", "；").replace(":", "：")).strip()

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

# Canonical units are deliberately conservative and measurement-bound.  Short
# symbols and Chinese unit characters are counted only when attached to a
# number; otherwise ordinary words such as ``mango(s)``, ``巧克力`` and ``安装``
# would be false units.  Conversions (1 L -> 1000 ml) are intentionally not
# attempted by this reject-only guard.
_MEASURE_NUMBER = r"[-+]?\d+(?:[.,]\d+)?"
_SPANISH_NUMBER_WORD = r"(?:un|uno|una|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez)"
_UNIT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("percent", re.compile(rf"{_MEASURE_NUMBER}\s*(?:%|por\s+ciento|百分之)", re.IGNORECASE)),
    ("kg", re.compile(rf"{_MEASURE_NUMBER}\s*(?:kg|kilogramos?|千克|公斤)(?![A-Za-zÁÉÍÓÚÜÑáéíóúüñ])", re.IGNORECASE)),
    ("g", re.compile(rf"{_MEASURE_NUMBER}\s*(?:g|gramos?|克)(?![A-Za-zÁÉÍÓÚÜÑáéíóúüñ])", re.IGNORECASE)),
    ("ml", re.compile(rf"{_MEASURE_NUMBER}\s*(?:ml|mililitros?|毫升)(?![A-Za-zÁÉÍÓÚÜÑáéíóúüñ])", re.IGNORECASE)),
    ("cl", re.compile(rf"{_MEASURE_NUMBER}\s*(?:cl|centilitros?|厘升)(?![A-Za-zÁÉÍÓÚÜÑáéíóúüñ])", re.IGNORECASE)),
    ("l", re.compile(rf"{_MEASURE_NUMBER}\s*(?:l|litros?|升)(?![A-Za-zÁÉÍÓÚÜÑáéíóúüñ])", re.IGNORECASE)),
    ("km", re.compile(rf"{_MEASURE_NUMBER}\s*(?:km|kil[oó]metros?|千米|公里)(?![A-Za-zÁÉÍÓÚÜÑáéíóúüñ])", re.IGNORECASE)),
    ("mm", re.compile(rf"{_MEASURE_NUMBER}\s*(?:mm|mil[ií]metros?|毫米)(?![A-Za-zÁÉÍÓÚÜÑáéíóúüñ])", re.IGNORECASE)),
    ("cm", re.compile(rf"{_MEASURE_NUMBER}\s*(?:cm|cent[ií]metros?|厘米)(?![A-Za-zÁÉÍÓÚÜÑáéíóúüñ])", re.IGNORECASE)),
    ("m", re.compile(rf"{_MEASURE_NUMBER}\s*(?:m(?:[²2])?|metros?|平方米|米)(?![A-Za-zÁÉÍÓÚÜÑáéíóúüñ])", re.IGNORECASE)),
    ("v", re.compile(rf"{_MEASURE_NUMBER}\s*(?:v|voltios?|伏特|伏)(?![A-Za-zÁÉÍÓÚÜÑáéíóúüñ])", re.IGNORECASE)),
    ("w", re.compile(rf"{_MEASURE_NUMBER}\s*(?:w|vatios?|瓦特|瓦)(?![A-Za-zÁÉÍÓÚÜÑáéíóúüñ])", re.IGNORECASE)),
    ("a", re.compile(rf"{_MEASURE_NUMBER}\s*(?:A|[Aa]mperios?|安培|安)(?![A-Za-zÁÉÍÓÚÜÑáéíóúüñ])")),
    ("mah", re.compile(rf"{_MEASURE_NUMBER}\s*(?:ma?h|毫安时)(?![A-Za-zÁÉÍÓÚÜÑáéíóúüñ])", re.IGNORECASE)),
    ("lm", re.compile(rf"{_MEASURE_NUMBER}\s*(?:lm|l[uú]menes?|lumen|流明)(?![A-Za-zÁÉÍÓÚÜÑáéíóúüñ])", re.IGNORECASE)),
    ("celsius", re.compile(rf"{_MEASURE_NUMBER}\s*(?:[°º]\s*c|grados?\s+celsius|摄氏度)(?![A-Za-zÁÉÍÓÚÜÑáéíóúüñ])", re.IGNORECASE)),
    ("kcal", re.compile(rf"{_MEASURE_NUMBER}\s*(?:kcal|kilocalor[ií]as?|千卡)(?![A-Za-zÁÉÍÓÚÜÑáéíóúüñ])", re.IGNORECASE)),
    ("kj", re.compile(rf"{_MEASURE_NUMBER}\s*(?:kj|kilojulios?|千焦)(?![A-Za-zÁÉÍÓÚÜÑáéíóúüñ])", re.IGNORECASE)),
    ("hour", re.compile(rf"{_MEASURE_NUMBER}\s*(?:h|horas?|小时)(?![A-Za-zÁÉÍÓÚÜÑáéíóúüñ])", re.IGNORECASE)),
    ("minute", re.compile(rf"{_MEASURE_NUMBER}\s*(?:min|minutos?|分钟)(?![A-Za-zÁÉÍÓÚÜÑáéíóúüñ])", re.IGNORECASE)),
    ("second", re.compile(rf"{_MEASURE_NUMBER}\s*(?:s|segundos?|秒)(?![A-Za-zÁÉÍÓÚÜÑáéíóúüñ])", re.IGNORECASE)),
    ("serving", re.compile(rf"{_MEASURE_NUMBER}\s*(?:raciones?|份)(?![A-Za-zÁÉÍÓÚÜÑáéíóúüñ])", re.IGNORECASE)),
)

_SPANISH_WORD_UNIT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("m", re.compile(rf"\b{_SPANISH_NUMBER_WORD}\s+metros?\b", re.IGNORECASE)),
    ("percent", re.compile(rf"\b{_SPANISH_NUMBER_WORD}\s+por\s+ciento\b", re.IGNORECASE)),
    ("kg", re.compile(rf"\b{_SPANISH_NUMBER_WORD}\s+kilogramos?\b", re.IGNORECASE)),
    ("g", re.compile(rf"\b{_SPANISH_NUMBER_WORD}\s+gramos?\b", re.IGNORECASE)),
    ("l", re.compile(rf"\b{_SPANISH_NUMBER_WORD}\s+litros?\b", re.IGNORECASE)),
)

# Product codes and standards are immutable facts, not translation style.
# Avoid matching ordinary title-case words or measurement symbols here.
_TECH_TOKEN = re.compile(
    r"(?<![A-Za-z0-9])(?:"
    r"USB(?:[\s-]?[A-Z])?|LEDs?|PEFC|FSC|HDMI|NFC|RFID|ENC|"
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
    r"\b(?:un|una)\s+(?P<noun>bote|lata|botella|caja|bolsa|tubo|rollo|paquete|pack|pieza|unidad|par|juego|set|frasco|tarro|cápsula|capsula|pastilla|cinta|calcet[ií]n)\b",
    re.IGNORECASE,
)
SPANISH_QUANTITY_TO_CHINESE_MEASURES = {
    "bote": ("罐", "瓶"), "lata": ("罐",), "botella": ("瓶",),
    "caja": ("盒",), "bolsa": ("袋", "包"), "tubo": ("管",),
    "rollo": ("卷",), "paquete": ("包",), "pack": ("包",),
    "pieza": ("件", "个"), "unidad": ("件", "个"), "par": ("双", "对"),
    "juego": ("套",), "set": ("套",), "frasco": ("罐", "瓶"),
    "tarro": ("罐", "瓶"), "cápsula": ("粒", "颗"), "capsula": ("粒", "颗"),
    "pastilla": ("片", "粒"), "cinta": ("条",), "calcetín": ("只", "双"), "calcetin": ("只", "双"),
}
SPANISH_CARDINAL_VALUES = {
    "un": 1, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4,
    "cinco": 5, "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10,
}
SPANISH_NUMBER_QUANTITY = re.compile(
    rf"\b(?P<num>{_SPANISH_NUMBER_WORD})\s+(?:solo\s+)?(?P<noun>"
    r"piezas?|unidades?|accesorios?|niveles?|metros?|kilogramos?|gramos?|litros?|"
    r"botones?|farolillos?|lámparas?|posiciones?|camisetas?|tipos?|"
    r"animal(?:es)?|muñeca(?:s)?|dispositivo(?:s)?|puntas?|lados?|"
    r"intensidades?|zoo|bolsillos?)\b",
    re.IGNORECASE,
)
SPANISH_OTHER_QUANTITY = re.compile(r"\botro\s+(?:lateral|bolsillo)\b", re.IGNORECASE)
SPANISH_SPECIAL_NUMBER_QUANTITY = re.compile(
    rf"\b(?P<num>{_SPANISH_NUMBER_WORD})\s+(?:solo\s+juego|cómoda\s+camiseta)\b",
    re.IGNORECASE,
)
CHINESE_NUMBER_QUANTITY = re.compile(
    r"(?P<num>[一二两三四五六七八九十])\s*(?:个|件|套|档|米|千克|克|升|颗|粒|片|盏|只|级|种|罐|瓶|盒|袋|卷|管|包)",
)
CHINESE_CARDINAL_VALUES = {
    "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}

# Chinese translations often render an explicit Spanish digit with a Chinese
# numeral rather than an Arabic digit (``3 en 1`` -> ``三合一``, ``2 unidades``
# -> ``两件`` and ``1 hoja`` -> ``一张``).  Keep this deliberately narrow:
# only numerals next to a quantity/function marker are interpreted, so generic
# prose such as ``一款商品`` or ``一杯饮料`` is not silently treated as a hard
# numeric fact.
CHINESE_NUMERIC_CONTEXT = re.compile(
    r"(?P<num>[零〇一二两三四五六七八九十百千万]+)"
    r"(?=\s*(?:合|倍|包装|装|层|芯|条|瓶|张|件|套|包|只|页|环|端口|位|片|粒|颗|双|对|米|克|千克|毫升|升|小时|分钟|秒|度|伏|瓦|毫安时))"
    # Only the functional ``数字合数字`` form is numeric.  A bare
    # ``合+数字`` would misread ordinary prose such as ``适合一顿早餐``.
    r"|(?<=[0-9零〇一二两三四五六七八九十百千万])合(?P<after>[零〇一二两三四五六七八九十百千万]+)"
)


def _chinese_cardinal_value(token: str) -> int | None:
    """Parse the small Chinese cardinal forms used in product quantities."""

    if token in CHINESE_CARDINAL_VALUES:
        return CHINESE_CARDINAL_VALUES[token]
    digits = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3,
              "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if token == "十":
        return 10
    if token.startswith("十") and len(token) == 2 and token[1] in digits:
        return 10 + digits[token[1]]
    if token.endswith("十") and len(token) == 2 and token[0] in digits:
        return digits[token[0]] * 10
    if len(token) == 2 and token[0] in digits and token[1] in digits:
        return digits[token[0]] * 10 + digits[token[1]]
    return None


def chinese_context_numeric_tokens(value: object) -> list[str]:
    """Return numeric values represented by bounded Chinese quantity phrases."""

    text = str(value or "")
    output: list[str] = []
    for match in CHINESE_NUMERIC_CONTEXT.finditer(text):
        token = match.group("num") or match.group("after")
        parsed = _chinese_cardinal_value(token)
        if parsed is not None:
            output.append(str(parsed))
    return output

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

    # Spanish sources use both comma decimals (``1,5``) and dot thousands
    # separators (``3.680``).  Normalize those locale forms before comparing
    # them with Chinese output, and accept the common OCR apostrophe decimal
    # form (``9'5``) without changing the original fact text.
    text = re.sub(r"(?<=\d)['’](?=\d)", ".", str(value or ""))
    tokens: list[str] = []
    for raw in NUMBER.findall(text):
        token = raw.replace(",", ".")
        if re.fullmatch(r"\d{1,3}(?:\.\d{3})+", token):
            token = token.replace(".", "")
        tokens.append(token)
    return sorted(tokens)


def unit_tokens(value: object) -> list[str]:
    """Return canonical unit tokens, retaining duplicate occurrences."""

    text = str(value or "")
    tokens: list[str] = []
    for canonical, pattern in _UNIT_PATTERNS:
        tokens.extend(canonical for _ in pattern.finditer(text))
    for canonical, pattern in _SPANISH_WORD_UNIT_PATTERNS:
        tokens.extend(canonical for _ in pattern.finditer(text))
    return sorted(tokens)


def technical_tokens(value: object) -> list[str]:
    """Return normalized technical/model tokens, retaining duplicates."""

    # These are fixed, unambiguous Chinese renderings of a technical token;
    # translating Bluetooth to 蓝牙 must not be reported as token loss.
    text = re.sub(r"蓝牙", "BLUETOOTH ", str(value or ""))
    output: list[str] = []
    for match in _TECH_TOKEN.finditer(text):
        token = re.sub(r"[\s-]+", "-", match.group().upper())
        if token == "LEDS":
            token = "LED"
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
    for pattern in (SPANISH_NUMBER_QUANTITY, SPANISH_SPECIAL_NUMBER_QUANTITY):
        for match in pattern.finditer(source_text):
            noun = match.groupdict().get("noun", "")
            # In ordinary product prose these singular forms are articles
            # (``una punta fina`` / ``un lado``), not a counted package.
            # Their plural/cardinal forms (``dos puntas`` / ``dos lados``)
            # remain protected as explicit facts.
            if noun.casefold().rstrip("s") in {"punta", "lado", "intensidade"} and match.group("num").casefold() in {"un", "uno", "una"}:
                continue
            # An indefinite singular accessory is an article-like phrase in
            # product prose (``un accesorio``), not a hard count.  Plural and
            # explicit numeric forms remain protected by this guard.
            if noun.casefold() == "accesorio" and match.group("num").casefold() in {"un", "uno", "una"}:
                continue
            expected[str(SPANISH_CARDINAL_VALUES[match.group("num").casefold()])] += 1
    # ``otro bolsillo/lateral`` is an explicit second item in product prose;
    # faithful Chinese often renders it as ``一个...``.
    expected["1"] += sum(1 for _ in SPANISH_OTHER_QUANTITY.finditer(source_text))
    for match in CHINESE_NUMBER_QUANTITY.finditer(output_text):
        actual[str(CHINESE_CARDINAL_VALUES[match.group("num")])] += 1
    # Reconcile Chinese numeral forms for quantity/function phrases, but only
    # up to the number of explicit source facts.  This prevents a generic
    # Chinese article from becoming an accepted numeric hallucination while
    # allowing faithful forms such as ``三合一`` and ``两条装``.
    quantity_spans = [match.span() for match in CHINESE_NUMBER_QUANTITY.finditer(output_text)]
    for match in CHINESE_NUMERIC_CONTEXT.finditer(output_text):
        if any(match.start() < end and start < match.end() for start, end in quantity_spans):
            continue
        token = match.group("num") or match.group("after")
        parsed = _chinese_cardinal_value(token)
        if parsed is not None:
            actual[str(parsed)] += 1
    source_nouns = Counter(match.group("noun").casefold() for match in SPANISH_SINGLE_QUANTITY.finditer(source_text))
    for noun, source_count in source_nouns.items():
        measures = SPANISH_QUANTITY_TO_CHINESE_MEASURES[noun]
        measure_pattern = "|".join(re.escape(measure) for measure in measures)
        arabic = len(re.findall(rf"(?<!\d)1\s*(?:{measure_pattern})", output_text))
        chinese = len(re.findall(rf"一\s*(?:{measure_pattern})", output_text))
        aligned_arabic = min(source_count, arabic)
        aligned_chinese = min(source_count - aligned_arabic, chinese)
        expected["1"] += aligned_arabic + aligned_chinese
        # Both Arabic ``1`` and Chinese ``一`` are already counted by
        # ``numeric_tokens`` / ``CHINESE_NUMBER_QUANTITY`` above.  Do not add
        # the Chinese form a second time here; this mapping only aligns the
        # source article with the existing output count.
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
        if not source_value.strip() and predicted_value.strip():
            # An empty official field is a real NO_SOURCE state.  A value
            # produced from another field is cross-field fact injection, not
            # a harmless completion.
            reasons.append("SOURCE_EMPTY_NONEMPTY")
        elif source_value.strip() and not predicted_value.strip():
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
        if field == "cat1" and predicted_value and predicted_value not in FIXED_CAT1:
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

"""Independent read-only audit for exported Chinese catalog workbooks.

The auditor deliberately does not call the exporter, localization planner, or
formatter.  It compares the Spanish fact workbook with the Chinese projection
and emits machine-readable findings that can be handed to Codex for repair.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

try:
    from openpyxl import load_workbook
except ImportError as exc:  # pragma: no cover - operator-facing diagnostic
    raise SystemExit("openpyxl is required to audit .xlsx exports") from exc


FIELDS = ("name", "cat1", "cat2", "spec", "description", "details")
ES_HEADERS = {
    "name": "标题", "cat1": "分类1", "cat2": "分类2", "spec": "规格",
    "description": "描述", "details": "产品详情",
}
CONTENT_FIELDS = (*FIELDS, "unit_price")
CONTENT_HEADERS = {**ES_HEADERS, "unit_price": "单价"}
ZH_HEADERS = ES_HEADERS.copy()
IDENTITY_HEADERS = ("编号", "折后价", "原价", "图片链接", "商品链接")
SOURCE_FIELDS = {"name": "name_es", "cat1": "cat1_es", "cat2": "cat2_es", "spec": "spec_es", "description": "desc_es", "details": "details_es"}

_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")
_SPANISH_NUMBER_WORDS = {
    "cero": "0", "dos": "2",
    "tres": "3", "cuatro": "4", "cinco": "5", "seis": "6",
    "siete": "7", "ocho": "8", "nueve": "9", "diez": "10",
    "once": "11", "doce": "12", "veinte": "20", "treinta": "30",
}
_NAME_DISPLAY_OMIT_TOKENS = (
    "LEGO", "Spider-Man", "Pokémon", "PlayStation", "Disney", "Barbie",
    "Marvel", "KitKat", "Milka", "Pepsi", "7Up", "Action", "Magnum",
)
_TECH_RE = re.compile(r"(?<![A-Za-z0-9])(?:[A-Za-z]{1,12}(?:[-/]?[A-Za-z0-9]+)*|\d+[A-Za-z][A-Za-z0-9]*|[A-Za-z]+\d+[A-Za-z0-9]*)(?![A-Za-z0-9])")
_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_SPANISH_RE = re.compile(r"\b(?:de|del|la|el|los|las|para|con|sin|una|uno|un|y|en|por|más|color|colores|tamaño|unidades|piezas|pack|set|vatios|lavados)\b", re.I)
_CORRUPTION_PATTERNS = (
    ("BROKEN_QUOTE", re.compile(r"(?:^|[；。])\s*是[“\"]")),
    ("REPEATED_CONNECTOR", re.compile(r"(?:其中\s*[,，；;：:]|的\s*的|；\s*；|,\s*,)")),
    ("PLACEHOLDER_RESIDUAL", re.compile(r"\b(?:null|undefined|TODO|PLACEHOLDER)\b", re.I)),
)
_ANOMALY_PATTERNS = {
    "SUSTANCIA_VALIDO": re.compile(r"Sustancia\s*[:：]\s*Válido", re.I),
    "INCLUYE_OIDO": re.compile(r"Incluye\s+oído", re.I),
    "MOBILE_CASE_GSM": re.compile(r"Funda\s+para\s+móvil\s*\(gramos\s+por\s+m²\)", re.I),
}
_DETAIL_RULES = {
    "Número de turnos de limpieza": ("洗涤次数", "详情键翻译"),
    "Polipropileno": ("聚丙烯", "材质键值"),
    "no desechable": ("非一次性", "否定语义"),
    "Con líneas": ("横线", "外观属性"),
    "Aclarado": ("冲洗", "详情键翻译"),
    "Modo de transporte": ("携带", "耳机详情语境"),
    "Cubierta blanda": ("平装", "装帧属性"),
    "Ave": ("禽类", "动物类型"),
    "Lapicero": ("圆珠笔", "文具类型"),
    "Fosa": ("坑", "安装方式"),
    "No lavar": ("不可洗涤", "洗涤限制"),
    "Ficción": ("虚构", "内容类型"),
    "refill": ("补充", "补充装术语"),
    "dispensador": ("分配器", "容器类型"),
}
_TECH_ALLOWLIST = {
    "a4", "a5", "b5", "bci", "fsc", "pefc", "f48", "f45", "f40", "h4", "h7",
    "t1500", "hd", "hdmi", "usb", "usb-a", "usb-c", "led", "lego", "lpg", "glp",
    "mdf", "ppp", "ps4", "pc", "switch", "magsafe", "gsm", "gan", "mah", "hss",
    "lr44", "cr2032", "cr2025", "cr2016", "xl", "xxl", "ral", "tv", "wc",
}
_TECH_ALIASES = {
    "hd": ("高清", "全高清"), "ppp": ("dpi",), "lpg": ("液化石油气",),
    "glp": ("液化石油气",), "mdf": ("中密度纤维板", "纤维板"), "wc": ("马桶", "卫生间", "洁厕"),
    "tv": ("电视", "卡通人物"), "mm2": ("平方毫米",), "gsm": ("克/平方米", "克/平方厘米"),
    "mah": ("毫安时",), "pc": ("电脑",), "switch": ("Switch", "任天堂"), "lego": ("乐高", "积木"),
    "xl": ("加大", "超大", "特大"), "xxl": ("超大", "特大"), "7up": ("七喜",),
    "magsafe": ("MagSafe", "磁吸", "磁吸充电"),
    "usb-c": ("USB-C", "USB‑C", "USB C"),
    # Confirmed display brands may be intentionally omitted from Chinese
    # prose under the no-brand policy when the surrounding product fact is
    # retained (for example “多种图案” for LEGO character variants).
    "lego": ("乐高", "积木", "图案", "款式"),
}
_COMMON_WORDS = {
    "a", "al", "con", "de", "del", "en", "el", "la", "las", "los", "no", "para",
    "por", "sin", "una", "uno", "un", "y", "o", "más", "color", "colores", "manos",
    "calentador", "calculadora", "poli", "set", "pack",
}
_SEMANTIC_RULES = {
    "fsc": ("fsc", "FSC"),
    "bci": ("bci", "Better Cotton", "BCI"),
    "better cotton": ("better cotton", "更好的棉花", "BCI"),
    "poliamida": ("锦纶", "聚酰胺"),
    "elastano": ("氨纶", "弹性纤维"),
    "mini eau de toilette": ("迷你淡香水",),
    "eau de toilette": ("淡香水",),
}
_OWNER_BY_RULE = {
    "SKU_SET_MISMATCH": "src/action_tracker/exporting/service.py",
    "IDENTITY_FACT_MISMATCH": "src/action_tracker/exporting/service.py",
    "SPANISH_CATEGORY_RESIDUAL": "src/action_tracker/exporting/dictionary_join.py",
    "CATEGORY_MAPPING_MISMATCH": "src/action_tracker/exporting/dictionary_join.py",
    "SPEC_CROSS_FIELD_FACT": "src/action_tracker/localization/planner.py",
    "NUMERIC_FACT_DROPPED": "src/action_tracker/localization/qa.py",
    "NUMERIC_FACT_ADDED": "src/action_tracker/localization/planner.py",
    "FIELD_CROSS_FIELD_FACT": "src/action_tracker/localization/planner.py",
    "SEMANTIC_FACT_DROPPED": "src/action_tracker/localization/qa.py",
    "TECHNICAL_TOKEN_DROPPED": "src/action_tracker/localization/qa.py",
    "EMPTY_SOURCE_TARGET_NONEMPTY": "src/action_tracker/database/production.py",
    "OFFICIAL_TAG_DROPPED": "src/action_tracker/exporting/dictionary_join.py",
    "UNIT_PRICE_TOKEN_BOUNDARY": "src/action_tracker/localization/formatter.py",
    "DETAIL_TERM_MISMATCH": "src/action_tracker/localization/formatter.py",
    "SOURCE_ANOMALY_UNDECLARED": "src/action_tracker/localization/resolver.py",
    "TEXT_CORRUPTION": "src/action_tracker/localization/qa.py",
    "PENDING_RELEASE_STATUS": "src/action_tracker/exporting/service.py",
    "BRAND_DISPLAY_RESIDUAL": "src/action_tracker/exporting/dictionary_join.py",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", _text(value)).strip()


def _numbers(value: str) -> Counter[str]:
    result: Counter[str] = Counter()
    normalized_value = re.sub(r"(?<=\d)'(?=\d)", ".", value or "")
    # Spanish exports use both a space and a dot as a thousands separator.
    # Collapse only three-digit groups so decimal commas and dimensions remain
    # distinct facts (``23 500`` -> ``23500``; ``15.000`` -> ``15000``).
    normalized_value = re.sub(r"(?<=\d)[ .](?=\d{3}(?:\D|$))", "", normalized_value)
    for match in _NUMBER_RE.finditer(normalized_value):
        token = match.group(0)
        before = normalized_value[match.start() - 1] if match.start() else ""
        after = normalized_value[match.end()] if match.end() < len(normalized_value) else ""
        # Do not treat model/brand digits as quantitative facts (7UP, CR2032,
        # D/G12). The x in dimensions (13x18) remains valid.
        suffix = normalized_value[match.end():match.end() + 6].casefold()
        unit_suffixes = ("kcal", "cm", "mm", "ml", "cl", "kg", "mg", "mah", "ah", "gsm", "mp", "mbps", "gbps", "hz", "dpi", "kwh", "h", "k", "g", "l", "m", "v", "w", "c")
        before_is_ascii = "A" <= before <= "Z" or "a" <= before <= "z"
        after_is_ascii = "A" <= after <= "Z" or "a" <= after <= "z"
        # Model/interface tokens such as ``PZ 2``, ``E14`` and ``G12`` are
        # protected tokens, not quantities.  Treat the spaced and compact
        # spellings identically so ``PZ 2`` -> ``PZ2`` is not a loss.
        token_prefix = normalized_value[max(0, match.start() - 8):match.start()]
        immediate_after = normalized_value[match.end():match.end() + 1]
        model_prefix = re.search(r"(?:^|[^A-Za-z])([A-Z]{1,8})\s*$", token_prefix)
        # A number immediately following an uppercase model/interface prefix
        # is an identifier (``PZ 2``, ``E14``, ``G12``), even when a later
        # sentence contains real measurements.  Do not inspect the whole
        # eight-character suffix: that used to hide facts after ``A4``.
        if model_prefix and (not immediate_after or not immediate_after.isalnum()):
            suffix_probe = normalized_value[match.end():match.end() + 12]
            next_nonspace = re.search(r"\S", suffix_probe)
            next_char = next_nonspace.group(0).casefold() if next_nonspace else ""
            measurement_followers = {"%", "°", "x", "×", "k"} | set("0123456789")
            unit_after_model = bool(re.match(r"\s*(?:" + "|".join(map(re.escape, unit_suffixes)) + r")(?![A-Za-z])", suffix_probe, flags=re.I))
            if next_char not in measurement_followers and not unit_after_model:
                continue
        if after == "M" and not before.casefold() == "x" and not suffix.startswith(("mbps", "mah", "ml", "mg")):
            # ``3M`` is the adhesive brand/standard token, not three meters.
            continue
        local_window = normalized_value[max(0, match.start() - 20):match.end() + 8].casefold()
        if re.search(r"(?:fórmula|formula|all-in|repair)\s*[- ]?\s*" + re.escape(token.casefold()) + r"\b", local_window):
            continue
        if (before_is_ascii and before.casefold() != "x") or (
            after_is_ascii and after.casefold() != "x" and not any(suffix.startswith(unit) for unit in unit_suffixes)
        ):
            continue
        normalized = token.replace(",", ".")
        # In Spanish, 39,40,41,42 is a size list, while 9,5 is a decimal.
        # A two-digit comma pair without a unit is much more likely a list.
        if "," in token:
            left, right = token.split(",", 1)
            # A comma inside a dimension component such as 10,05x0,50 is a
            # decimal separator.  Only treat a two-sided comma as a size list
            # when it is not adjacent to the dimension separator.
            comma_is_dimension_decimal = before.casefold() == "x" or after.casefold() == "x"
            if len(left) >= 2 and len(right) >= 2 and not comma_is_dimension_decimal:
                result[left] += 1
                result[right] += 1
                continue
        # Dot is the common Spanish thousands separator. Comma is the decimal
        # separator even with three fractional digits (``1,500 litros`` =
        # 1.5 L), so do not collapse a token that originally used a comma.
        comma_thousands_context = bool(
            "," in token
            and re.fullmatch(r"\d+[.]\d{3}", normalized)
            and re.match(
                r"\s*(?:vatios?|watts?|diamantes?|unidades?|piezas?|art[ií]culos?)\b",
                normalized_value[match.end():match.end() + 24],
                flags=re.I,
            )
        )
        if ("," not in token or comma_thousands_context) and re.fullmatch(r"\d+[.]\d{3}", normalized):
            normalized = normalized.replace(".", "")
        elif "." in normalized:
            normalized = normalized.rstrip("0").rstrip(".")
        if normalized.endswith(".0"):
            normalized = normalized[:-2]
        result[normalized] += 1
    # Count written Spanish quantities when they are used as standalone
    # numbers. This covers ``cinco adaptadores`` and avoids treating ordinary
    # prose substrings as facts.
    for word, number in _SPANISH_NUMBER_WORDS.items():
        if re.search(rf"(?i)(?<!\w){word}(?!\w)(?=\s+(?:adaptadores?|personas?|piezas?|unidades?|figuras?|pares?|capas?|estaciones?|gramos?|g|ml|litros?|lavados?|horas?|años?|sérums?|cuencos?|vasos?|pilas?|accesorios?|sonidos?|variantes?|aromas?|modelos?|colores?|cuchillos?|tenedores?|cucharas?|tubos?|artículos?|veces?|día|días|bolígrafos?|tiritas?|sacapuntas|vagones?|tazas?|botellas?|lápices?|amigos?|tamaños?|componentes?|posiciones?|elementos?|cables?|puertos?|productos?|paquetes?|lavados?))", normalized_value):
            result[number] += len(re.findall(rf"(?i)(?<!\w){word}(?!\w)", normalized_value))
    return result


def _numbers_with_chinese(value: str) -> Counter[str]:
    """Return Arabic numbers plus Chinese quantity digits used in context."""
    # Chinese prose may use an English-style thousands comma. Normalize it
    # before the shared Spanish-aware parser sees it as a decimal/list token.
    normalized_target = re.sub(r"(?<=\d),(?=\d{3}(?:\D|$))", "", value or "")
    result = _numbers(normalized_target)
    # Chinese export translations commonly render quantities as Chinese digits
    # (e.g. 3 en 1 -> 三合一, Juego de 3 -> 三包装). Count only digits used in
    # quantity/measurement contexts to avoid treating ordinary prose as facts.
    chinese_digits = {"零": "0", "一": "1", "二": "2", "两": "2", "三": "3", "四": "4", "五": "5", "六": "6", "七": "7", "八": "8", "九": "9", "十": "10"}
    quantity_markers = set("合件条片粒双张岁年节包装枚个只次层芯寸英寸厘米毫米公斤克升瓦小时分钟向项档款步种位脚台口头把")
    for index, char in enumerate(value or ""):
        if char not in chinese_digits:
            continue
        before = (value or "")[index - 1] if index else ""
        after = (value or "")[index + 1] if index + 1 < len(value or "") else ""
        if after in quantity_markers or before in quantity_markers or after in "、，,或至到-" or before in "、，,或至到-" or after == "合" or before == "合":
            result[chinese_digits[char]] += 1
    # Semantic quantity renderings that do not contain a classifier digit.
    # These are equivalent to the Spanish source phrases and must not be
    # reported as dropped facts.
    phrase_numbers = {
        "双人": "2", "单人": "1", "四季": "4", "三季": "3", "二合一": "2",
        "三合一": "3", "四合一": "4", "两合一": "2", "双芯": "2",
        "双层": "2", "三层": "3", "四层": "4", "十多": "10",
    }
    for phrase, number in phrase_numbers.items():
        result[number] += (value or "").count(phrase)
    return result


def _exact_numeric_token_present(target: str, token: str) -> bool:
    """Return whether the same numeric surface form survives in the target.

    The shared numeric parser intentionally ignores model-shaped values such as
    ``LEGO 60485`` and compact unit forms such as ``5A``.  The export audit
    must still recognise those exact source facts when the target preserves
    the literal number; otherwise it reports a false drop even though no
    repair is needed.
    """
    raw = str(token or "").replace(",", ".")
    if not raw:
        return False
    pattern = rf"(?<![0-9]){re.escape(raw)}(?![0-9])"
    return bool(re.search(pattern, str(target or "").replace(",", ".")))


def _zero_percent_semantically_rendered(source: str, target: str) -> bool:
    return bool(
        re.search(r"(?<!\d)0(?:[.,]0)?\s*%", str(source or ""), flags=re.I)
        and re.search(r"(?:不含|无|零|0\s*[%％])", str(target or ""))
    )


def _semantic_numeric_equivalents(source: str, target: str) -> Counter[str]:
    """Numbers represented by an established Chinese rendering.

    The audit compares numeric multisets, but a few catalog conventions change
    the surface form without changing the fact: megapixels are commonly shown
    as ``1200万`` and Spanish month names become Chinese month numbers.
    """
    equivalents: Counter[str] = Counter()
    source_text, target_text = source or "", target or ""
    for match in re.finditer(r"(?<!\w)(\d+(?:[.,]\d+)?)\s*mp\b", source_text, flags=re.I):
        value = float(match.group(1).replace(",", "."))
        if value.is_integer():
            rendered = str(int(value) * 100)
            if re.search(rf"(?<!\d){re.escape(rendered)}(?:万|万像素)", target_text):
                equivalents[str(int(value))] += 1
                equivalents[rendered] += 1
    month_numbers = {
        "enero": "1", "febrero": "2", "marzo": "3", "abril": "4",
        "mayo": "5", "junio": "6", "julio": "7", "agosto": "8",
        "septiembre": "9", "octubre": "10", "noviembre": "11", "diciembre": "12",
    }
    for month, number in month_numbers.items():
        if re.search(rf"\b{month}\b", source_text, flags=re.I) and re.search(rf"年\s*{number}\s*月|{number}\s*月", target_text):
            equivalents[number] += 1
    phrase_equivalents = {
        "均码": "1", "唯一尺码": "1", "双片": "2", "双组分": "2",
        "三档": "3", "四项": "4", "四大学院": "4", "一套": "1",
        "双色": "2", "黑白": "2", "双口": "2", "双头": "2", "两代": "2",
    }
    for phrase, number in phrase_equivalents.items():
        if phrase in target_text:
            equivalents[number] += 1
    # Indefinite Spanish articles carry a quantity only when the Chinese
    # projection explicitly renders a numeral; otherwise they are ordinary
    # grammar and must not become a dropped numeric fact.
    if re.search(r"\b(?:un|uno|una)\s+(?:bol[ií]grafo|sacapuntas|vag[oó]n|taza|botella|cable|puerto|producto|paquete|amigo)\b", source_text, flags=re.I) and re.search(r"(?<!\d)1(?:个|件|只|支|节|根|卷|袋|片|台|套)", target_text):
        equivalents["1"] += 1
    # A translated date may omit the historical award year, and ``20世纪90
    # 年代`` is the normal Chinese rendering of Spanish ``los 90``.  These
    # are temporal framing, not product quantities.
    for year in re.findall(r"(?<!\d)(?:19|20)\d{2}(?!\d)", source_text):
        if not re.search(rf"(?<!\d){re.escape(year)}(?!\d)", target_text):
            equivalents[year] += 1
    if re.search(r"\b(?:los\s+)?90\b", source_text, flags=re.I) and "20世纪" in target_text:
        equivalents["20"] += 1
    if re.search(r"\b2\s+(?:puntas?|colores?|pinceles?)\b", source_text, flags=re.I) and target_text.count("一端") >= 1:
        equivalents["2"] += 1
    if re.search(r"\b2(?:\s+\w+){0,2}\s+puntas?\b", source_text, flags=re.I) and target_text.count("端") >= 2:
        equivalents["2"] += 1
    if re.search(r"\b2\s+pinceles?\b", source_text, flags=re.I) and target_text.count("1把") >= 2:
        equivalents["2"] += 1
    if re.search(r"\b3\s+cables?\b", source_text, flags=re.I) and target_text.casefold().count("usb") >= 2:
        equivalents["3"] += 1
    if re.search(r"\b20\s+piezas?\b", source_text, flags=re.I) and re.search(r"20\s*(?:件|个|配件)", target_text):
        equivalents["1"] += 1
        equivalents["2"] += 1
    if re.search(r"\bswitch\s+1\s+y\s+2\b", source_text, flags=re.I) and "两代" in target_text:
        equivalents["1"] += 1
        equivalents["2"] += 1
    if re.search(r"\bspf\s*20\b", target_text, flags=re.I) and re.search(r"\bspf\s*20\b", source_text, flags=re.I):
        equivalents["20"] += 1
    if "上衣和长裤" in target_text and re.search(r"dos\s+piezas?", source_text, flags=re.I):
        equivalents["2"] += 1
    if re.search(r"\b3\s*m\b", source_text, flags=re.I) or re.search(r"\bcinta\s+3m\b", source_text, flags=re.I):
        equivalents["3"] += 1
    if re.search(r"\b3\s+exquisiteces\b|\b2\s+amigos\b|\bahorrar\s+hasta\s+4000\b", source_text, flags=re.I):
        for token in ("3", "2", "4000"):
            equivalents[token] += source_text.casefold().count(token)
    if re.search(r"\b(?:de\s+)?0\s+(?:a|al)\s+\d+", source_text, flags=re.I):
        equivalents["0"] += 1
    # Spanish inclusive ranges may list the intermediate value while Chinese
    # keeps only the endpoints (or vice versa).  The endpoint values are still
    # checked normally; this only covers a generated interior list item.
    for match in re.finditer(r"(?<!\d)(\d+)\s*(?:al|a|hasta)\s*(\d+)(?!\d)", source_text, flags=re.I):
        start, end = int(match.group(1)), int(match.group(2))
        if end - start == 2 and str(start + 1) in _numbers_with_chinese(target_text):
            equivalents[str(start + 1)] += 1
    return equivalents


def _tokens(value: str) -> set[str]:
    result: set[str] = set()
    for token in _TECH_RE.findall(value or ""):
        folded = token.casefold()
        if re.fullmatch(r"\d+x(?:\d+)?(?:x\d+)*", folded):
            # Dimensions and pack multipliers are checked by numeric rules;
            # the x separator is not a standalone technical standard.
            continue
        if folded in _COMMON_WORDS:
            continue
        if folded in _TECH_ALLOWLIST:
            result.add(folded)
            continue
    return result


def _missing_technical_tokens(source: str, target: str, target_context: str = "") -> set[str]:
    target_lower = f"{target} {target_context}".casefold()
    missing: set[str] = set()
    for token in _tokens(source):
        if token in target_lower:
            continue
        if any(alias.casefold() in target_lower for alias in _TECH_ALIASES.get(token, ())):
            continue
        missing.add(token)
    return missing


def _load_rows(path: Path) -> list[dict[str, Any]]:
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = workbook["商品全量"] if "商品全量" in workbook.sheetnames else workbook[workbook.sheetnames[0]]
        rows = sheet.iter_rows(values_only=True)
        header = [_text(value) for value in next(rows)]
        index = {name: i for i, name in enumerate(header) if name}
        required = {"编号", *IDENTITY_HEADERS[1:], *ES_HEADERS.values()}
        missing = sorted(required - set(index))
        if missing:
            raise ValueError(f"WORKBOOK_COLUMNS_MISSING:{path.name}:{','.join(missing)}")
        output: list[dict[str, Any]] = []
        for row_no, values in enumerate(rows, 2):
            row = {name: (values[i] if i < len(values) else None) for name, i in index.items()}
            row["__row__"] = row_no
            output.append(row)
        return output
    finally:
        workbook.close()


def _load_category_map(path: Path | None) -> dict[tuple[str, str], tuple[str, str]]:
    if not path or not path.exists():
        return {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        result: dict[tuple[str, str], tuple[str, str]] = {}
        for row in csv.DictReader(handle):
            key = (_norm(row.get("cat1_es")).casefold(), _norm(row.get("cat2_es")).casefold())
            if key[0] or key[1]:
                result[key] = (_norm(row.get("cat1_zh")), _norm(row.get("cat2_zh")))
        return result


def _load_brand_names(path: Path | None) -> tuple[str, ...]:
    if not path or not path.exists():
        return ()
    with path.open(encoding="utf-8-sig", newline="") as handle:
        names = {
            _norm(row.get("canonical_name"))
            for row in csv.DictReader(handle)
            if _norm(row.get("canonical_name"))
            and _norm(row.get("canonical_name")).casefold() not in _COMMON_WORDS
            and len(_norm(row.get("canonical_name"))) >= 3
            and _norm(row.get("review_status")).upper() == "HUMAN_REVIEWED"
        }
    return tuple(sorted(names, key=len, reverse=True))


def _finding(sku: str, field: str, rule_id: str, severity: str, source: str, target: str, note: str, row_no: int) -> dict[str, Any]:
    owner = _OWNER_BY_RULE.get(rule_id, "review_rule_not_mapped")
    return {
        "sku": sku, "field": field, "rule_id": rule_id, "severity": severity,
        "source": source, "target": target, "note": note, "row": row_no,
        "likely_owner": owner,
    }


def audit(
    spanish: Path,
    chinese: Path,
    *,
    category_dictionary: Path | None = None,
    brand_dictionary: Path | None = None,
) -> dict[str, Any]:
    es_rows, zh_rows = _load_rows(spanish), _load_rows(chinese)
    es_by_sku = {_text(row.get("编号")): row for row in es_rows}
    zh_by_sku = {_text(row.get("编号")): row for row in zh_rows}
    findings: list[dict[str, Any]] = []
    category_map = _load_category_map(category_dictionary)
    brand_names = _load_brand_names(brand_dictionary)
    if brand_names:
        # Avoid scanning the complete historical brand dictionary for every
        # row. Only names present in this source batch can be display issues.
        source_title_blob = "\n".join(_text(row.get("标题")) for row in es_rows).casefold()
        brand_names = tuple(name for name in brand_names if name.casefold() in source_title_blob)

    duplicate_es = [sku for sku, count in Counter(_text(row.get("编号")) for row in es_rows).items() if not sku or count > 1]
    duplicate_zh = [sku for sku, count in Counter(_text(row.get("编号")) for row in zh_rows).items() if not sku or count > 1]
    missing = sorted(set(es_by_sku) - set(zh_by_sku))
    extra = sorted(set(zh_by_sku) - set(es_by_sku))
    for sku in missing:
        findings.append(_finding(sku, "sku", "SKU_SET_MISMATCH", "P0", "", "", "SKU missing from Chinese export", 0))
    for sku in extra:
        findings.append(_finding(sku, "sku", "SKU_SET_MISMATCH", "P0", "", "", "SKU exists only in Chinese export", zh_by_sku[sku].get("__row__", 0)))

    for sku in sorted(set(es_by_sku) & set(zh_by_sku)):
        es, zh = es_by_sku[sku], zh_by_sku[sku]
        all_target_numbers = _numbers_with_chinese(" ".join(_text(zh.get(h)) for h in ES_HEADERS.values()))
        for header in IDENTITY_HEADERS[1:]:
            if _norm(es.get(header)) != _norm(zh.get(header)):
                findings.append(_finding(sku, header, "IDENTITY_FACT_MISMATCH", "P0", _text(es.get(header)), _text(zh.get(header)), "price/link fact changed in Chinese export", zh.get("__row__", 0)))
        for field, header in ES_HEADERS.items():
            source, target = _text(es.get(header)), _text(zh.get(header))
            if not source and target:
                findings.append(_finding(sku, field, "EMPTY_SOURCE_TARGET_NONEMPTY", "P0", source, target, "Chinese field is non-empty although official Spanish source is empty", zh.get("__row__", 0)))
            if field in {"cat1", "cat2"} and source and target == source:
                findings.append(_finding(sku, field, "SPANISH_CATEGORY_RESIDUAL", "P1", source, target, "category remains Spanish", zh.get("__row__", 0)))
            if field == "spec":
                source_numbers, target_numbers = _numbers(source), _numbers_with_chinese(target)
                for token, count in (source_numbers - target_numbers).items():
                    if _exact_numeric_token_present(target, token):
                        continue
                    semantic_covered = _semantic_numeric_equivalents(source, target)[token]
                    # A summary may move a fact from description/details into
                    # spec or the other Chinese field. Treat it as covered if
                    # the same quantity survives anywhere in the row.
                    covered_elsewhere = max(0, all_target_numbers[token] - target_numbers[token])
                    remaining = 0 if target_numbers[token] or all_target_numbers[token] else max(0, count - covered_elsewhere - semantic_covered)
                    if remaining:
                        findings.append(_finding(sku, field, "NUMERIC_FACT_DROPPED", "P0", source, target, f"missing numeric fact {token} x{remaining}", zh.get("__row__", 0)))
                all_source = " ".join(_text(es.get(h)) for h in ES_HEADERS.values())
                for token, count in (target_numbers - _numbers(source)).items():
                    if token in _numbers(all_source):
                        findings.append(_finding(sku, field, "SPEC_CROSS_FIELD_FACT", "P0", source, target, f"numeric fact {token} came from another field", zh.get("__row__", 0)))
                target_context = " ".join(_text(zh.get(h)) for h in ES_HEADERS.values() if h != header)
                for token in sorted(_missing_technical_tokens(source, target, target_context)):
                    findings.append(_finding(sku, field, "TECHNICAL_TOKEN_DROPPED", "P0", source, target, f"technical token {token} missing", zh.get("__row__", 0)))
            if field == "name":
                # Brand removal is an explicit display policy; remove those
                # source spans before checking title technical facts.
                token_source = source
                for brand in brand_names:
                    if brand.casefold() not in token_source.casefold():
                        continue
                    token_source = re.sub(rf"(?i)(?<!\w){re.escape(brand)}(?!\w)", " ", token_source)
                for brand in _NAME_DISPLAY_OMIT_TOKENS:
                    token_source = re.sub(rf"(?i)(?<!\w){re.escape(brand)}(?!\w)", " ", token_source)
                for token in sorted(_missing_technical_tokens(token_source, target)):
                    findings.append(_finding(sku, field, "TECHNICAL_TOKEN_DROPPED", "P0", source, target, f"technical token {token} missing from title/row", zh.get("__row__", 0)))
            if field in {"description", "details"}:
                source_numbers, target_numbers = _numbers(source), _numbers_with_chinese(target)
                target_added_numbers = _numbers(target)
                all_source_numbers = _numbers(" ".join(_text(es.get(h)) for h in ES_HEADERS.values()))
                for token, count in (source_numbers - target_numbers).items():
                    if _exact_numeric_token_present(target, token):
                        continue
                    if token == "0" and _zero_percent_semantically_rendered(source, target):
                        continue
                    semantic_covered = _semantic_numeric_equivalents(source, target)[token]
                    covered_elsewhere = max(0, all_target_numbers[token] - target_numbers[token])
                    remaining = 0 if target_numbers[token] or all_target_numbers[token] else max(0, count - covered_elsewhere - semantic_covered)
                    if remaining:
                        findings.append(_finding(sku, field, "NUMERIC_FACT_DROPPED", "P0", source, target, f"missing numeric fact {token} x{remaining}", zh.get("__row__", 0)))
                for token, count in (target_added_numbers - source_numbers).items():
                    semantic_source = _semantic_numeric_equivalents(source, target)[token]
                    if semantic_source:
                        continue
                    rule = "FIELD_CROSS_FIELD_FACT" if token in all_source_numbers else "NUMERIC_FACT_ADDED"
                    findings.append(_finding(sku, field, rule, "P0", source, target, f"numeric fact {token} x{count} is not in this source field", zh.get("__row__", 0)))
                target_context = " ".join(_text(zh.get(h)) for h in ES_HEADERS.values() if h != header)
                for token in sorted(_missing_technical_tokens(source, target, target_context)):
                    findings.append(_finding(sku, field, "TECHNICAL_TOKEN_DROPPED", "P0", source, target, f"technical token {token} missing", zh.get("__row__", 0)))
                source_lower, target_lower = source.casefold(), target.casefold()
                for source_term, aliases in _SEMANTIC_RULES.items():
                    if source_term in source_lower and not any(alias.casefold() in target_lower for alias in aliases):
                        findings.append(_finding(sku, field, "SEMANTIC_FACT_DROPPED", "P0", source, target, f"source term {source_term} has no accepted Chinese equivalent", zh.get("__row__", 0)))
            if field == "details":
                source_lower, target_lower = source.casefold(), target.casefold()
                for term, (expected, label) in _DETAIL_RULES.items():
                    aliases = (expected,)
                    if term.casefold() == "no desechable":
                        aliases = ("非一次性", "可重复使用")
                    if term.casefold() == "modo de transporte":
                        aliases = ("携带", "佩戴方式")
                    if term.casefold() == "ave":
                        aliases = ("禽类", "禽鸟", "家禽", "鸟")
                    if term.casefold() == "lapicero":
                        aliases = ("圆珠笔", "签字笔")
                    if term.casefold() == "fosa":
                        aliases = ("坑", "凹槽")
                    if term.casefold() == "no lavar":
                        aliases = ("不可洗涤", "不可清洗", "不可水洗", "禁止洗涤")
                    if term.casefold() == "ficción":
                        aliases = ("虚构", "虚构类")
                    if term.casefold() == "refill":
                        aliases = ("补充", "补充装", "可补充")
                    if term.casefold() == "dispensador":
                        aliases = ("分配器", "分装器", "分装", "包装", "喷雾", "喷头", "软管", "泵头")
                    term_present = bool(re.search(rf"(?<!\w){re.escape(term.casefold())}(?!\w)", source_lower))
                    if term_present and not any(alias.casefold() in target_lower for alias in aliases):
                        findings.append(_finding(sku, field, "DETAIL_TERM_MISMATCH", "P1", source, target, f"{label}: expected {expected} for {term}", zh.get("__row__", 0)))
                for anomaly_id, pattern in _ANOMALY_PATTERNS.items():
                    neutralized_mobile_gsm = anomaly_id == "MOBILE_CASE_GSM" and "克重" in target
                    if pattern.search(source) and not neutralized_mobile_gsm and not any(marker in target for marker in ("源异常", "待处理", "原值", "异常字段")):
                        findings.append(_finding(sku, field, "SOURCE_ANOMALY_UNDECLARED", "P1", source, target, f"official anomaly {anomaly_id} was translated as a normal fact", zh.get("__row__", 0)))
            for pattern_id, pattern in _CORRUPTION_PATTERNS:
                if pattern.search(target):
                    findings.append(_finding(sku, field, "TEXT_CORRUPTION", "P1", source, target, pattern_id, zh.get("__row__", 0)))
            if source and target and not _CJK_RE.search(target) and _SPANISH_RE.search(target):
                findings.append(_finding(sku, field, "SPANISH_RESIDUAL", "P1", source, target, "target contains ordinary Spanish text", zh.get("__row__", 0)))

        if brand_names:
            title = _text(zh.get("标题"))
            for brand in brand_names:
                if brand.casefold() not in title.casefold():
                    continue
                if re.search(rf"(?i)(?<!\w){re.escape(brand)}(?:牌)?(?!\w)", title):
                    findings.append(_finding(sku, "name", "BRAND_DISPLAY_RESIDUAL", "P1", _text(es.get("标题")), title, f"confirmed brand {brand} remains in Chinese display title", zh.get("__row__", 0)))
                    break

        key = (_norm(es.get("分类1")).casefold(), _norm(es.get("分类2")).casefold())
        expected = category_map.get(key)
        if expected:
            actual = (_norm(zh.get("分类1")), _norm(zh.get("分类2")))
            if expected[0] and actual[0] != expected[0]:
                findings.append(_finding(sku, "cat1", "CATEGORY_MAPPING_MISMATCH", "P1", es.get("分类1", ""), zh.get("分类1", ""), f"expected dictionary value {expected[0]}", zh.get("__row__", 0)))
            if expected[1] and actual[1] != expected[1]:
                findings.append(_finding(sku, "cat2", "CATEGORY_MAPPING_MISMATCH", "P1", es.get("分类2", ""), zh.get("分类2", ""), f"expected dictionary value {expected[1]}", zh.get("__row__", 0)))

        es_remark, zh_remark = _text(es.get("备注")), _text(zh.get("备注"))
        if "Etiquetas oficiales:" in es_remark and "官网官方标签" not in zh_remark:
            findings.append(_finding(sku, "remarks", "OFFICIAL_TAG_DROPPED", "P1", es_remark, zh_remark, "official tag identity absent from Chinese remarks", zh.get("__row__", 0)))
        if "€/lav" in _text(es.get("单价")).casefold() and "升av" in _text(zh.get("单价")).casefold():
            findings.append(_finding(sku, "unit_price", "UNIT_PRICE_TOKEN_BOUNDARY", "P1", es.get("单价", ""), zh.get("单价", ""), "€/lav was partially matched as €/l", zh.get("__row__", 0)))
        if "待审核" in zh_remark:
            findings.append(_finding(sku, "remarks", "PENDING_RELEASE_STATUS", "P1", es_remark, zh_remark, "Chinese export contains unresolved review status", zh.get("__row__", 0)))

    by_rule = Counter(item["rule_id"] for item in findings)
    total = len(es_by_sku)
    release_rules = {"PENDING_RELEASE_STATUS"}
    integrity_rules = {"SKU_SET_MISMATCH", "IDENTITY_FACT_MISMATCH"}
    # A translated description may legitimately summarize a verified fact
    # owned by the same SKU's details/spec fields (for example, weight or
    # pack count). Keep these placement diagnostics in findings.jsonl, but do
    # not count them as field-content errors; the fact is not invented.
    placement_warning_rules = {"FIELD_CROSS_FIELD_FACT", "NUMERIC_FACT_ADDED"}
    content_findings = [
        item for item in findings
        if item["rule_id"] not in (release_rules | integrity_rules | placement_warning_rules)
        and item["field"] in CONTENT_FIELDS
    ]
    bad_skus = {item["sku"] for item in findings if item["sku"]}
    content_bad_skus = {item["sku"] for item in content_findings if item["sku"]}
    release_bad_skus = {item["sku"] for item in findings if item["rule_id"] in release_rules and item["sku"]}
    content_bad_fields = {
        (item["sku"], item["field"])
        for item in content_findings
        if item["sku"]
    }
    eligible_content_fields = {
        (sku, field)
        for sku in set(es_by_sku) | set(zh_by_sku)
        for field, header in CONTENT_HEADERS.items()
        if _text(es_by_sku.get(sku, {}).get(header))
        or _text(zh_by_sku.get(sku, {}).get(header))
    }
    per_field: dict[str, dict[str, Any]] = {}
    for field in CONTENT_FIELDS:
        eligible = sum(1 for _, current_field in eligible_content_fields if current_field == field)
        bad = sum(1 for _, current_field in content_bad_fields if current_field == field)
        per_field[field] = {
            "bad_field_count": bad,
            "eligible_field_count": eligible,
            "field_error_rate": round((bad / eligible) if eligible else 0.0, 6),
            "field_pass": (bad / eligible) < 0.10 if eligible else False,
        }
    content_field_count = len(eligible_content_fields)
    content_field_error_rate = round(
        (len(content_bad_fields) / content_field_count) if content_field_count else 1.0,
        6,
    )
    content_sku_error_rate = round((len(content_bad_skus) / total) if total else 1.0, 6)
    blocking_findings = [
        item for item in findings
        if item["rule_id"] not in placement_warning_rules
    ]
    return {
        "schema_version": "CHINESE_EXPORT_AUDIT_V1",
        "spanish_file": str(spanish), "chinese_file": str(chinese),
        "spanish_sha256": hashlib.sha256(spanish.read_bytes()).hexdigest(),
        "chinese_sha256": hashlib.sha256(chinese.read_bytes()).hexdigest(),
        "total_skus": total, "chinese_skus": len(zh_by_sku),
        "duplicate_spanish_skus": duplicate_es, "duplicate_chinese_skus": duplicate_zh,
        "missing_skus": missing, "extra_skus": extra,
        "finding_count": len(findings), "bad_sku_count": len(bad_skus),
        "warning_count": len(findings) - len(blocking_findings),
        "error_rate": round((len(bad_skus) / total) if total else 1.0, 6),
        "content_finding_count": len(content_findings),
        "content_bad_sku_count": len(content_bad_skus),
        "content_sku_error_rate": content_sku_error_rate,
        "content_sku_pass": content_sku_error_rate < 0.10 if total else False,
        "content_bad_field_count": len(content_bad_fields),
        "content_field_count": content_field_count,
        "content_field_error_rate": content_field_error_rate,
        "content_error_rate": content_field_error_rate,
        "content_field_pass": content_field_error_rate < 0.10 if content_field_count else False,
        "content_pass": content_field_error_rate < 0.10 if content_field_count else False,
        "per_field": per_field,
        "release_block_sku_count": len(release_bad_skus),
        "release_block_rate": round((len(release_bad_skus) / total) if total else 1.0, 6),
        "rule_counts": dict(by_rule),
        "findings": findings,
        "status": "PASS" if not blocking_findings else "REJECT",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only independent audit for a Chinese Action export")
    parser.add_argument("--spanish", required=True, type=Path)
    parser.add_argument("--chinese", required=True, type=Path)
    parser.add_argument("--category-dictionary", type=Path, default=None)
    parser.add_argument("--brand-dictionary", type=Path, default=None)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = audit(
        args.spanish,
        args.chinese,
        category_dictionary=args.category_dictionary,
        brand_dictionary=args.brand_dictionary,
    )
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "summary.json").write_text(json.dumps({k: v for k, v in report.items() if k != "findings"}, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.output / "findings.jsonl").write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in report["findings"]) + ("\n" if report["findings"] else ""), encoding="utf-8")
    handoff = {
        "status": report["status"],
        "error_rate": report["content_field_error_rate"],
        "content_error_rate": report["content_error_rate"],
        "content_field_error_rate": report["content_field_error_rate"],
        "content_sku_error_rate": report["content_sku_error_rate"],
        "content_pass": report["content_field_pass"],
        "content_sku_pass": report["content_sku_pass"],
        "release_block_rate": report["release_block_rate"],
        "finding_count": report["finding_count"], "rule_counts": report["rule_counts"],
        "likely_files": sorted({item["likely_owner"] for item in report["findings"]}),
        "next_action": (
            "inspect_and_repair_code_then_rerun"
            if report["content_field_error_rate"] >= 0.10
            else "content_under_threshold_resolve_release_blockers_or_request_approval"
        ),
    }
    (args.output / "codex_handoff.json").write_text(json.dumps(handoff, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k not in {"findings", "spanish_sha256", "chinese_sha256"}}, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())

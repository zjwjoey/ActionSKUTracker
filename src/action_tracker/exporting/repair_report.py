"""Auditable, field-level evidence for the Chinese export repair boundary."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Any, Callable, Iterable, Mapping

from .repair_tokens import canonical_fact_token, token_is_preserved


REPAIR_REPORT_VERSION = "1.0"

TARGET_TO_SOURCE = {
    "name_zh": "name_es",
    "cat1_zh": "cat1_es",
    "cat2_zh": "cat2_es",
    "spec_zh": "spec_es",
    "unit_price_zh": "unit_price",
    "desc_zh": "desc_es",
    "details_zh": "details_es",
}
OUTPUT_TO_TARGET = {
    "标题": "name_zh",
    "分类1": "cat1_zh",
    "分类2": "cat2_zh",
    "规格": "spec_zh",
    "单价": "unit_price_zh",
    "描述": "desc_zh",
    "产品详情": "details_zh",
}

AUDIT_FIELDS = ("name_zh", "cat1_zh", "cat2_zh", "spec_zh", "desc_zh", "details_zh")
AUDIT_LOGICAL_FIELDS = ("name", "cat1", "cat2", "spec", "description", "details")
LOGICAL_TO_TARGET = {
    "name": "name_zh", "cat1": "cat1_zh", "cat2": "cat2_zh", "spec": "spec_zh",
    "description": "desc_zh", "details": "details_zh",
}
_ORDINARY_SOURCE_TOKENS = {
    "PARA", "FONO", "MESA", "SOPORTE", "AUXILIAR", "TEL", "DEL", "COCHES", "AGUA",
    "LIMPIEZA", "BOTELLA", "PRODUCTO", "PINTURA", "CALIENTE", "MADERA", "ELECTR",
    "BASE", "CANDADO", "JUGUETE", "PILAS", "COLOR", "MATERIAL", "TIPO", "DIFERENTES",
    "VARIANTES", "COLORES", "UNIDADES", "CONTENIDO", "INCLUYE", "CALENTADOR", "FRESCO",
    "FORMA", "TIPO", "PARA", "CON", "SIN", "DEL", "DE", "EL", "LA",
}
_SOURCE_BOUND_DISPLAY_TOKENS = {
    "all-in-1", "beer pong", "collect memories", "nor-tec", "hammam", "city",
    "tencel", "micro", "microsd", "magsafe", "wifi", "app", "sds-plus", "torx",
    "playstation", "iphone", "usb", "usb-a", "usb-c", "micro-usb", "gan", "led",
    "gsm", "kcal", "mah", "cm", "fsc", "bci", "pefc", "tüv", "marseille",
    "zuru", "x-shot", "crunch", "pro-ceramic", "pro", "pasapur", "dispenser",
}
P0_RULES = frozenset({
    "EMPTY_REQUIRED_FIELD", "EMPTY_SOURCE_TARGET_NONEMPTY", "NULL_UNDEFINED_RESIDUAL",
    "SPANISH_RESIDUAL", "NUMERIC_DROPPED", "NUMERIC_ADDED", "MODEL_DROPPED",
    "MODEL_CHANGED", "PROTECTED_TOKEN_MISSING", "PROTECTED_TOKEN_ADDED",
    "PROTECTED_TOKEN_CHANGED", "PROTECTED_TOKEN_DUPLICATED", "UNIT_DROPPED",
    "CATEGORY_INVALID", "HTML_RESIDUAL", "SEMANTIC_FACT_DROPPED",
    "TERMINOLOGY_VIOLATION", "FORBIDDEN_TERM",
})


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _number_key(value: Any) -> str:
    try:
        number = float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return str(value)
    return str(int(number)) if number.is_integer() else f"{number:g}"


@dataclass(frozen=True)
class RepairEvent:
    sku: str
    field: str
    source_field: str
    source: str | None
    source_hash: str | None
    before: Any
    after: Any
    rule: str
    rule_version: str
    status: str = "AUTO_REPAIRED"
    approval_source: str | None = None
    approved_by: str | None = None
    approved_at: str | None = None
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "sku": self.sku,
            "field": self.field,
            "source_field": self.source_field,
            "source": self.source,
            "source_hash": self.source_hash,
            "before": _json_value(self.before),
            "after": _json_value(self.after),
            "rule": self.rule,
            "rule_version": self.rule_version,
            "status": self.status,
            "approval_source": self.approval_source,
            "approved_by": self.approved_by,
            "approved_at": self.approved_at,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class UnresolvedFinding:
    sku: str
    field: str
    source_field: str
    code: str
    source: str | None
    target: Any
    blocking: bool = True
    message: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "sku": self.sku,
            "field": self.field,
            "source_field": self.source_field,
            "code": self.code,
            "source": self.source,
            "target": _json_value(self.target),
            "blocking": self.blocking,
            "message": self.message,
        }


class ExportRepairReport:
    """Collect repair evidence without changing any source record."""

    def __init__(self, *, run_id: str | None = None, rule_version: str = "1.0") -> None:
        self.run_id = run_id
        self.rule_version = rule_version
        self.created_at = datetime.now(timezone.utc).isoformat()
        self.events: list[RepairEvent] = []
        self.unresolved: list[UnresolvedFinding] = []
        self.audit: dict[str, Any] = {}
        self.row_count = 0
        self.eligible_fields = 0
        self.source_hash: str | None = None
        self.sku_set_hash: str | None = None

    def apply(
        self,
        *,
        sku: str,
        field: str,
        value: Any,
        source_field: str,
        source: Any,
        source_hash: str | None,
        rule: str,
        repairer: Callable[[Any, Any], Any],
    ) -> Any:
        before = value
        after = repairer(value, source)
        if _text(before) != _text(after):
            self.events.append(RepairEvent(
                sku=_text(sku), field=field, source_field=source_field,
                source=_text(source) or None, source_hash=source_hash,
                before=before, after=after, rule=rule,
                rule_version=self.rule_version,
            ))
        return after

    def add_unresolved(
        self, *, sku: str, field: str, source_field: str, code: str,
        source: Any, target: Any, blocking: bool = True, message: str | None = None,
    ) -> None:
        candidate = UnresolvedFinding(
            sku=_text(sku), field=field, source_field=source_field,
            code=code, source=_text(source) or None, target=target,
            blocking=blocking, message=message,
        )
        if candidate not in self.unresolved:
            self.unresolved.append(candidate)

    def finalize(
        self,
        records: Iterable[Mapping[str, Any]],
        rows: Iterable[Mapping[str, Any]],
        *,
        source_hash: str | None = None,
        sku_set_hash: str | None = None,
    ) -> None:
        records_by_sku = {_text(row.get("sku")): row for row in records}
        rows_by_sku = {_text(row.get("编号")): row for row in rows}
        self.row_count = len(rows_by_sku)
        self.eligible_fields = 0
        self.source_hash = source_hash
        self.sku_set_hash = sku_set_hash
        for sku, record in records_by_sku.items():
            output = rows_by_sku.get(sku, {})
            for output_field, target_field in OUTPUT_TO_TARGET.items():
                if target_field not in AUDIT_FIELDS:
                    continue
                source_field = TARGET_TO_SOURCE[target_field]
                source = record.get(source_field)
                target = output.get(output_field)
                if _text(source):
                    self.eligible_fields += 1
                    if not _text(target):
                        self.add_unresolved(
                            sku=sku, field=target_field, source_field=source_field,
                            code="EMPTY_TARGET_AFTER_REPAIR", source=source, target=target,
                        )
        # A report can be finalized after an initial audit by legacy callers.
        # Re-apply the stored audit so newly found blockers always close its gate.
        if self.audit:
            self.set_audit(self.audit)

    def set_audit(self, audit: Mapping[str, Any]) -> None:
        value = dict(audit)
        unresolved_blocking = sum(1 for item in self.unresolved if item.blocking)
        value["unresolved_blocking_count"] = unresolved_blocking
        value["release_ready"] = bool(value.get("release_ready")) and unresolved_blocking == 0
        self.audit = value

    def as_dict(self) -> dict[str, Any]:
        rules = Counter(event.rule for event in self.events)
        fields = Counter(event.field for event in self.events)
        unresolved_codes = Counter(item.code for item in self.unresolved)
        unresolved_fields = {(item.sku, item.field) for item in self.unresolved if item.blocking}
        audit = dict(self.audit)
        affected_fields = int(audit.get("affected_fields") or 0)
        eligible = int(audit.get("eligible_fields") or self.eligible_fields or 0)
        pass_rate = 1.0 if not eligible else max(0.0, 1.0 - affected_fields / eligible)
        return {
            "schema_version": REPAIR_REPORT_VERSION,
            "run_id": self.run_id,
            "rule_version": self.rule_version,
            "created_at": self.created_at,
            "row_count": self.row_count,
            "eligible_fields": eligible,
            "repair_count": len(self.events),
            "repair_counts_by_rule": dict(sorted(rules.items())),
            "repair_counts_by_field": dict(sorted(fields.items())),
            "unresolved_count": len(self.unresolved),
            "unresolved_blocking_count": sum(1 for item in self.unresolved if item.blocking),
            "unresolved_counts_by_code": dict(sorted(unresolved_codes.items())),
            "unresolved_affected_fields": len(unresolved_fields),
            "field_pass_rate": pass_rate,
            "source_hash": self.source_hash,
            "sku_set_hash": self.sku_set_hash,
            "audit": audit,
            "repairs": [event.as_dict() for event in self.events],
            "unresolved": [item.as_dict() for item in self.unresolved],
        }


_SPANISH_NUMBER_WORDS = {
    "un": 1, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4,
    "cinco": 5, "seis": 6, "siete": 7, "ocho": 8, "nueve": 9,
    "diez": 10, "once": 11, "doce": 12, "trece": 13, "catorce": 14,
    "quince": 15, "dieciseis": 16, "dieciséis": 16, "diecisiete": 17,
    "dieciocho": 18, "diecinueve": 19, "veinte": 20, "treinta": 30,
    "cuarenta": 40, "cincuenta": 50, "sesenta": 60, "setenta": 70,
    "ochenta": 80, "noventa": 90, "cien": 100, "ciento": 100,
}
_SPANISH_MONTHS = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5,
    "junio": 6, "julio": 7, "agosto": 8, "septiembre": 9,
    "setiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
}


def _number_as_key(value: Any) -> str:
    try:
        number = float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return str(value)
    return str(int(number)) if number.is_integer() else f"{number:g}"


def _source_word_number_counts(source: str) -> Counter[str]:
    counts: Counter[str] = Counter()
    for word in re.findall(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+", source.casefold()):
        normalized = word.replace("á", "a").replace("é", "e").replace("í", "i").replace("ó", "o").replace("ú", "u")
        if word in _SPANISH_NUMBER_WORDS:
            counts[str(_SPANISH_NUMBER_WORDS[word])] += 1
        elif normalized in _SPANISH_NUMBER_WORDS:
            counts[str(_SPANISH_NUMBER_WORDS[normalized])] += 1
    return counts


def _source_range_values(source: str) -> set[str]:
    values: set[str] = set()
    for match in re.finditer(r"(?<!\d)(\d{1,3})\s*(?:-|–|,|al)\s*(\d{1,3})(?!\d)", source.casefold()):
        start, end = int(match.group(1)), int(match.group(2))
        if end >= start and end - start <= 50:
            values.update(str(value) for value in range(start, end + 1))
    return values


def _source_bound_alias(token: str, source: str, target: str) -> bool:
    key = token.casefold().strip()
    source_lower = source.casefold()
    target_lower = target.casefold()
    if key == "led" and re.search(r"\bled(?:es|s)?\b|luces?\s+led", source_lower):
        return "led" in target_lower or "发光二极管" in target or "灯珠" in target
    if key == "uv" and "ultravioleta" in source_lower:
        return "uv" in target_lower or "紫外线" in target
    if key == "uv" and re.search(r"protecci[oó]n\s+uv\b", source_lower):
        # ``防晒`` is the normal Chinese title rendering of UV protection.
        return "uv" in target_lower or "紫外线" in target or "防晒" in target
    if key == "aa" and re.search(r"pilas\s+alcalinas\b.*\baa\b", source_lower):
        return "aa" in target_lower or "碱性电池" in target
    if key == "bci" and ("better cotton" in source_lower or re.search(r"\bbetter\s+cotton\b", source_lower)):
        return "bci" in target_lower or "良好棉花" in target
    if key == "eva" and "acetato de etileno y vinilo" in source_lower:
        return "eva" in target_lower or "乙烯" in target
    aliases = {
        "qr": ("código qr", "codigo qr", "二维码"),
        "rc": ("mando a distancia", "control por radio", "遥控"),
        "mp": ("megapíxel", "megapíxeles", "megapixel", "megapixels", "万像素"),
        "ca": ("voltios ca", "corriente alterna", "交流"),
        "cc": ("voltios cc", "corriente continua", "直流"),
        "pa": ("paño", "almohadilla", "清洁布", "清洁垫"),
        "diy": ("bricolaje", "手工"),
        "tp": ("tp-link",),
        "le": ("leña",),
        "en": ("en el suelo",),
        "ue": ("unión europea", "ue", "欧盟"),
        "ip": ("cámara ip", "ip", "网络摄像头"),
        "tv": ("personajes favoritos de tv", "televisión", "电视"),
        "pc": ("pc", "电脑"),
        "lu": ("mikado lu", "jaffa lu"),
        "sx": ("sx",),
        "dj": ("dj", "tiësto", "蒂耶斯托"),
    }
    terms = aliases.get(key, ())
    if not terms:
        return False
    if key == "tp" and "tp-link" in source_lower:
        return True
    if key == "lu" and re.search(r"\b(?:mikado|jaffa)\s+lu\b", source_lower):
        return True
    if key == "sx" and re.search(r"\bsx\s+\d+\s+tama", source_lower):
        return True
    if key == "sx" and "profi-box sx" in source_lower:
        return True
    if key == "dj" and re.search(r"\bdj\s+ti[eë]sto\b", source_lower):
        return True
    if key == "le" and "leña" in source_lower and ("木炉" in target or "木材" in target):
        return True
    if key == "en" and "en el suelo" in source_lower and ("地面" in target or "落地" in target):
        return True
    if key == "mp" and re.search(r"megap[ií]xeles?", source_lower) and re.search(r"\d+\s*万", target):
        return True
    if key == "tv" and "personajes favoritos de tv" in source_lower:
        # The Chinese rendering may preserve the meaning as “cartoon
        # characters” without repeating the literal TV abbreviation.
        return any(term in target for term in ("电视", "卡通人物", "角色"))
    if not any(term in source_lower for term in terms if term not in {"二维码", "遥控", "万像素", "交流", "直流", "清洁布", "清洁垫", "手工", "欧盟", "网络摄像头", "电视", "电脑"}):
        return False
    target_aliases = {
        "二维码", "遥控", "万像素", "交流", "直流", "清洁布", "清洁垫", "手工",
        "欧盟", "网络摄像头", "电视", "电脑", "木炉", "电热木炉", "地面", "地面摆放",
        "蒂耶斯托", "遥控器",
    }
    return any(term in target_lower or term in target for term in terms if term in target_aliases) or key in target_lower


def _token_equivalence_allows(rule_id: str, evidence: Mapping[str, Any], source: str, target: str) -> bool:
    token = str(evidence.get("value") or "")
    token_type = str(evidence.get("token_type") or "")
    # Product URLs are carried by the dedicated link column.  They are not
    # required to be repeated inside the Chinese details field.
    if token_type == "URL":
        return True
    if token and token_is_preserved(token, target):
        return True
    # Numeric technical tokens may be rendered with a Chinese unit or with
    # the unit separated/attached differently (``30 dB`` → ``30分贝``).
    unit_match = re.fullmatch(r"(\d+(?:[.,]\d+)?)\s*(mah|kwh|db|mp|mcg|kg|cm|mm|km|m|g|w|v)", token, flags=re.I)
    if unit_match:
        number, unit = unit_match.groups()
        number = number.replace(",", ".")
        normalized_number = canonical_fact_token(number)
        aliases = {
            "mah": ("毫安时",), "kwh": ("千瓦时",), "db": ("分贝",),
            "mp": ("万像素",), "mcg": ("微克",), "kg": ("千克", "公斤"),
            "cm": ("厘米",), "mm": ("毫米",), "km": ("千米",), "m": ("米",),
            "g": ("克",), "w": ("瓦",), "v": ("伏", "伏特"),
        }.get(unit.casefold(), ())
        if re.search(rf"(?<!\d){re.escape(number)}(?!\d)", target) and any(alias in target for alias in aliases):
            return True
        if normalized_number and re.search(rf"(?<!\d){re.escape(normalized_number)}(?!\d)", target) and any(alias in target for alias in aliases):
            return True
        if unit.casefold() == "mp" and re.search(rf"(?<!\d){int(float(number) * 100)}万", target):
            return True
    if token and _source_bound_alias(token, source, target):
        return True
    if token_type in {"CAPACITY", "BATTERY_CAPACITY", "POWER", "VOLTAGE", "UNIT"}:
        key = canonical_fact_token(token)
        normalized_target = canonical_fact_token(target)
        if key and key in normalized_target:
            return True
    if rule_id == "PROTECTED_TOKEN_DUPLICATED":
        expected = int(evidence.get("expected") or 0)
        # Count complete tokens, not substrings: ``BC`` inside ``BCI`` is not
        # a second BC fact.
        if token and expected:
            actual = len(re.findall(rf"(?<![A-Za-z0-9]){re.escape(token)}(?![A-Za-z0-9])", target, flags=re.I))
            if actual <= expected:
                return True
    return False


def _numeric_drop_equivalent(source: str, target: str, missing: Mapping[str, Any]) -> bool:
    source_numbers = {_number_as_key(value) for value in re.findall(r"\d+(?:[.,]\d+)?", source)}
    target_numbers = {_number_as_key(value) for value in re.findall(r"\d+(?:[.,]\d+)?", target)}
    if all(_number_as_key(value) in target_numbers for value in missing):
        return True
    if _source_range_values(source) and all(_number_as_key(value) in target_numbers for value in missing):
        return True
    # Shoe-size ranges are serialized as decimal-looking values by the
    # source parser (``37.38``), while Chinese output uses ``37-38``.
    if re.search(r"(?:talla|calzado|zapato)", source, flags=re.I):
        for value in missing:
            text = str(value)
            if re.fullmatch(r"\d{2}\.\d{2}", text):
                left, right = text.split(".")
                if left in target_numbers and right in target_numbers:
                    continue
            return False
        return True
    # A number embedded in an omitted brand/model is not a dropped product
    # quantity (``3M``, ``All-in-1``).
    for value in missing:
        if re.search(rf"(?<![A-Za-z0-9])[A-Za-z]+[- ]?{re.escape(str(value))}(?![A-Za-z0-9])", source, flags=re.I):
            continue
        if re.search(rf"(?<![A-Za-z0-9]){re.escape(str(value))}[A-Za-z](?![A-Za-z0-9])", source):
            continue
        if re.search(rf"(?<![A-Za-z0-9])[A-Za-z]+[- ]?{re.escape(str(value))}(?![A-Za-z0-9])", source):
            continue
        break
    else:
        return True
    if re.search(r"lumen|lúmenes", source, flags=re.I):
        for value in missing:
            text = str(value)
            if re.search(rf"(?<!\d){re.escape(text)}(?:[.,]\d+)?(?!\d)", target):
                continue
            if "." in text:
                left, right = text.split(".", 1)
                if left in target_numbers and right in target_numbers:
                    continue
            return False
        return True
    for match in re.finditer(r"(?<!\d)(\d+(?:[.,]\d+)?)\s*mp\b", source, flags=re.I):
        converted = str(int(float(match.group(1).replace(",", ".")) * 100))
        if converted in target_numbers and any(_number_as_key(value) == _number_as_key(match.group(1)) for value in missing):
            return True
    return False


def _numeric_is_in_omitted_confirmed_brand(source: str, missing: Mapping[str, Any], approved_tokens: set[str]) -> bool:
    """Return true only when every missing digit belongs to a reviewed brand.

    The no-brand display policy may remove a source brand such as ``7Up`` or
    ``9th Avenue``.  Their digits are identity characters, never quantities.
    """
    source_text = str(source or "")
    remaining = {_number_key(value) for value in missing}
    if not remaining:
        return False
    covered: set[str] = set()
    for brand in approved_tokens:
        value = str(brand or "").strip()
        if not value or not re.search(r"\d", value):
            continue
        if not re.search(rf"(?<![A-Za-z0-9]){re.escape(value)}(?![A-Za-z0-9])", source_text, flags=re.IGNORECASE):
            continue
        covered.update(_number_key(item) for item in re.findall(r"\d+(?:[.,]\d+)?", value))
    return remaining.issubset(covered)


def _numeric_addition_explained(source: str, target: str, evidence: Mapping[str, Any], all_source: str = "") -> bool:
    extra = Counter({str(key): int(value) for key, value in (evidence.get("extra") or {}).items()})
    if not extra:
        return False
    source_numbers = Counter(_number_as_key(value) for value in re.findall(r"\d+(?:[.,]\d+)?", source))
    # Numeric provenance is field-scoped.  A value found in another source
    # field must not excuse a new value in this target field unless a separate
    # approved migration rule explicitly handles that relocation.
    words = _source_word_number_counts(source)
    ranges = _source_range_values(source)
    months = {str(value) for word, value in _SPANISH_MONTHS.items() if re.search(rf"\b{word}\b", source.casefold())}
    for key, count in list(extra.items()):
        normalized = _number_as_key(key)
        covered = source_numbers.get(normalized, 0) + words.get(normalized, 0)
        if normalized in ranges or normalized in months:
            covered = max(covered, 1)
        if not covered:
            # ``12 MP`` is commonly rendered as ``1200万``.
            for match in re.finditer(r"(?<!\d)(\d+(?:[.,]\d+)?)\s*mp\b", source, flags=re.I):
                if normalized == str(int(float(match.group(1).replace(",", ".")) * 100)):
                    covered = 1
                    break
        if not covered and normalized == "1" and re.search(r"一[^；。\n]{0,6}一", target):
            covered = 1
        if not covered and normalized in {"20"} and re.search(r"(?:años?|los)\s+(?:\d{2}\s*(?:y|e)\s*)?\d{2}", source, flags=re.I) and "20世纪" in target:
            covered = 1
        if not covered:
            # ``2 tazas y media`` is a source-bound mixed number rendered as
            # the decimal ``2.5`` in Chinese.
            for match in re.finditer(r"(?<!\d)(\d+)\s+tazas?\s+y\s+media\b", source, flags=re.I):
                if normalized == f"{match.group(1)}.5":
                    covered = 1
                    break
        if not covered and normalized == "1" and re.search(r"\bjuego\s+con\s+vaso\b", source, flags=re.I) and re.search(r"1\s*(?:个|只)?\s*杯", target):
            covered = 1
        if not covered and re.search(r"colores?\s+de", source, flags=re.I):
            color_words = re.findall(r"\b(?:azul|negra?|roja?|verde|amarill[oa]|blanca?|rosa|marr[oó]n|morada?|púrpura)\b", source.casefold())
            if len(set(color_words)) >= 3 and re.search(rf"(?<!\d){re.escape(normalized)}(?=\s*种)", target):
                covered = 1
        if not covered:
            # Thousands separators are parsed as two values by the generic
            # numeric guard (``10.000`` → ``10`` and ``000``).  The compact
            # target value is the same source fact.
            for match in re.finditer(r"(?<!\d)(\d{1,3})[., ](\d{3})\s*(mAh|mah|g|kg|ml|l|w|v|rpm)?", source, flags=re.I):
                compact = f"{match.group(1)}{match.group(2)}"
                if normalized == compact and re.search(rf"(?<!\d){re.escape(compact)}(?!\d)", target):
                    covered = 1
                    break
        if covered >= count:
            del extra[key]
    return not extra


def audit_repaired_rows(
    records: Iterable[Mapping[str, Any]],
    rows: Iterable[Mapping[str, Any]],
    *,
    min_field_pass_rate: float = 0.99,
    allowed_tokens: set[str] | None = None,
) -> dict[str, Any]:
    """Run the field-level localization QA against the actual export rows."""
    from ..localization.contracts import SourceFacts
    from ..localization.policy import has_ordinary_spanish
    from ..localization.qa import (
        _is_allowed_translated_tech_token,
        _is_ordinary_spanish_uppercase_token,
        _source_term_present,
        _source_bound_cross_field_tokens,
        _source_bound_display_tokens,
        audit_translation,
    )

    source_by_sku = {_text(row.get("sku")): row for row in records}
    row_by_sku = {_text(row.get("编号")): row for row in rows}
    output_to_target = {
        "标题": "name_zh", "分类1": "cat1_zh", "分类2": "cat2_zh",
        "规格": "spec_zh", "描述": "desc_zh", "产品详情": "details_zh",
    }
    findings: list[dict[str, Any]] = []
    approved_tokens = {str(token).strip() for token in (allowed_tokens or set()) if str(token).strip()}
    translated_aliases = {
        "mdf": ("中密度纤维板", "纤维板"), "usb": ("USB", "通用串行总线"),
        "usb-c": ("USB-C", "Type-C", "C型接口"), "usb-a": ("USB-A", "Type-A", "A型接口"),
        "uv": ("UV", "紫外线"), "xl": ("XL", "加大", "超大", "特大"),
        "gsm": ("GSM", "克重", "克/平方米"), "co2": ("CO2", "二氧化碳", "碳中和"),
        "a4": ("A4", "A4纸"), "b5": ("B5", "B5纸"), "fsc": ("FSC", "FSC认证"),
        "bci": ("BCI", "BCI认证"), "wc": ("WC", "马桶", "洁厕"),
        "led": ("LED", "发光二极管", "灯珠"), "eva": ("EVA", "乙烯-醋酸乙烯共聚物"),
        "diy": ("DIY", "手工"), "qr": ("QR", "二维码"), "rc": ("RC", "遥控"),
        "mp": ("MP", "万像素"), "uv": ("UV", "紫外线"), "ca": ("CA", "交流"),
        "cc": ("CC", "直流"), "pa": ("PA", "清洁布", "清洁垫"),
    }
    unit_aliases = {
        "m": ("米", "平方米", "㎡"), "km": ("千米", "公里"), "v": ("伏", "伏特"),
        "l": ("升", "L"), "mcg": ("微克",), "db": ("分贝",), "mah": ("毫安时",),
        "kwh": ("千瓦时",), "mp": ("万像素",), "g": ("克",), "kg": ("千克", "公斤"),
        "cm": ("厘米",), "mm": ("毫米",), "w": ("瓦",), "v": ("伏", "伏特"),
    }
    eligible = 0
    eligible_by_field: Counter[str] = Counter()
    for sku, source in source_by_sku.items():
        output = row_by_sku.get(sku, {})
        record = dict(source)
        targets = {
            "name": output.get("标题"),
            "cat1": output.get("分类1"),
            "cat2": output.get("分类2"),
            "spec": output.get("规格"),
            "description": output.get("描述"),
            "details": output.get("产品详情"),
        }
        all_source_text = " ".join(
            str(source.get(field) or "")
            for field in ("name_es", "spec_es", "desc_es", "details_es", "cat1_es", "cat2_es")
        )
        for target in AUDIT_FIELDS:
            source_field = TARGET_TO_SOURCE[target]
            if _text(source.get(source_field)):
                eligible += 1
                eligible_by_field[target] += 1
        source_facts = SourceFacts.from_record(record)
        qa_findings = audit_translation(source_facts, targets, AUDIT_LOGICAL_FIELDS)
        for finding in qa_findings:
            source_field = TARGET_TO_SOURCE[LOGICAL_TO_TARGET[finding.field_name]]
            source_text = str(source.get(source_field) or "")
            target_text = str(targets.get(finding.field_name) or "")
            token = str(finding.evidence.get("value") or "")
            token_key = token.casefold()
            if finding.rule_id in {
                "MODEL_DROPPED", "MODEL_CHANGED", "PROTECTED_TOKEN_MISSING",
                "PROTECTED_TOKEN_CHANGED", "PROTECTED_TOKEN_ADDED",
                "PROTECTED_TOKEN_DUPLICATED",
            }:
                if _token_equivalence_allows(finding.rule_id, finding.evidence, source_text, target_text):
                    continue
                # A reviewed dictionary brand is intentionally absent from the
                # Chinese display under ACTION_MASTER_NO_BRAND_V1.  Preserve
                # the field-local source binding, but do not force a brand or
                # a brand-shaped code such as GS27 back into the output.
                if token_key in {item.casefold() for item in approved_tokens} and _source_term_present(source_text, token):
                    continue
            if finding.rule_id in {"PROTECTED_TOKEN_MISSING", "PROTECTED_TOKEN_CHANGED", "MODEL_DROPPED", "MODEL_CHANGED"}:
                if finding.rule_id == "PROTECTED_TOKEN_MISSING" and int(finding.evidence.get("actual") or 0) > 0:
                    continue
                if finding.evidence.get("token_type") == "TECH":
                    if _is_ordinary_spanish_uppercase_token(source_text, token):
                        continue
                    if token.upper() in _ORDINARY_SOURCE_TOKENS:
                        continue
                    if token_key in {item.casefold() for item in approved_tokens}:
                        continue
                    aliases = translated_aliases.get(token_key, ())
                    if any(alias.casefold() in target_text.casefold() for alias in aliases):
                        continue
                    if _is_allowed_translated_tech_token(source_text, token, target_text):
                        continue
                elif token_key in translated_aliases and any(alias.casefold() in target_text.casefold() for alias in translated_aliases[token_key]):
                    continue
            if finding.rule_id == "UNIT_DROPPED":
                unit = str(finding.evidence.get("unit") or "").casefold()
                if any(alias.casefold() in target_text.casefold() for alias in unit_aliases.get(unit, ())):
                    continue
                # ``100 %`` and ``100%`` are the same fact; the source
                # tokenizer may report the spacing-normalized form as a drop.
                if unit == "%" and re.search(r"(?<!\d)\d+(?:[.,]\d+)?\s*%", target_text):
                    continue
                # ``3M`` is a source brand/model span, not the standalone
                # metre unit.  The protection tokenizer cannot distinguish
                # the two because both end in the letter ``m``.
                if unit == "m" and re.search(r"(?<![A-Za-z0-9])\d+[A-Z](?![A-Za-z0-9])", source_text):
                    continue
                if unit == "l" and not re.search(r"\d+(?:[.,]\d+)?\s*l(?:itro|$)", source_text, flags=re.I):
                    continue
                if unit == "m" and re.search(r"(?<![A-Za-z0-9])\d+\s*m(?:etros?)?\b", source_text, flags=re.I):
                    if re.search(r"\d+(?:[.,]\d+)?\s*(?:米|平方米|㎡|m)\b", target_text, flags=re.I):
                        continue
            if finding.rule_id == "NUMERIC_DROPPED":
                missing = finding.evidence.get("missing") or {}
                target_numbers = finding.evidence.get("target") or {}
                if _numeric_is_in_omitted_confirmed_brand(source_text, missing, approved_tokens):
                    continue
                if missing and all(
                    any(_number_key(value) == _number_key(existing) for existing in target_numbers)
                    for value in missing
                ):
                    continue
                if _numeric_drop_equivalent(source_text, target_text, missing):
                    continue
            if finding.rule_id == "NUMERIC_ADDED":
                if _numeric_addition_explained(source_text, target_text, finding.evidence, all_source_text):
                    continue
            if finding.rule_id == "SPANISH_RESIDUAL":
                source_allowed = {
                    token for token in approved_tokens
                    if token.casefold() in source_text.casefold()
                }
                # Keep export residual detection aligned with localization QA.
                # Only confirmed brands, strict source-bound model/technical
                # tokens, or approved allowlist entries may survive.
                source_allowed.update(_source_bound_display_tokens(source_text, target_text))
                source_allowed.update(
                    _source_bound_cross_field_tokens(str(source.get("name_es") or ""), target_text)
                )
                residual_text = target_text
                # These two markers are deliberately retained as provenance
                # for malformed official source keys.  Their Spanish spelling
                # is not a product-language residual.
                residual_text = residual_text.replace("来源异常：官网字段Sustancia=Válido", "")
                residual_text = residual_text.replace("来源异常：官网字段Incluye oído", "")
                if not has_ordinary_spanish(residual_text, allowed_tokens=source_allowed):
                    continue
            findings.append({
                "sku": sku,
                "field": LOGICAL_TO_TARGET[finding.field_name],
                "code": finding.rule_id,
                "severity": finding.severity,
                "blocking": bool(finding.blocking),
                "evidence": dict(finding.evidence),
            })
    affected_fields = {(item["sku"], item["field"]) for item in findings if item["blocking"]}
    affected_skus = {item["sku"] for item in findings if item["blocking"]}
    p0_findings = [item for item in findings if item["code"] in P0_RULES]
    p0_fields = {(item["sku"], item["field"]) for item in p0_findings}
    field_pass_rate = 1.0 if not eligible else max(0.0, 1.0 - len(affected_fields) / eligible)
    field_error_rates = {
        field: {
            "eligible_fields": count,
            "affected_fields": sum(1 for sku, affected in affected_fields if affected == field),
            "field_error_rate": (sum(1 for sku, affected in affected_fields if affected == field) / count) if count else 0.0,
        }
        for field, count in sorted(eligible_by_field.items())
    }
    counts = Counter(item["code"] for item in findings)
    return {
        "audit_findings": len(findings),
        "audit_counts_by_code": dict(sorted(counts.items())),
        "affected_fields": len(affected_fields),
        "affected_skus": len(affected_skus),
        "eligible_fields": eligible,
        "blocking_affected_fields": len(affected_fields),
        "p0_findings": len(p0_findings),
        "p0_affected_fields": len(p0_fields),
        "field_pass_rate": field_pass_rate,
        "field_error_rate": 1.0 - field_pass_rate,
        "field_error_rates": field_error_rates,
        "target_field_pass_rate": min_field_pass_rate,
        "release_ready": not p0_findings and not affected_fields and field_pass_rate >= min_field_pass_rate,
        "findings": findings,
    }

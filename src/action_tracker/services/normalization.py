"""数据归一化：价格解析、日期、Canonical_ID 等。

Action 西班牙站使用西语小数逗号格式，例如 "3,99 €"、"1.234,56 €/ud."。
必须把字符串稳健地转成 float / date，保证 Excel 中价格是真数值、日期是真日期。
"""
from __future__ import annotations

import datetime as dt
import html
import re
from typing import Any


_HTML_TAG_RE = re.compile(r"<[^>]*>")
_UI_SPEC_RE = re.compile(
    r"^\s*(?:añadir\s+a\s+tus\s+favoritos|todo\s+de\s+.+?)\s*$",
    re.IGNORECASE,
)

# Detail rows are sometimes flattened as ``Key; Value`` or ``Key<TAB>Value``.
# Restrict repairs to known official labels so arbitrary Spanish prose is never
# reinterpreted as a key/value pair.
_DETAIL_KEYS = tuple(sorted({
    "Ancho", "Altura", "Capacidad", "Cantidad", "Color", "Color suave",
    "Contenido", "Destinado a", "Diámetro", "Diámetro del cable", "Forma",
    "Longitud", "Longitud del cable", "Material", "Método de fijación",
    "Número de artículo", "Número del artículo", "Número de piezas",
    "Número de pilas necesarias", "Peso", "Potencia", "Tipo", "Tipo de batería",
    "Tipo de dispensador", "Tipo de accesorio para el cabello",
    "Tipo de material para fabricación de joyas", "Tipo de agarre",
    "Tipo de abrazadera", "Tipo de ambientador / desodorante", "Tipo de producto",
    "Incluye abalorios", "Incluye cable", "Incluye caja de almacenaje", "Incluye cierre",
    "Incluye tenazas", "Incluye recarga", "Tamaño", "Tensión", "Voltaje", "Volumen",
    "Edad recomendada", "Advertencias de seguridad", "Contenido del paquete", "Incluye",
}, key=len, reverse=True))
_DETAIL_KEY_RE = re.compile(
    r"^(?P<key>" + "|".join(re.escape(key) for key in _DETAIL_KEYS) + r")\s*:?(?P<value>.*)$",
    re.IGNORECASE,
)


def _normalize_detail_pairs(text: str) -> str:
    """Repair safe detail delimiters without removing duplicate official facts."""
    tokens = [
        re.sub(r"\s+", " ", token).strip()
        for line in text.split("\n")
        for token in re.split(r";|\t", line)
        if re.sub(r"\s+", " ", token).strip()
    ]
    output: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        match = _DETAIL_KEY_RE.match(token)
        if not match:
            output.append(token)
            index += 1
            continue
        key = match.group("key").strip()
        value = match.group("value").strip()
        # Bare generic labels must not turn a value such as "Tipo de..." into
        # an invented two-column detail row.
        if key.casefold() in {"tipo", "incluye"} and value and not re.match(
            rf"^{re.escape(key)}\s*:", token, re.IGNORECASE
        ):
            output.append(token)
            index += 1
        elif value:
            output.append(f"{key}: {value}")
            index += 1
        elif index + 1 < len(tokens) and not _DETAIL_KEY_RE.match(tokens[index + 1]):
            output.append(f"{key}: {tokens[index + 1]}")
            index += 2
        else:
            output.append(f"{key}:")
            index += 1
    return "; ".join(output)


def normalize_official_text(value: Any, *, field: str = "") -> str | None:
    """Remove Action transport/UI residue while preserving official Spanish facts.

    This is deliberately mechanical: it never translates, deduplicates fields,
    or fills values that are absent from the official page.
    """
    if value is None:
        return None
    text = html.unescape(str(value)).replace("\r\n", "\n").replace("\r", "\n")
    if field == "spec" and _UI_SPEC_RE.fullmatch(text):
        return None
    if re.search(r"</?\w|>\s*>", text):
        def replacement(match: re.Match[str]) -> str:
            tag = match.group(0).casefold()
            return "\n" if tag.startswith(("<p", "</p", "<div", "</div", "<br")) else ""
        text = _HTML_TAG_RE.sub(replacement, text).replace(">", "").replace("<", "")
    lines: list[str] = []
    for line in text.split("\n"):
        line = re.sub(r"[ \t]+", " ", line).strip()
        if not line:
            continue
        if field == "description" and re.fullmatch(r"descripci[oó]n|leer\s+m[aá]s", line, re.IGNORECASE):
            continue
        lines.append(line)
    text = "\n".join(lines).strip()
    text = re.sub(r"^(?:null|undefined)\.?\s*", "", text, flags=re.IGNORECASE)
    if field == "description":
        text = re.sub(r"\s*(?:leer\s+m[aá]s)\s*$", "", text, flags=re.IGNORECASE)
        text = re.sub(r"(?:>\s*){2,}", "", text)
    if field == "details":
        text = re.sub(r"\s*::+\s*", ": ", text)
        text = re.sub(r"\s*;\s*", "; ", text)
        text = _normalize_detail_pairs(text)
        text = re.sub(r"\s*:\s*;", ":", text)
        text = re.sub(r";\s*;", "; ", text)
    return text or None

# ---- Canonical_ID：ACT + SKU 前补零到 7 位（与现有 Master 一致）----

def canonical_id(sku: Any) -> str:
    return f"ACT{str(sku).strip().zfill(7)}"


# ---- 价格解析 ----

_DECIMAL_SEP_RE = re.compile(r"[.,]")
_PRICE_CLEAN_RE = re.compile(r"[^\d.,\-]+")


def parse_price(text: Any) -> float | None:
    """解析西语/欧元价格字符串为 float；无法解析返回 None。

    - "3,99 €"        -> 3.99
    - "3,99 €/ud."    -> 3.99
    - "4,95"          -> 4.95
    - "18"            -> 18.0
    - "1.234,56 €"    -> 1234.56
    - "" / "N/A"      -> None
    """
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text)
    s = str(text).strip()
    if not s or s.lower() in {"nan", "none", "n/a", "-"}:
        return None
    # 移除货币符号、单位、空格
    s = _PRICE_CLEAN_RE.sub("", s)
    # 单位缩写（如 "€/ud."）会残留尾部点号，剥掉（不影响真小数 "3.99"）
    s = s.rstrip(".")
    if not s:
        return None
    # 逗号/小数点同时出现时，以最后一个为小数点
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "").replace(".", ".")
    elif "," in s:
        # 可能是千分位（西语 "1.234,56" 已在上分支），这里逗号按小数
        s = s.replace(",", ".")
    else:
        # 只有小数点：一个点且后面 1-2 位按小数；否则按千分位删除
        parts = s.split(".")
        if len(parts) == 2 and 1 <= len(parts[1]) <= 2:
            pass
        else:
            s = s.replace(".", "")
    # 处理可能残留的负号（如 -19% 不应走到这）
    try:
        v = float(s)
    except ValueError:
        return None
    if v != v:  # NaN
        return None
    return v


def parse_discount_percent(text: Any) -> float | None:
    """解析折扣百分比，如 "-19%" -> -0.19（负号表示折扣）。"""
    if text is None:
        return None
    s = str(text).strip().replace("%", "").replace(" ", "")
    if not s or s.lower() in {"nan", "none"}:
        return None
    try:
        v = float(s.replace(",", "."))
    except ValueError:
        return None
    # 统一为负值表示降价百分比，如 -19% -> -0.19
    return abs(v) / 100.0 * (-1 if "-" in str(text) else 1) if v else 0.0


# ---- 日期 ----

def parse_date(text: Any) -> dt.date | None:
    """解析 "2026-01-09"、Excel date、datetime 为 date。失败返回 None。"""
    if text is None or text == "":
        return None
    if isinstance(text, dt.datetime):
        return text.date()
    if isinstance(text, dt.date):
        return text
    if isinstance(text, (int, float)):
        # Excel 序列号
        try:
            base = dt.date(1899, 12, 30)
            return base + dt.timedelta(days=int(text))
        except Exception:
            return None
    s = str(text).strip()
    # Master 及 snapshot 会写入 ISO datetime，例如 2026-08-25T00:00:00。
    # 先走 fromisoformat，避免把真实最后观测日期解析为空。
    iso = s[:-1] + "+00:00" if s.endswith("Z") else s
    try:
        return dt.datetime.fromisoformat(iso).date()
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def fmt_date(d: dt.date | str | None) -> str | None:
    """统一输出 "YYYY-MM-DD"。"""
    if d is None:
        return None
    if isinstance(d, dt.datetime):
        return d.date().isoformat()
    if isinstance(d, dt.date):
        return d.isoformat()
    p = parse_date(d)
    return p.isoformat() if p else None


# ---- 布尔（是/否/None）----

def parse_bool_zh(text: Any) -> bool | None:
    """现有 Master 中 "是"/"否" 字段解析。"""
    if text is None:
        return None
    s = str(text).strip()
    if s == "是":
        return True
    if s == "否":
        return False
    low = s.lower()
    if low in {"true", "1", "yes"}:
        return True
    if low in {"false", "0", "no"}:
        return False
    return None

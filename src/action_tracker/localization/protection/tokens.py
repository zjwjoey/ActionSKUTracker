from __future__ import annotations

import re
from dataclasses import dataclass
from collections import Counter


class ProtectedTokenError(ValueError):
    pass


# Order matters: URLs/SKU/model tokens before generic numbers and units.
_TOKEN_RE = re.compile(
    r"<[^>]+>|https?://[^\s;]+|(?<![A-Za-z0-9])(?:EAN[- ]?\d{8,14})(?![A-Za-z0-9])|(?<![A-Za-z0-9])(?:SKU[- ]?\d{4,}|\d{6,})(?![A-Za-z0-9])|"
    r"(?<![A-Za-z0-9])(?:[A-Z]{1,5}[-/]?[A-Z0-9]{1,8}|[A-Z]{2,}\d+)(?![A-Za-z0-9])|"
    r"(?<![A-Za-z0-9])\d+(?:[.,]\d+)?\s?(?:€|EUR|\$|USD)(?![A-Za-z0-9])|"
    r"(?<![A-Za-z0-9])\d+(?:[.,]\d+)?\s?(?:x|×)\s?\d+(?:[.,]\d+)?(?:\s?(?:x|×)\s?\d+(?:[.,]\d+)?)?(?![A-Za-z0-9])|"
    r"(?<![A-Za-z0-9])\d+(?:[.,]\d+)?\s?(?:-|–)\s?\d+(?:[.,]\d+)?(?![A-Za-z0-9])|"
    r"(?<![A-Za-z0-9])\d+(?:[.,]\d+)?\s?(?:mg|mcg|μg|g|kg|ml|l|cm|mm|m|V|W|Hz|D|%)\b|"
    r"(?<![A-Za-z0-9])\d+(?:[.,]\d+)?(?![A-Za-z0-9])"
)


@dataclass(frozen=True)
class ProtectedText:
    text: str
    tokens: tuple[str, ...]
    # Typed metadata is intentionally additive.  The legacy placeholder text
    # remains stable for existing callers/artifacts, while new callers can
    # inspect the exact fact type and sequence.
    token_types: tuple[str, ...] = ()

    @property
    def typed_tokens(self) -> tuple[tuple[str, str, str], ...]:
        return tuple((f"PROTECTED_{idx:04d}", kind, value)
                     for idx, (kind, value) in enumerate(zip(self.token_types, self.tokens)))


def _token_type(value: str) -> str:
    v = value.strip()
    if v.startswith("http://") or v.startswith("https://"):
        return "URL"
    if v.startswith("<") and v.endswith(">"):
        return "HTML"
    if re.fullmatch(r"EAN[- ]?\d{8,14}", v, re.I):
        return "EAN"
    if re.fullmatch(r"(?:SKU[- ]?\d{4,}|\d{6,})", v, re.I):
        return "SKU"
    if re.fullmatch(r"[A-Z]{1,5}[-/]?[A-Z0-9]{1,8}|[A-Z]{2,}\d+", v):
        return "MODEL" if re.search(r"\d", v) else "TECH"
    if re.search(r"(?:mg|mcg|μg|kg|ml|cm|mm|Hz|V|W|D|%)", v, re.I):
        if "%" in v:
            return "PERCENT"
        if re.search(r"(?:V|W|Hz)$", v, re.I):
            return "VOLTAGE" if re.search(r"V$", v, re.I) else "POWER"
        return "QUANTITY"
    if re.search(r"(?:x|×|[-–])", v) and re.search(r"\d", v):
        return "RANGE" if re.search(r"[-–]", v) else "DIMENSION"
    return "NUMBER" if re.fullmatch(r"\d+(?:[.,]\d+)?", v) else "TECH"


def protect_text(text: str) -> ProtectedText:
    source = str(text or "")
    tokens: list[str] = []
    token_types: list[str] = []

    def repl(match: re.Match[str]) -> str:
        tokens.append(match.group(0))
        token_types.append(_token_type(match.group(0)))
        return f"[[PROTECTED_{len(tokens) - 1:04d}]]"

    return ProtectedText(_TOKEN_RE.sub(repl, source), tuple(tokens), tuple(token_types))


def restore_text(text: str, protected: ProtectedText) -> str:
    result = str(text or "")
    placeholders = re.findall(r"\[\[PROTECTED_(\d{4})\]\]", result)
    expected = [f"{idx:04d}" for idx in range(len(protected.tokens))]
    if placeholders != expected:
        raise ProtectedTokenError(f"PROTECTED_TOKEN_MISMATCH:expected={expected}:actual={placeholders}")
    for idx, value in enumerate(protected.tokens):
        result = result.replace(f"[[PROTECTED_{idx:04d}]]", value)
    return result


def validate_roundtrip(text: str, protected: ProtectedText) -> None:
    """Fail closed when a model drops, duplicates, or reorders protected facts."""
    placeholders = re.findall(r"\[\[PROTECTED_(\d{4})\]\]", str(text or ""))
    expected = [f"{idx:04d}" for idx in range(len(protected.tokens))]
    if placeholders != expected:
        raise ProtectedTokenError(
            f"PROTECTED_TOKEN_ROUNDTRIP_MISMATCH:expected={expected}:actual={placeholders}"
        )


def token_counts(protected: ProtectedText) -> Counter[str]:
    """Expose typed multiplicity for QA/audit without changing placeholders."""
    return Counter(protected.token_types)

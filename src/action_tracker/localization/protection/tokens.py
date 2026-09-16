from __future__ import annotations

import re
from dataclasses import dataclass


class ProtectedTokenError(ValueError):
    pass


# Order matters: URLs/SKU/model tokens before generic numbers and units.
_TOKEN_RE = re.compile(
    r"https?://[^\s;]+|(?<![A-Za-z0-9])(?:SKU[- ]?\d{4,}|\d{6,})(?![A-Za-z0-9])|"
    r"(?<![A-Za-z0-9])(?:[A-Z]{1,5}[-/]?[A-Z0-9]{1,8}|[A-Z]{2,}\d+)(?![A-Za-z0-9])|"
    r"(?<![A-Za-z0-9])\d+(?:[.,]\d+)?\s?(?:mg|mcg|μg|g|kg|ml|l|cm|mm|m|V|W|Hz|D|%)\b|"
    r"(?<![A-Za-z0-9])\d+(?:[.,]\d+)?(?![A-Za-z0-9])"
)


@dataclass(frozen=True)
class ProtectedText:
    text: str
    tokens: tuple[str, ...]


def protect_text(text: str) -> ProtectedText:
    source = str(text or "")
    tokens: list[str] = []

    def repl(match: re.Match[str]) -> str:
        tokens.append(match.group(0))
        return f"[[PROTECTED_{len(tokens) - 1:04d}]]"

    return ProtectedText(_TOKEN_RE.sub(repl, source), tuple(tokens))


def restore_text(text: str, protected: ProtectedText) -> str:
    result = str(text or "")
    placeholders = re.findall(r"\[\[PROTECTED_(\d{4})\]\]", result)
    expected = {f"{idx:04d}" for idx in range(len(protected.tokens))}
    actual = set(placeholders)
    if actual != expected:
        raise ProtectedTokenError(f"PROTECTED_TOKEN_MISMATCH:expected={sorted(expected)}:actual={sorted(actual)}")
    for idx, value in enumerate(protected.tokens):
        result = result.replace(f"[[PROTECTED_{idx:04d}]]", value)
    return result

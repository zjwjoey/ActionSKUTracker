"""Structured, lossless parsing for Action product-details text.

The parser is deliberately not a translation engine.  It separates the
official Spanish key/value rows so reviewers can compare key semantics and
values independently while retaining order and duplicate keys.  Conflicting
values for the same key are reported, never silently merged.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from ..services.normalization import normalize_official_text


@dataclass(frozen=True)
class DetailPair:
    position: int
    key_es: str
    value_es: str
    raw: str

    @property
    def normalized_key(self) -> str:
        return " ".join(self.key_es.casefold().split())

    @property
    def normalized_value(self) -> str:
        return " ".join(self.value_es.casefold().split())


def parse_details(value: Any) -> tuple[DetailPair, ...]:
    """Parse normalized detail rows without reordering or deduplicating."""
    raw_text = "" if value is None else str(value)
    # Browser exports frequently flatten a two-column row into Key<TAB>Value.
    # Restore only that explicit boundary before the official text normalizer;
    # no semantic replacement is performed here.
    tab_lines = []
    for line in re.split(r"\r?\n", raw_text):
        parts = [part.strip() for part in line.split("\t") if part.strip()]
        tab_lines.append(": ".join(parts) if len(parts) == 2 else line)
    text = normalize_official_text("\n".join(tab_lines), field="details")
    if not text:
        return ()
    pairs: list[DetailPair] = []
    for position, segment in enumerate(_segments(text)):
        raw = segment.strip()
        if not raw:
            continue
        if ":" in raw:
            key, item = raw.split(":", 1)
            key = key.strip()
            item = item.strip()
        else:
            # Preserve an unstructured official fragment instead of guessing
            # a key.  It remains visible to the semantic reviewer.
            key, item = raw, ""
        pairs.append(DetailPair(position, key, item, raw))
    return tuple(pairs)


def conflicting_keys(pairs: tuple[DetailPair, ...] | list[DetailPair]) -> tuple[str, ...]:
    """Return keys with two different non-empty official values."""
    values: dict[str, set[str]] = {}
    display: dict[str, str] = {}
    for pair in pairs:
        if not pair.normalized_value:
            continue
        values.setdefault(pair.normalized_key, set()).add(pair.normalized_value)
        display.setdefault(pair.normalized_key, pair.key_es)
    return tuple(display[key] for key in sorted(values) if len(values[key]) > 1)


def render_details(pairs: tuple[DetailPair, ...] | list[DetailPair]) -> str:
    """Render the parsed source in original order, including duplicates."""
    rendered = []
    for pair in pairs:
        rendered.append(f"{pair.key_es}: {pair.value_es}" if pair.value_es else pair.key_es)
    return "; ".join(rendered)


def _segments(text: str) -> list[str]:
    segments: list[str] = []
    for line in re.split(r"\r?\n", text):
        for part in line.split("|"):
            for item in part.split(";"):
                item = item.strip()
                if item:
                    segments.append(item)
    return segments

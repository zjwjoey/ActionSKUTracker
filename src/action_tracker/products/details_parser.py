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
        separator = re.search(r"[:：]", raw)
        if separator is not None:
            key = raw[:separator.start()]
            item = raw[separator.end():]
            key = key.strip()
            item = item.strip()
        else:
            # Preserve an unstructured official fragment instead of guessing
            # a key.  It remains visible to the semantic reviewer.
            key, item = raw, ""
        pairs.append(DetailPair(position, key, item, raw))
    return tuple(pairs)


# These tokens are presentation headings emitted by the official site rather
# than product attributes.  Keep this intentionally small: semantic review
# must not silently discard an unrecognised source fragment.
_STRUCTURAL_DETAIL_HEADERS = frozenset({"especificaciones"})


def parse_semantic_detail_pairs(value: Any) -> tuple[DetailPair, ...]:
    """Return a comparison-only view of details for audit alignment.

    ``parse_details`` is the authoritative, lossless parser used for repair
    previews and rendering.  Some historic site exports, however, flatten
    a two-column detail row as ``Key; Value`` and include a bare
    ``Especificaciones`` heading.  Counting those fragments as independent
    attributes creates false source/target pair-count mismatches in a
    read-only audit.

    This helper removes only the known heading and joins adjacent *bare*
    fragments into a single key/value pair.  It preserves order and duplicate
    rows, never changes the source text, and deliberately leaves a lone bare
    fragment for human review instead of guessing its meaning.  It must not be
    used to render or repair product details.
    """
    raw_pairs = parse_details(value)
    semantic_pairs: list[DetailPair] = []
    index = 0
    while index < len(raw_pairs):
        current = raw_pairs[index]
        if not current.value_es and current.normalized_key in _STRUCTURAL_DETAIL_HEADERS:
            index += 1
            continue

        # Old exports encode one logical row as two delimiter-separated bare
        # fragments: ``Código de batería; AA``.  Coalesce only an adjacent
        # pair; a lone fragment remains explicit evidence for semantic review.
        if (
            not current.value_es
            and index + 1 < len(raw_pairs)
            and not raw_pairs[index + 1].value_es
            and raw_pairs[index + 1].normalized_key not in _STRUCTURAL_DETAIL_HEADERS
        ):
            following = raw_pairs[index + 1]
            semantic_pairs.append(DetailPair(
                position=current.position,
                key_es=current.key_es,
                value_es=following.key_es,
                raw=f"{current.raw}; {following.raw}",
            ))
            index += 2
            continue

        semantic_pairs.append(current)
        index += 1
    return tuple(semantic_pairs)


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


def render_details(
    pairs: tuple[DetailPair, ...] | list[DetailPair],
    *, separators: tuple[str, ...] | list[str] | None = None,
) -> str:
    """Render detail pairs in order, optionally using original delimiters."""
    rendered = []
    for pair in pairs:
        rendered.append(f"{pair.key_es}: {pair.value_es}" if pair.value_es else pair.key_es)
    if separators is not None and len(separators) == max(0, len(rendered) - 1):
        output = rendered[0] if rendered else ""
        for separator, segment in zip(separators, rendered[1:], strict=True):
            output += separator + segment
        return output
    return "; ".join(rendered)


def _segments(text: str) -> list[str]:
    segments: list[str] = []
    for line in re.split(r"\r?\n", text):
        for item in re.split(r"[;；|｜]", line):
            item = item.strip()
            if item:
                segments.append(item)
    return segments

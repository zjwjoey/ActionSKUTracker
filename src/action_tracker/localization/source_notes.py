"""Recognize verbatim, own-field source anomaly evidence in structured details.

This masks evidence only for language detection. It does not waive source
quality, approval, freshness, numeric or semantic gates, or mutate the text.
"""
from collections import Counter
import re

from .normalization.structured_details import parse_structured_details


_NOTE = re.compile(
    r"(?P<boundary>^|[;；|｜\n])(?P<space>\s*)"
    r"(?P<note>来源异常：官网字段(?P<key>Incluye oído)=(?P<value>Sí|No))"
    r"(?=\s*(?:$|[;；|｜\n]))"
)


def mask_verified_detail_source_notes(source: str, target: str) -> str:
    """Mask complete matched key/value annotations, never ordinary source words.

    Only the reviewed malformed label ``Incluye oído`` is supported. Its Sí/No
    value and multiplicity must match the same details source. Unknown labels,
    changed values, fragments, prose suffixes and cross-field evidence stay
    visible to residual detection. The original annotation remains in exports.
    """
    text = str(target or "")
    pairs = Counter(
        (p.key, p.value) for p in parse_structured_details(str(source or ""))
        if p.key == "Incluye oído" and p.value in {"Sí", "No"}
    )
    notes = Counter((m['key'], m['value']) for m in _NOTE.finditer(text))

    def replace(match):
        pair = (match['key'], match['value'])
        if pairs[pair] and pairs[pair] == notes[pair]:
            return match['boundary'] + match['space']
        return match.group(0)

    return _NOTE.sub(replace, text)


def repair_legacy_detail_source_notes(source: str, target: str) -> str:
    """Preserve a known anomalous key/value when replacing a legacy inference.

    Replaces one complete old handle/ear Boolean pair only when its value agrees
    with the single own-field source pair. Never appends missing facts or masks
    polarity mismatches; unrecognized/multiple pairs remain for review.
    """
    text = str(target or "")
    pairs = [p for p in parse_structured_details(str(source or "")) if p.key == 'Incluye oído']
    if len(pairs) != 1 or pairs[0].value not in {'Sí', 'No'}:
        return text
    pattern = re.compile(
        r'(?P<boundary>^|[;；|｜\n])(?P<space>\s*)'
        r'(?:是否带把手|带把手|是否带耳)：(?P<value>是|否)'
        r'(?=\s*(?:$|[;；|｜\n]))'
    )
    matches = list(pattern.finditer(text))
    if len(matches) != 1 or matches[0]['value'] != ('是' if pairs[0].value == 'Sí' else '否'):
        return text
    return pattern.sub(
        lambda m: m['boundary'] + m['space'] + '来源异常：官网字段Incluye oído=' + pairs[0].value,
        text,
    )

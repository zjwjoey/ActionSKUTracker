"""Source-bound, context-aware terminology repair for structured details.

Rules only alter a Chinese detail pair when its position maps to a parsed
Spanish source pair. Unknown or structurally mismatched content stays intact
for model or human review.
"""
from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..products.details_parser import DetailPair, parse_details, render_details
from .model_guard import numeric_tokens, technical_tokens


class DetailTerminologyConfigError(ValueError):
    """The versioned detail terminology rules are malformed or incomplete."""


def validate_detail_terminology_rules(rules: Mapping[str, Any]) -> None:
    """Fail closed on malformed rule sections instead of silently skipping them."""
    if not isinstance(rules, Mapping) or not str(rules.get("version") or "").strip():
        raise DetailTerminologyConfigError("DETAIL_RULES_VERSION_MISSING")
    metadata = rules.get("rule_metadata")
    if not isinstance(metadata, Mapping) or metadata.get("evidence_status") != "OWNER_CONFIRMED":
        raise DetailTerminologyConfigError("DETAIL_RULES_EVIDENCE_METADATA_INVALID")
    if metadata.get("unknown_value_action") != "REVIEW_REQUIRED":
        raise DetailTerminologyConfigError("DETAIL_RULES_UNKNOWN_VALUE_POLICY_INVALID")
    required_maps = ("key_translations", "candidate_key_aliases")
    for section in required_maps:
        value = rules.get(section)
        if not isinstance(value, Mapping):
            raise DetailTerminologyConfigError(f"DETAIL_RULES_SECTION_INVALID:{section}")
        for key, translation in value.items():
            if not isinstance(key, str) or not key.strip():
                raise DetailTerminologyConfigError(f"DETAIL_RULE_KEY_INVALID:{section}")
            if section == "key_translations":
                if not isinstance(translation, str) or not translation.strip():
                    raise DetailTerminologyConfigError(f"DETAIL_RULE_VALUE_INVALID:{section}:{key}")
            elif (
                not isinstance(translation, list)
                or not translation
                or any(not isinstance(item, str) or not item.strip() for item in translation)
            ):
                raise DetailTerminologyConfigError(f"DETAIL_RULE_VALUE_INVALID:{section}:{key}")

    closed_enum_keys = rules.get("closed_enum_keys")
    if (
        not isinstance(closed_enum_keys, list)
        or any(not isinstance(key, str) or not key.strip() for key in closed_enum_keys)
    ):
        raise DetailTerminologyConfigError("DETAIL_RULES_SECTION_INVALID:closed_enum_keys")
    known_source_keys = {
        _norm(key) for key in rules.get("key_translations", {})
    } | {
        _norm(row.get("source_key")) for section in ("contextual_key_translations", "value_translations", "numeric_suffix_rules")
        for row in rules.get(section, ()) if isinstance(row, Mapping) and row.get("source_key")
    }
    if any(_norm(key) not in known_source_keys for key in closed_enum_keys):
        raise DetailTerminologyConfigError("DETAIL_RULES_CLOSED_ENUM_KEY_UNKNOWN")

    for section in ("contextual_key_translations", "value_translations", "numeric_suffix_rules"):
        value = rules.get(section)
        if not isinstance(value, list):
            raise DetailTerminologyConfigError(f"DETAIL_RULES_SECTION_INVALID:{section}")
        for index, row in enumerate(value):
            if not isinstance(row, Mapping) or not isinstance(row.get("source_key"), str) or not row["source_key"].strip():
                raise DetailTerminologyConfigError(f"DETAIL_RULE_ROW_INVALID:{section}:{index}")
            if section == "contextual_key_translations":
                if (
                    not isinstance(row.get("target_key"), str) or not row["target_key"].strip()
                    or not isinstance(row.get("name_es_any"), list) or not row["name_es_any"]
                    or any(not isinstance(item, str) or not item.strip() for item in row["name_es_any"])
                ):
                    raise DetailTerminologyConfigError(f"DETAIL_RULE_ROW_INVALID:{section}:{index}")
            elif section == "value_translations":
                has_source_selector = any(
                    isinstance(row.get(key), str) and row[key].strip()
                    for key in ("source_value", "source_value_contains")
                )
                target_contains = row.get("target_value_contains", {})
                has_target = (
                    isinstance(row.get("target_value"), str) and bool(row["target_value"].strip())
                ) or (
                    isinstance(target_contains, Mapping) and bool(target_contains)
                    and all(
                        isinstance(source, str) and source
                        and isinstance(target, str) and target
                        for source, target in target_contains.items()
                    )
                )
                if not has_source_selector or not has_target:
                    raise DetailTerminologyConfigError(f"DETAIL_RULE_ROW_INVALID:{section}:{index}")
                choices = row.get("candidate_value_any", [])
                contexts = row.get("name_es_any", [])
                if (
                    not isinstance(choices, list) or any(not isinstance(item, str) or not item.strip() for item in choices)
                    or not isinstance(contexts, list) or any(not isinstance(item, str) or not item.strip() for item in contexts)
                ):
                    raise DetailTerminologyConfigError(f"DETAIL_RULE_ROW_INVALID:{section}:{index}")
            else:
                if any(not isinstance(row.get(key), str) or not row[key].strip() for key in ("target_key", "suffix")):
                    raise DetailTerminologyConfigError(f"DETAIL_RULE_ROW_INVALID:{section}:{index}")


def load_detail_terminology_rules(path: Path) -> dict[str, Any]:
    try:
        rules = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DetailTerminologyConfigError(f"DETAIL_RULES_READ_FAILED:{path}") from exc
    if not isinstance(rules, dict):
        raise DetailTerminologyConfigError("DETAIL_RULES_ROOT_INVALID")
    validate_detail_terminology_rules(rules)
    return rules


def _norm(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = "".join(char for char in unicodedata.normalize("NFD", text) if unicodedata.category(char) != "Mn")
    return " ".join(text.split())


def detail_rule_context_matches(rule: Mapping[str, Any], context: Mapping[str, Any]) -> bool:
    """Return whether a context-scoped rule applies to this product context."""
    choices = rule.get("name_es_any")
    if not choices:
        return True
    name = _norm(context.get("name_es"))
    for choice in choices:
        token = _norm(choice)
        if not token:
            continue
        variants = {token, f"{token}s", f"{token}es"}
        if any(re.search(rf"(?<![a-z0-9]){re.escape(variant)}(?![a-z0-9])", name) for variant in variants):
            return True
    return False


def detail_value_rule_matches_source(
    rule: Mapping[str, Any], source_key: object, source_value: object,
    *, context: Mapping[str, Any] | None = None,
) -> bool:
    """Check source-key/value selectors and the same context used by runtime repair."""
    if _norm(rule.get("source_key")) != _norm(source_key):
        return False
    if not detail_rule_context_matches(rule, context or {}):
        return False
    normalized_value = _norm(source_value)
    exact, contains = rule.get("source_value"), rule.get("source_value_contains")
    if exact is not None and normalized_value != _norm(exact):
        return False
    if contains is not None and _norm(contains) not in normalized_value:
        return False
    return exact is not None or contains is not None


def find_unmapped_closed_enum_values(
    source_es: object,
    rules: Mapping[str, Any],
    *,
    context: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Find closed-enum source pairs without an exact/contextual value rule.

    This source-only preflight lets Stage 5 route a known but unmapped enum to
    a human before spending a model request on a field that cannot be
    automatically accepted. It never proposes or changes a translation.
    """
    source_pairs = parse_details(source_es)
    context = context or {}
    closed_keys = {_norm(key) for key in rules.get("closed_enum_keys", ())}
    value_rules = list(rules.get("value_translations") or ())
    numeric_rules = {
        _norm(row.get("source_key")): row
        for row in rules.get("numeric_suffix_rules", ())
    }
    findings: list[dict[str, Any]] = []
    for index, pair in enumerate(source_pairs):
        source_key, source_value = _norm(pair.key_es), _norm(pair.value_es)
        if source_key not in closed_keys:
            continue
        matched = False
        for rule in value_rules:
            if not detail_value_rule_matches_source(
                rule, source_key, pair.value_es, context=context,
            ):
                continue
            matched = True
            break
        numeric_rule = numeric_rules.get(source_key)
        numeric_supported = bool(
            numeric_rule and re.fullmatch(r"\d+(?:[.,]\d+)?", pair.value_es.strip())
        )
        if not matched and not numeric_supported:
            findings.append({
                "code": "DETAIL_SOURCE_VALUE_UNMAPPED_REVIEW",
                "pair_index": index,
                "source_key": pair.key_es,
                "source_value": pair.value_es,
                "review_only": True,
            })
    return findings


def resolve_detail_from_rules(
    source_es: object,
    rules: Mapping[str, Any],
    *,
    context: Mapping[str, Any] | None = None,
) -> str | None:
    """Translate a complete detail cell only when every pair is rule-covered.

    This is a deterministic rule-first path, not a partial translator: if any
    key/value or context is unknown, return ``None`` so the caller can route
    the whole cell to its existing model or review path without mixing
    deterministic fragments with guessed content.
    """
    pairs = parse_details(source_es)
    if not pairs:
        return None
    context = context or {}
    key_map = {_norm(key): str(value) for key, value in (rules.get("key_translations") or {}).items()}
    contextual_key_rules = {
        _norm(rule.get("source_key")): rule
        for rule in rules.get("contextual_key_translations", ())
        if _norm(rule.get("source_key"))
    }
    value_rules = list(rules.get("value_translations") or ())
    numeric_rules = {
        _norm(rule.get("source_key")): rule
        for rule in rules.get("numeric_suffix_rules", ())
        if _norm(rule.get("source_key"))
    }
    translated: list[DetailPair] = []
    for pair in pairs:
        source_key = _norm(pair.key_es)
        contextual = contextual_key_rules.get(source_key)
        if contextual is not None:
            if not detail_rule_context_matches(contextual, context):
                return None
            target_key = str(contextual.get("target_key") or "")
        else:
            target_key = key_map.get(source_key, "")
        if not target_key or not pair.value_es.strip():
            return None

        matching_rules = [
            rule for rule in value_rules
            if detail_value_rule_matches_source(
                rule, pair.key_es, pair.value_es, context=context,
            )
        ]
        target_values = {
            str(rule.get("target_value"))
            for rule in matching_rules
            if isinstance(rule.get("target_value"), str) and rule["target_value"].strip()
        }
        numeric_rule = numeric_rules.get(source_key)
        numeric_value = pair.value_es.strip()
        if numeric_rule and re.fullmatch(r"\d+(?:[.,]\d+)?", numeric_value):
            suffix = str(numeric_rule.get("suffix") or "")
            numeric_target_key = str(numeric_rule.get("target_key") or target_key)
            if not suffix or len(target_values) > 1:
                return None
            target_values.add(f"{numeric_value}{suffix}")
            target_key = numeric_target_key
        if len(target_values) != 1:
            return None
        translated.append(DetailPair(pair.position, target_key, next(iter(target_values)), pair.raw))
    raw_source = str(source_es or "")
    separators = tuple(re.findall(r"[ \t]*[;；|｜][ \t]*|\r?\n", raw_source))
    if len(separators) != max(0, len(translated) - 1):
        return None
    return render_details(translated, separators=separators)


def repair_detail_candidate(
    source_es: object,
    candidate_zh: object,
    rules: Mapping[str, Any],
    *,
    context: Mapping[str, Any] | None = None,
) -> tuple[str, tuple[str, ...]]:
    """Apply approved key/value rules while preserving pair count and order.

    Returns the unchanged candidate when source and candidate cannot be paired
    one-to-one. Context is used only to disambiguate a source-supported term;
    it never supplies facts to the target field.
    """
    source_pairs = parse_details(source_es)
    target_pairs = parse_details(candidate_zh)
    original = "" if candidate_zh is None else str(candidate_zh)
    if not source_pairs or len(source_pairs) != len(target_pairs):
        return original, ("DETAIL_PAIR_COUNT_MISMATCH",) if source_pairs or target_pairs else ()
    unmapped_closed_enum_indexes = {
        int(item["pair_index"])
        for item in find_unmapped_closed_enum_values(source_es, rules, context=context)
    }

    context = context or {}
    key_map = {_norm(key): str(value) for key, value in (rules.get("key_translations") or {}).items()}
    contextual_rules = {
        _norm(rule.get("source_key")): rule
        for rule in rules.get("contextual_key_translations", ())
        if _norm(rule.get("source_key"))
    }
    for source_key, rule in contextual_rules.items():
        if detail_rule_context_matches(rule, context):
            key_map[source_key] = str(rule.get("target_key") or "")
    key_aliases = {
        _norm(key): {_norm(value) for value in values}
        for key, values in (rules.get("candidate_key_aliases") or {}).items()
    }
    # Confirm positional alignment before applying any rule. Known keys may
    # have a known mistranslation, but an unrelated or reordered key blocks
    # the whole repair so facts cannot migrate between detail rows.
    for source_pair, target_pair in zip(source_pairs, target_pairs, strict=True):
        source_key = _norm(source_pair.key_es)
        contextual_rule = contextual_rules.get(source_key)
        if contextual_rule is not None and not detail_rule_context_matches(contextual_rule, context):
            return original, ("DETAIL_CONTEXT_REQUIRED",)
        canonical_key = key_map.get(source_key)
        if canonical_key is not None:
            allowed_keys = key_aliases.get(source_key, set()) | {_norm(canonical_key)}
            if _norm(target_pair.key_es) not in allowed_keys:
                return original, ("DETAIL_PAIR_ALIGNMENT_UNCERTAIN",)
        source_numbers = sorted(numeric_tokens(source_pair.value_es))
        target_numbers = sorted(numeric_tokens(target_pair.value_es))
        if (source_numbers or target_numbers) and source_numbers != target_numbers:
            return original, ("DETAIL_PAIR_NUMERIC_ALIGNMENT_UNCERTAIN",)
        source_tokens = sorted(technical_tokens(source_pair.value_es))
        target_tokens = sorted(technical_tokens(target_pair.value_es))
        if (source_tokens or target_tokens) and source_tokens != target_tokens:
            return original, ("DETAIL_PAIR_TOKEN_ALIGNMENT_UNCERTAIN",)

    value_rules = list(rules.get("value_translations") or ())
    numeric_rules = {_norm(row.get("source_key")): row for row in rules.get("numeric_suffix_rules", ())}
    replacements: dict[int, tuple[str, str]] = {}
    applied: list[str] = []
    review_flags: list[str] = []

    for index, (source_pair, target_pair) in enumerate(zip(source_pairs, target_pairs, strict=True)):
        source_key = _norm(source_pair.key_es)
        source_value = _norm(source_pair.value_es)
        target_key = target_pair.key_es
        target_value = target_pair.value_es
        new_key = key_map.get(source_key, target_key)
        new_value = target_value

        for rule_index, rule in enumerate(value_rules):
            if not detail_value_rule_matches_source(
                rule, source_key, source_pair.value_es, context=context,
            ):
                continue
            candidate_value_any = rule.get("candidate_value_any")
            if candidate_value_any:
                accepted_values = {_norm(item) for item in candidate_value_any}
            else:
                accepted_values = set()
                if isinstance(rule.get("target_value"), str):
                    accepted_values.add(_norm(rule["target_value"]))
                accepted_values.update(_norm(item) for item in (rule.get("target_value_contains") or {}).keys())
                accepted_values.update(_norm(item) for item in (rule.get("target_value_contains") or {}).values())
            if _norm(target_value) not in accepted_values:
                review_flags.append(f"DETAIL_VALUE_TRANSLATION_UNRECOGNIZED:{rule_index}")
                continue
            target = rule.get("target_value")
            if target is not None:
                new_value = str(target)
            for wrong, right in (rule.get("target_value_contains") or {}).items():
                if wrong in new_value:
                    new_value = new_value.replace(wrong, str(right))
            if new_value != target_value:
                applied.append(f"DETAIL_VALUE_RULE:{index}")

        if index in unmapped_closed_enum_indexes:
            review_flags.append(f"DETAIL_SOURCE_VALUE_UNMAPPED_REVIEW:{index}")

        numeric_rule = numeric_rules.get(source_key)
        if numeric_rule and re.fullmatch(r"\d+(?:[.,]\d+)?", source_pair.value_es.strip()):
            new_key = str(numeric_rule.get("target_key") or new_key)
            number = re.sub(r"\s+", "", target_value)
            if re.fullmatch(r"\d+(?:[.,]\d+)?(?:次)?", number):
                new_value = number if number.endswith("次") else f"{number}{numeric_rule.get('suffix', '')}"
                if new_value != target_value:
                    applied.append(f"DETAIL_NUMERIC_KEY_RULE:{index}")

        if new_key != target_key:
            applied.append(f"DETAIL_KEY_RULE:{index}")
        if new_key != target_key or new_value != target_value:
            replacements[index] = (new_key, new_value)

    if not replacements:
        return original, tuple(dict.fromkeys([*applied, *review_flags]))
    rewritten = _rewrite_candidate_segments(original, target_pairs, replacements)
    if rewritten is None:
        return original, ("DETAIL_PAIR_SPAN_ALIGNMENT_UNCERTAIN",)
    return rewritten, tuple(dict.fromkeys([*applied, *review_flags]))


def _rewrite_candidate_segments(
    original: str,
    target_pairs: tuple[Any, ...],
    replacements: Mapping[int, tuple[str, str]],
) -> str | None:
    """Replace only mapped key/value fragments; preserve every other byte.

    Detail cells can use ASCII/full-width separators, pipes, or line breaks.
    Capturing delimiters avoids normalizing the whole Excel cell when one pair
    is corrected.
    """
    pieces = re.split(r"([;；|｜]|\r?\n)", original)
    segment_indices: list[int] = []
    for index in range(0, len(pieces), 2):
        if parse_details(pieces[index]):
            segment_indices.append(index)
    if len(segment_indices) != len(target_pairs):
        return None

    for pair_index, piece_index in enumerate(segment_indices):
        replacement = replacements.get(pair_index)
        if replacement is None:
            continue
        segment = pieces[piece_index]
        left_padding = segment[:len(segment) - len(segment.lstrip())]
        right_padding = segment[len(segment.rstrip()):]
        body = segment.strip()
        colon = re.search(r"[:：]", body)
        new_key, new_value = replacement
        if colon is None:
            if new_value:
                return None
            new_body = new_key
        else:
            old_key = body[:colon.start()]
            old_tail = body[colon.start():]
            old_value_match = re.match(r"[:：](\s*)(.*)$", old_tail, re.DOTALL)
            if old_value_match is None:
                return None
            separator = old_tail[0] + old_value_match.group(1)
            old_value = old_value_match.group(2)
            trailing_value_space = old_value[len(old_value.rstrip()):]
            rendered_value = new_value + trailing_value_space if new_value else ""
            new_body = f"{new_key}{separator}{rendered_value}"
            if new_key == target_pairs[pair_index].key_es:
                new_body = f"{old_key}{separator}{rendered_value}"
        pieces[piece_index] = f"{left_padding}{new_body}{right_padding}"
    return "".join(pieces)


__all__ = [
    "find_unmapped_closed_enum_values", "repair_detail_candidate", "resolve_detail_from_rules",
]

"""Helpers for reading serialized localization provenance safely."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any


def coerce_field_provenance(value: Any) -> Mapping[str, Mapping[str, Any]]:
    """Return field provenance as a mapping without weakening the export gate.

    Older extraction snapshots persisted ``zh_field_provenance`` as a JSON
    string, while current readers use a dictionary.  Malformed or unexpected
    values are treated as missing provenance so the caller produces a review
    required result instead of crashing or treating a field as approved.
    """

    candidate: Any = value
    if isinstance(candidate, str):
        try:
            candidate = json.loads(candidate)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
    if not isinstance(candidate, Mapping):
        return {}
    return {
        str(field): metadata
        for field, metadata in candidate.items()
        if isinstance(metadata, Mapping)
    }

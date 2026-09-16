from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


# The frozen production contract uses ``desc_es`` (not the display alias
# ``description_es``).  Keep this exact order and delimiter compatible with
# services.hashing.localization_source_hash.
SOURCE_HASH_V1_FIELDS = ("name_es", "cat1_es", "cat2_es", "spec_es", "desc_es", "details_es")
SOURCE_HASH_CONTRACT_VERSION = "SOURCE_HASH_V1"


def canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def localization_source_hash_v1(fields: Mapping[str, Any]) -> str:
    payload = {name: fields.get(name, fields.get("description_es") if name == "desc_es" else None) for name in SOURCE_HASH_V1_FIELDS}
    # Existing source-hash compatibility uses an ordered pipe payload.  Keep
    # the explicit contract and do not silently migrate old hashes.
    hasher = hashlib.sha256()
    for name in SOURCE_HASH_V1_FIELDS:
        value = payload[name]
        if value is None:
            hasher.update(b"\x00")
        else:
            hasher.update(str(value).strip().encode("utf-8", "ignore"))
        hasher.update(b"\x1f")
    return hasher.hexdigest()


def source_hash_payload_v2(fields: Mapping[str, Any]) -> dict[str, Any]:
    return {name: fields.get(name, fields.get("description_es") if name == "desc_es" else None) for name in SOURCE_HASH_V1_FIELDS}


def source_hash_v2(fields: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(source_hash_payload_v2(fields)).encode("utf-8")).hexdigest()


def value_hash(value: Any) -> str:
    return hashlib.sha256(str(value if value is not None else "").encode("utf-8")).hexdigest()

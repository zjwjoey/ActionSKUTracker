from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Iterable

from ..dictionary import (
    BRAND_DICTIONARY_HEADERS, CATEGORY_DICTIONARY_HEADERS, DICTIONARY_BASELINE_FILENAMES,
    OVERRIDE_HEADERS, PRODUCT_DICTIONARY_HEADERS, TERM_DICTIONARY_HEADERS,
    is_confirmed_brand_record, normalize_category_key,
)

NEW_SCHEMAS: dict[str, tuple[str, ...]] = {
    "product_type_dictionary.csv": ("schema_version", "product_type_id", "source_term", "source_aliases", "cat1_es", "cat2_es", "canonical_zh", "confidence", "review_status", "notes"),
    "detail_key_dictionary.csv": ("schema_version", "key_es", "key_zh", "field_group", "value_type", "unit_rule", "review_status", "notes"),
    "tech_token_dictionary.csv": ("schema_version", "token", "canonical_token", "token_type", "keep_original", "normalization_rule", "review_status", "notes"),
    "phrase_dictionary.csv": ("schema_version", "source_phrase", "zh_value", "semantic_type", "preferred_target", "allowed_targets", "category_context", "review_status", "notes"),
}

NEW_KEYS: dict[str, tuple[str, ...]] = {
    "product_type_dictionary.csv": ("product_type_id",),
    "detail_key_dictionary.csv": ("key_es",),
    "tech_token_dictionary.csv": ("token",),
    "phrase_dictionary.csv": ("source_phrase",),
}


class KnowledgeContext(dict[str, Any]):
    """Single read-only-shaped context passed to Planner/Resolver callers.

    It remains a ``dict`` subclass for compatibility with the existing
    resolver APIs while giving integrations an explicit contract name.
    Callers must treat its contents as a snapshot for one policy/knowledge
    version; mutation is not persisted by this class.
    """


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class KnowledgeLoader:
    def __init__(self, directory: Path):
        self.directory = Path(directory)

    def load(self) -> dict[str, Any]:
        result: KnowledgeContext = KnowledgeContext({"brands": set(), "cat1_map": {}, "cat2_map": {}, "product_by_sku": {}, "product_types": {}, "product_type_rows": [], "detail_keys": {}, "tech_tokens": {}, "phrases": {}, "terms": [], "manual_overrides": []})
        brand = self.directory / "brand_dictionary.csv"
        if brand.exists():
            for row in csv.DictReader(brand.open(encoding="utf-8-sig")):
                if not is_confirmed_brand_record(row):
                    continue
                value = str(row.get("brand_name_zh") or row.get("brand_zh") or row.get("brand_name") or row.get("canonical_name") or "").strip()
                aliases = [value, *str(row.get("aliases_es") or "").replace("、", "|").split("|")]
                for alias in aliases:
                    if alias.strip(): result["brands"].add(alias.strip())
        product = self.directory / "product_dictionary.csv"
        if product.exists():
            for row in csv.DictReader(product.open(encoding="utf-8-sig")):
                sku = str(row.get("sku") or "").strip()
                if sku: result["product_by_sku"][sku] = row
        term = self.directory / "term_dictionary.csv"
        if term.exists():
            with term.open(encoding="utf-8-sig", newline="") as fh:
                result["terms"] = [row for row in csv.DictReader(fh) if str(row.get("review_status") or "").upper() in {"HUMAN_REVIEWED", "LOCKED", "APPROVED"}]
        overrides = self.directory / "manual_overrides.csv"
        if overrides.exists():
            with overrides.open(encoding="utf-8-sig", newline="") as fh:
                result["manual_overrides"] = list(csv.DictReader(fh))
        category = self.directory / "category_dictionary.csv"
        if category.exists():
            for row in csv.DictReader(category.open(encoding="utf-8-sig")):
                es1 = str(row.get("cat1_es") or "").strip(); es2 = str(row.get("cat2_es") or "").strip()
                zh1 = str(row.get("cat1_zh") or row.get("cat1_zh_standard") or "").strip(); zh2 = str(row.get("cat2_zh") or row.get("cat2_zh_standard") or "").strip()
                if es1 and zh1:
                    key1 = normalize_category_key(es1)
                    previous = result["cat1_map"].get(key1)
                    if previous and previous != zh1:
                        raise ValueError(f"CATEGORY_DICTIONARY_CONFLICT:{es1}")
                    result["cat1_map"][key1] = zh1
                if es1 and es2 and zh2:
                    key2 = (normalize_category_key(es1), normalize_category_key(es2))
                    previous = result["cat2_map"].get(key2)
                    if previous and previous != zh2:
                        raise ValueError(f"CATEGORY_DICTIONARY_CONFLICT:{es1}|{es2}")
                    result["cat2_map"][key2] = zh2
        for filename, key in (("product_type_dictionary.csv", "product_types"), ("detail_key_dictionary.csv", "detail_keys"), ("tech_token_dictionary.csv", "tech_tokens"), ("phrase_dictionary.csv", "phrases")):
            path = self.directory / filename
            if path.exists():
                validate_knowledge_file(path, NEW_SCHEMAS[filename], NEW_KEYS[filename])
                rows = list(csv.DictReader(path.open(encoding="utf-8-sig")))
                accepted = {"HUMAN_REVIEWED", "LOCKED", "APPROVED"}
                result[key] = [row for row in rows if str(row.get("review_status") or "").upper() in accepted]
                rows = result[key]
                if filename == "product_type_dictionary.csv":
                    result["product_type_rows"] = rows
                    result["product_types"] = {str(row.get("source_term") or "").casefold(): str(row.get("canonical_zh") or "") for row in rows if str(row.get("review_status") or "").upper() in {"HUMAN_REVIEWED", "LOCKED", "APPROVED"}}
                if filename == "tech_token_dictionary.csv":
                    result["tech_tokens"] = {str(row.get("token") or ""): str(row.get("canonical_token") or row.get("token") or "") for row in rows}
        result["hash"] = self.content_hash()
        return result

    def content_hash(self) -> str:
        values = []
        for path in sorted(self.directory.glob("*.csv")):
            values.append((path.name, _sha(path)))
        return hashlib.sha256(json.dumps(values, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def ensure_schemas(directory: Path) -> dict[str, Any]:
    """Create empty, versioned knowledge files without overwriting data."""
    directory = Path(directory); directory.mkdir(parents=True, exist_ok=True)
    created = []
    for filename, headers in NEW_SCHEMAS.items():
        path = directory / filename
        if not path.exists():
            with path.open("w", encoding="utf-8-sig", newline="") as fh:
                csv.DictWriter(fh, fieldnames=headers).writeheader()
            created.append(filename)
    # Validate both newly created and pre-existing files before publishing a
    # manifest.  Duplicate keys would make dictionary resolution order
    # dependent and are therefore a hard failure.
    for filename, headers in NEW_SCHEMAS.items():
        validate_knowledge_file(directory / filename, headers, NEW_KEYS[filename])
    manifest = directory / "localization_manifest.json"
    entries = {name: {"sha256": _sha(directory / name), "schema_version": "1.0"} for name in NEW_SCHEMAS}
    # The governance manifest covers the legacy dictionaries as well.  Their
    # historical schemas remain untouched; only their digest and row count
    # are recorded so old audit semantics are not silently rewritten.
    for name in DICTIONARY_BASELINE_FILENAMES:
        path = directory / name
        if path.exists() and name not in entries:
            with path.open(encoding="utf-8-sig", newline="") as fh:
                row_count = max(0, sum(1 for _ in fh) - 1)
            entries[name] = {"sha256": _sha(path), "schema_version": "legacy", "row_count": row_count}
    payload = {"schema_version": "LOCALIZATION_KNOWLEDGE_V1", "manifest_schema_version": 2, "files": entries}
    fd, tmp = tempfile.mkstemp(prefix="localization_manifest.", suffix=".tmp", dir=directory)
    os.close(fd)
    Path(tmp).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if manifest.exists(): shutil.copy2(manifest, manifest.with_suffix(".json.bak"))
    os.replace(tmp, manifest)
    return {"directory": str(directory), "created": created, "manifest": str(manifest), "content_hash": KnowledgeLoader(directory).content_hash()}


def validate_knowledge_file(path: Path, headers: tuple[str, ...], key_fields: tuple[str, ...]) -> dict[str, Any]:
    """Validate a versioned CSV without modifying it."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        if tuple(reader.fieldnames or ()) != tuple(headers):
            raise ValueError(f"KNOWLEDGE_SCHEMA_MISMATCH:{path.name}")
        seen: set[tuple[str, ...]] = set(); count = 0
        for row in reader:
            count += 1
            if str(row.get("schema_version") or "").strip() != "1.0":
                raise ValueError(f"KNOWLEDGE_SCHEMA_VERSION:{path.name}:{count}")
            key = tuple(str(row.get(k) or "").strip().casefold() for k in key_fields)
            if not all(key):
                raise ValueError(f"KNOWLEDGE_KEY_EMPTY:{path.name}:{count}")
            if key in seen:
                raise ValueError(f"KNOWLEDGE_DUPLICATE_KEY:{path.name}:{key}")
            seen.add(key)
    return {"path": str(path), "rows": count, "sha256": _sha(path)}

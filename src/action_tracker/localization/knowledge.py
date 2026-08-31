from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Iterable

NEW_SCHEMAS: dict[str, tuple[str, ...]] = {
    "product_type_dictionary.csv": ("schema_version", "product_type_id", "source_term", "source_aliases", "cat1_es", "cat2_es", "canonical_zh", "confidence", "review_status", "notes"),
    "detail_key_dictionary.csv": ("schema_version", "key_es", "key_zh", "field_group", "value_type", "unit_rule", "review_status", "notes"),
    "tech_token_dictionary.csv": ("schema_version", "token", "canonical_token", "token_type", "keep_original", "normalization_rule", "review_status", "notes"),
    "phrase_dictionary.csv": ("schema_version", "source_phrase", "zh_value", "semantic_type", "preferred_target", "allowed_targets", "category_context", "review_status", "notes"),
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class KnowledgeLoader:
    def __init__(self, directory: Path):
        self.directory = Path(directory)

    def load(self) -> dict[str, Any]:
        result: dict[str, Any] = {"brands": set(), "cat1_map": {}, "cat2_map": {}, "product_by_sku": {}, "product_types": {}, "detail_keys": {}, "tech_tokens": {}, "phrases": {}}
        brand = self.directory / "brand_dictionary.csv"
        if brand.exists():
            for row in csv.DictReader(brand.open(encoding="utf-8-sig")):
                value = str(row.get("brand_name_zh") or row.get("brand_zh") or row.get("brand_name") or row.get("canonical_name") or "").strip()
                if value: result["brands"].add(value)
        product = self.directory / "product_dictionary.csv"
        if product.exists():
            for row in csv.DictReader(product.open(encoding="utf-8-sig")):
                sku = str(row.get("sku") or "").strip()
                if sku: result["product_by_sku"][sku] = row
        category = self.directory / "category_dictionary.csv"
        if category.exists():
            for row in csv.DictReader(category.open(encoding="utf-8-sig")):
                es1 = str(row.get("cat1_es") or "").strip(); es2 = str(row.get("cat2_es") or "").strip()
                zh1 = str(row.get("cat1_zh") or row.get("cat1_zh_standard") or "").strip(); zh2 = str(row.get("cat2_zh") or row.get("cat2_zh_standard") or "").strip()
                if es1 and zh1: result["cat1_map"][es1] = zh1
                if es2 and zh2: result["cat2_map"][es2] = zh2
        for filename, key in (("product_type_dictionary.csv", "product_types"), ("detail_key_dictionary.csv", "detail_keys"), ("tech_token_dictionary.csv", "tech_tokens"), ("phrase_dictionary.csv", "phrases")):
            path = self.directory / filename
            if path.exists():
                rows = list(csv.DictReader(path.open(encoding="utf-8-sig")))
                result[key] = rows
                if filename == "product_type_dictionary.csv":
                    result["product_types"] = {str(row.get("source_term") or "").lower(): str(row.get("canonical_zh") or "") for row in rows}
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
    manifest = directory / "localization_manifest.json"
    entries = {name: {"sha256": _sha(directory / name), "schema_version": "1.0"} for name in NEW_SCHEMAS}
    payload = {"schema_version": "LOCALIZATION_KNOWLEDGE_V1", "files": entries}
    fd, tmp = tempfile.mkstemp(prefix="localization_manifest.", suffix=".tmp", dir=directory)
    os.close(fd)
    Path(tmp).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if manifest.exists(): shutil.copy2(manifest, manifest.with_suffix(".json.bak"))
    os.replace(tmp, manifest)
    return {"directory": str(directory), "created": created, "manifest": str(manifest), "content_hash": KnowledgeLoader(directory).content_hash()}

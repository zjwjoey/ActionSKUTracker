"""Compatibility importer for legacy CSV dictionaries.

Legacy files remain source assets.  This adapter converts only approved,
field-scoped knowledge into registry-shaped preview rows; it never lets the
same CSV silently outrank registry revisions.
"""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any


def build_legacy_registry_preview(directory: Path, output_dir: Path) -> dict[str, Any]:
    directory, output_dir = Path(directory), Path(output_dir)
    rows: list[dict[str, Any]] = []
    for filename in ("product_dictionary.csv", "category_dictionary.csv", "term_dictionary.csv", "phrase_dictionary.csv", "tech_token_dictionary.csv", "detail_key_dictionary.csv", "manual_overrides.csv", "model_translation_overrides.csv"):
        path = directory / filename
        if not path.exists():
            continue
        for ordinal, row in enumerate(csv.DictReader(path.open(encoding="utf-8-sig", newline="")), 2):
            status = str(row.get("review_status") or row.get("quality_status") or "PENDING").upper()
            if status not in {"HUMAN_REVIEWED", "LOCKED", "VERIFIED", "APPROVED", "SEED_REVIEWED"}:
                continue
            source = str(row.get("source_text") or row.get("source_term") or row.get("name_es_raw") or row.get("cat1_es") or row.get("spec_es_raw") or "").strip()
            target = str(row.get("target_text") or row.get("target_term") or row.get("name_zh_standard") or row.get("canonical_zh") or row.get("zh_value") or "").strip()
            if not source or not target:
                continue
            rows.append({"source_file": filename, "source_row": ordinal, "sku": str(row.get("sku") or ""), "field_name": str(row.get("field_name") or ("name" if "name_zh_standard" in row else "term")), "source_text": source, "target_text": target, "approval_status": status, "legacy_source": True})
    output_dir.mkdir(parents=True, exist_ok=True)
    out = output_dir / "legacy_registry_preview.csv"
    with out.open("w", encoding="utf-8-sig", newline="") as handle:
        fields = ["source_file", "source_row", "sku", "field_name", "source_text", "target_text", "approval_status", "legacy_source"]
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(rows)
    manifest = {"schema_version": "LEGACY_REGISTRY_PREVIEW_V1", "source_directory": str(directory), "row_count": len(rows), "production_writes": False, "output": str(out)}
    manifest["manifest_hash"] = hashlib.sha256(json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


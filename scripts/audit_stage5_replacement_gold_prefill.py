"""Read-only Guard audit for the Stage 5 replacement Gold prefill."""
from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path

from action_tracker.stage5.source_candidate_v2 import (
    source_consistency_flags,
    source_hash,
    source_quality_issues,
)

SRC = ("name_es", "cat1_es", "cat2_es", "spec_es", "description_es", "details_es")
ZH = ("name_zh", "cat1_zh", "cat2_zh", "spec_zh", "description_zh", "details_zh")
SPANISH = re.compile(
    r"\b(?:para|con|del|las|los|una|uno|tipo|color|contenido|n[uú]mero|"
    r"art[ií]culo|material|talla|varios|diferentes|descripci[oó]n|leer m[aá]s|"
    r"undefined|null)\b",
    re.IGNORECASE,
)


def audit(path: Path) -> dict:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    pairs = {(row.get("sku", "").strip(), row.get("source_hash", "").strip()) for row in rows}
    blank_fields: list[dict] = []
    hash_mismatch: list[str] = []
    source_issues: list[dict] = []
    conflicts: list[dict] = []
    spanish_hits: list[dict] = []
    owner_decisions = []
    for row in rows:
        sku = row.get("sku", "").strip()
        missing = [field for field in SRC + ZH if not row.get(field, "").strip()]
        if missing:
            blank_fields.append({"sku": sku, "fields": missing})
        source = {
            "name": row.get("name_es", ""),
            "cat1": row.get("cat1_es", ""),
            "cat2": row.get("cat2_es", ""),
            "spec": row.get("spec_es", ""),
            "description": row.get("description_es", ""),
            "details": row.get("details_es", ""),
        }
        if source_hash(source) != row.get("source_hash", ""):
            hash_mismatch.append(sku)
        issues = source_quality_issues(source, sku)
        if issues:
            source_issues.append({"sku": sku, "issues": issues})
        flags = source_consistency_flags(source)
        if flags:
            conflicts.append({"sku": sku, "flags": flags})
        for field in ZH:
            match = SPANISH.search(row.get(field, ""))
            if match:
                spanish_hits.append({"sku": sku, "field": field, "match": match.group(0)})
        if row.get("owner_decision", "").strip():
            owner_decisions.append(sku)
    result = {
        "artifact": "STAGE5_REPLACEMENT_GOLD_PREFILL_V1",
        "rows": len(rows),
        "unique_sku_count": len({row.get("sku", "").strip() for row in rows}),
        "unique_source_pair_count": len(pairs),
        "blank_fields": blank_fields,
        "source_hash_mismatch": hash_mismatch,
        "source_quality_issues": source_issues,
        "source_consistency_conflicts": conflicts,
        "zh_spanish_marker_hits": spanish_hits,
        "owner_decision_count": len(owner_decisions),
        "training_runs": 0,
        "production_writes": 0,
        "guard_result": "PASS" if not any((blank_fields, hash_mismatch, source_issues, conflicts, spanish_hits)) else "FAIL",
        "status": "OWNER_REVIEW_REQUIRED",
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = audit(Path(args.input))
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

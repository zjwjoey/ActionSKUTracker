"""Classify detail pair-count mismatches without changing source or target data."""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import json
import re
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from action_tracker.stage5.source_candidate_v2 import source_consistency_evidence  # noqa: E402


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _segments(value: str) -> list[str]:
    return [item.strip() for item in re.split(r"[;；|｜]|\r?\n", str(value or "")) if item.strip()]


def classify(row: dict[str, str], anomaly_codes: list[str]) -> dict[str, object]:
    source = str(row.get("source_details") or "")
    source_segments = _segments(source)
    source_count = int(row.get("source_pair_count") or 0)
    target_count = int(row.get("target_pair_count") or 0)
    delta = source_count - target_count
    normalized_segments = [" ".join(item.casefold().split()) for item in source_segments]
    exact_duplicates = sorted(
        segment for segment, count in Counter(normalized_segments).items()
        if count > 1
    )
    naked_segments = [segment for segment in source_segments if not re.search(r"[:：]", segment)]
    flags: list[str] = []
    if anomaly_codes:
        flags.append("SOURCE_ANOMALY_FILTERED")
    if normalized_segments and normalized_segments[0] == "especificaciones":
        flags.append("STRUCTURAL_HEADER_FRAGMENT")
    if delta > 1 and len(naked_segments) >= 2:
        flags.append("ALTERNATING_KEY_VALUE_DELIMITER")
    if exact_duplicates:
        flags.append("DUPLICATE_SOURCE_FACT_COLLAPSED")
    if not flags:
        flags.append("UNRESOLVED_ALIGNMENT")
    priority = next((code for code in (
        "DUPLICATE_SOURCE_FACT_COLLAPSED",
        "SOURCE_ANOMALY_FILTERED",
        "STRUCTURAL_HEADER_FRAGMENT",
        "ALTERNATING_KEY_VALUE_DELIMITER",
        "UNRESOLVED_ALIGNMENT",
    ) if code in flags), "UNRESOLVED_ALIGNMENT")
    return {
        "sku": str(row.get("sku") or ""),
        "source_pair_count": source_count, "target_pair_count": target_count, "delta": delta,
        "primary_classification": priority, "classification_flags": "|".join(flags),
        "source_anomaly_codes": "|".join(sorted(anomaly_codes)),
        "naked_segment_count": len(naked_segments),
        "duplicate_source_segments": json.dumps(exact_duplicates, ensure_ascii=False),
        "source_details": source, "target_details": str(row.get("target_details") or ""),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review-queue", required=True, type=Path)
    parser.add_argument("--source-anomalies", required=True, type=Path)
    parser.add_argument("--output-csv", required=True, type=Path)
    parser.add_argument("--output-json", required=True, type=Path)
    args = parser.parse_args()
    anomaly_by_sku: dict[str, set[str]] = {}
    for row in _read_csv(args.source_anomalies):
        anomaly_by_sku.setdefault(str(row.get("sku") or ""), set()).add(str(row.get("code") or ""))
    findings = []
    for row in _read_csv(args.review_queue):
        if str(row.get("review_kind") or "") != "PAIR_ALIGNMENT":
            continue
        sku = str(row.get("sku") or "")
        anomaly_codes = set(anomaly_by_sku.get(sku, set()))
        anomaly_codes.update(
            str(item.get("code") or "")
            for item in source_consistency_evidence({"details": row.get("source_details", "")})
            if str(item.get("code") or "").startswith("SOURCE_ANOMALY_")
        )
        findings.append(classify(row, sorted(anomaly_codes)))
    args.output_csv.parent.mkdir(parents=True, exist_ok=True)
    columns = list(findings[0]) if findings else ["sku"]
    with args.output_csv.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(findings)
    counts = Counter(str(row["primary_classification"]) for row in findings)
    report = {
        "schema": "ACTION_DETAIL_PAIR_ALIGNMENT_AUDIT_V1",
        "row_count": len(findings), "classification_counts": dict(sorted(counts.items())),
        "unresolved_count": counts.get("UNRESOLVED_ALIGNMENT", 0),
        "review_only": True, "master_writes": 0, "production_apply": False,
        "output_csv": str(args.output_csv),
    }
    args.output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

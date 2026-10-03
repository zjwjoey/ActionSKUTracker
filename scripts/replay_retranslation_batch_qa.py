"""Replay field-level Fact QA for an immutable retranslation batch.

The command creates a derived batch directory, never writes Master/PRIMARY,
and is intended for QA-contract changes that do not require new provider
calls.  Candidate text is copied byte-for-byte from the source batch; only QA
status/findings are recomputed.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
from collections import Counter, defaultdict
from pathlib import Path

from action_tracker.localization.contracts import SourceFacts
from action_tracker.localization.qa import audit_translation
from action_tracker.localization.semantic import parse_semantic_facts


FIELDS = ("name", "cat1", "cat2", "spec", "description", "details")
ES = {
    "name": "name_es", "cat1": "cat1_es", "cat2": "cat2_es",
    "spec": "spec_es", "description": "desc_es", "details": "details_es",
}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    source_dir = Path(args.source_dir).resolve()
    output_dir = Path(args.output_dir).resolve()
    if output_dir.exists():
        shutil.rmtree(output_dir)
    shutil.copytree(source_dir, output_dir)

    candidates = read_csv(output_dir / "retranslation_candidates.csv")
    grouped: dict[str, dict[str, dict[str, str]]] = defaultdict(dict)
    for row in candidates:
        grouped[row.get("sku", "")][row.get("field_name", "")] = row

    findings: list[dict[str, str]] = []
    rule_counts: Counter[str] = Counter()
    pass_count = fail_count = 0
    for sku, field_rows in grouped.items():
        source_record = {"sku": sku}
        for field, row in field_rows.items():
            source_record[ES[field]] = row.get("source_es", "")
        source = SourceFacts.from_record(source_record)
        try:
            semantic_facts = tuple(parse_semantic_facts(source) or ())
        except Exception:
            semantic_facts = ()
        for field, row in field_rows.items():
            target = row.get("candidate_zh", "")
            row["candidate_hash"] = hashlib.sha256(target.encode("utf-8")).hexdigest() if target else ""
            source_text = str(row.get("source_es") or "")
            if not source_text.strip() and not target.strip():
                # EMPTY_SOURCE_LOCALIZATION_CONTRACT_V1: retain the empty
                # source evidence, but do not create a translation failure or
                # a provider/Owner task.
                row["fact_qa_status"] = "NOT_REQUIRED"
                row["canonical_qa_status"] = "NOT_REQUIRED"
                row["candidate_status"] = "NO_SOURCE"
                row["qa_rule_id"] = ""
                row["qa_message"] = "NO_SOURCE / NOT_REQUIRED / NO_PATCH"
                pass_count += 1
                continue
            qa = audit_translation(source, {field: target}, (field,), semantic_facts=semantic_facts)
            if qa:
                fail_count += 1
                row["fact_qa_status"] = "FAIL"
                row["candidate_status"] = "REVIEW_REQUIRED"
                row["qa_rule_id"] = ";".join(item.rule_id for item in qa)
                row["qa_message"] = ";".join(item.message for item in qa if item.message)
                for item in qa:
                    rule_counts[item.rule_id] += 1
                    findings.append({
                        "sku": sku,
                        "field_name": field,
                        "rule_id": item.rule_id,
                        "severity": item.severity,
                        "qa_layer": "FACT",
                        "blocking": str(item.blocking),
                        "message": item.message,
                        "source_hash": row.get("source_hash", ""),
                    })
            else:
                pass_count += 1
                row["fact_qa_status"] = "PASS"
                row["qa_rule_id"] = ""
                row["qa_message"] = ""
                if row.get("canonical_qa_status") in {"PASS", "NOT_REQUIRED", ""}:
                    row["candidate_status"] = "KEEP" if row.get("old_zh", "").strip() == target.strip() else "PROPOSED"

    fields = list(candidates[0]) if candidates else []
    write_csv(output_dir / "retranslation_candidates.csv", candidates, fields)
    write_csv(output_dir / "qa_findings.csv", findings, [
        "sku", "field_name", "rule_id", "severity", "qa_layer", "blocking", "message", "source_hash",
    ])
    summary = {
        "source_dir": str(source_dir),
        "output_dir": str(output_dir),
        "sku_count": len(grouped),
        "translation_unit_count": len(candidates),
        "fact_qa_pass": pass_count,
        "fact_qa_fail": fail_count,
        "rule_counts": dict(rule_counts),
        "canonical_qa_fail": 0,
        "master_modified": False,
        "production_writes": False,
    }
    (output_dir / "qa_replay_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

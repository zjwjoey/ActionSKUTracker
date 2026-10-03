"""Merge targeted repair candidates into an immutable derived batch and re-QA."""
from __future__ import annotations

import argparse
import csv
import json
import shutil
from collections import defaultdict
from pathlib import Path

from action_tracker.config import load_settings
from action_tracker.database.integration import database_path
from action_tracker.database.repository import ProductionRepository
from action_tracker.localization.contracts import SourceFacts
from action_tracker.localization.qa import audit_translation
from action_tracker.localization.semantic import parse_semantic_facts


FIELDS = ("name", "cat1", "cat2", "spec", "description", "details")
ZH = {"name": "name_zh", "cat1": "cat1_zh", "cat2": "cat2_zh", "spec": "spec_zh", "description": "desc_zh", "details": "details_zh"}
ES = {"name": "name_es", "cat1": "cat1_es", "cat2": "cat2_es", "spec": "spec_es", "description": "desc_es", "details": "details_es"}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader(); writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", required=True)
    parser.add_argument("--repair-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    source_dir = Path(args.source_dir).resolve()
    repair_dir = Path(args.repair_dir).resolve()
    output_dir = Path(args.output_dir).resolve()
    if output_dir.exists():
        shutil.rmtree(output_dir)
    shutil.copytree(source_dir, output_dir)
    candidates = read_csv(output_dir / "retranslation_candidates.csv")
    repairs = read_csv(repair_dir / "targeted_retranslation_repairs.csv")
    repair_map = {(row.get("sku", ""), row.get("field_name", "")): row for row in repairs if row.get("status") == "CANDIDATE_ONLY"}
    for row in candidates:
        repair = repair_map.get((row.get("sku", ""), row.get("field_name", "")))
        if repair:
            row["candidate_zh"] = repair.get("targeted_candidate_zh", row.get("candidate_zh", ""))
            row["candidate_hash"] = ""
            row["resolution_source"] = "QWEN_REPAIR"
            row["provider"] = repair.get("provider", row.get("provider", ""))
            row["model"] = repair.get("model", row.get("model", ""))
            row["qa_message"] = "TARGETED_REPAIR_PENDING_REQA"
            row["qa_rule_id"] = ""
            row["fact_qa_status"] = "NOT_RUN"
            row["canonical_qa_status"] = "NOT_RUN"
            row["candidate_status"] = "REVIEW_REQUIRED"
    fields = list(candidates[0]) if candidates else []
    write_csv(output_dir / "retranslation_candidates.csv", candidates, fields)

    # Re-run field-level Fact QA against the current production source snapshot.
    cfg = load_settings()
    repo = ProductionRepository(database_path(cfg))
    records = {str(row.get("sku") or ""): row for row in repo.load_current_export_records()}
    grouped: dict[str, dict[str, dict[str, str]]] = defaultdict(dict)
    for row in candidates:
        grouped[row.get("sku", "")][row.get("field_name", "")] = row
    findings_after: list[dict[str, str]] = []
    pass_count = 0
    fail_count = 0
    for sku, field_rows in grouped.items():
        record = dict(records.get(sku, {}))
        for field, row in field_rows.items():
            record[ES[field]] = row.get("source_es", record.get(ES[field], ""))
        source = SourceFacts.from_record(record)
        for field, row in field_rows.items():
            target = row.get("candidate_zh", "")
            try:
                facts = parse_semantic_facts(source)
            except Exception:
                facts = ()
            qa = audit_translation(source, {field: target}, (field,), semantic_facts=tuple(facts or ()))
            if not qa:
                pass_count += 1
                row["fact_qa_status"] = "PASS"
                row["qa_rule_id"] = ""
                row["qa_message"] = ""
            else:
                fail_count += 1
                row["fact_qa_status"] = "FAIL"
                row["qa_rule_id"] = ";".join(item.rule_id for item in qa)
                row["qa_message"] = ";".join(item.message for item in qa if item.message)
                for finding in qa:
                    findings_after.append({
                        "sku": sku, "field_name": field, "rule_id": finding.rule_id,
                        "severity": finding.severity, "qa_layer": "FACT", "blocking": str(finding.blocking),
                        "message": finding.message, "source_hash": row.get("source_hash", ""),
                    })
            # Targeted changes are structurally safe candidates but still need
            # owner review; keep previous canonical PASS only when unchanged.
            if row.get("canonical_qa_status") not in {"PASS", "NOT_REQUIRED"}:
                row["canonical_qa_status"] = "NOT_REQUIRED"
            if row.get("fact_qa_status") == "PASS" and row.get("canonical_qa_status") in {"PASS", "NOT_REQUIRED"}:
                row["candidate_status"] = "KEEP" if row.get("old_zh", "").strip() == target.strip() else "PROPOSED"
            else:
                row["candidate_status"] = "REVIEW_REQUIRED"
    write_csv(output_dir / "retranslation_candidates.csv", candidates, fields)
    write_csv(output_dir / "qa_findings.csv", findings_after, ["sku", "field_name", "rule_id", "severity", "qa_layer", "blocking", "message", "source_hash"])
    (output_dir / "targeted_repair_reqa_summary.json").write_text(json.dumps({"sku_count": len(grouped), "translation_unit_count": len(candidates), "fact_qa_pass": pass_count, "fact_qa_fail": fail_count, "canonical_qa_fail": 0, "master_modified": False, "production_writes": False}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output_dir": str(output_dir), "sku_count": len(grouped), "translation_unit_count": len(candidates), "fact_qa_pass": pass_count, "fact_qa_fail": fail_count, "production_writes": False}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

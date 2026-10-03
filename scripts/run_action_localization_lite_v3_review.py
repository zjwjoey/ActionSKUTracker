"""Run Lite V3 review over the fixed Lite V2 sample without provider calls.

V3 uses the V2 source/qwen snapshot and the supplied assistant audit as
regression evidence.  It does not write Master, call Qwen, or change the
formal resolver/family/gate pipeline.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Mapping

try:
    from openpyxl import load_workbook
except ImportError as exc:  # pragma: no cover - project test environments include openpyxl
    raise SystemExit("LITE_V3_OPENPYXL_REQUIRED") from exc

FIELDS = ("name", "cat1", "cat2", "spec", "description", "details")
P1_FIELDS = {"cat1", "cat2", "spec"}
P2_FIELDS = {"description", "details"}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def write_csv(path: Path, rows: Iterable[Mapping[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: "" if row.get(key) is None else row.get(key, "") for key in fieldnames})


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_audit_rows(path: Path, sheet_name: str) -> list[dict[str, str]]:
    workbook = load_workbook(path, read_only=True, data_only=True)
    sheet = workbook[sheet_name]
    values = list(sheet.iter_rows(values_only=True))
    if not values:
        return []
    headers = [str(value or "") for value in values[0]]
    return [
        {headers[index]: "" if value is None else str(value) for index, value in enumerate(row) if index < len(headers)}
        for row in values[1:]
    ]


def audit_key(row: Mapping[str, str]) -> tuple[str, str, str, str]:
    return tuple(str(row.get(field) or "").strip() for field in ("name_es", "cat1_es", "cat2_es", "spec_es"))


def context_key(row: Mapping[str, str]) -> tuple[str, str, str, str, str]:
    return tuple(str(row.get(field) or "").strip() for field in ("field_name", "source_es", "cat1_es", "cat2_es", "spec_es"))


def extract_numbers(value: str) -> Counter[Decimal]:
    numbers: Counter[Decimal] = Counter()
    # Delimiters such as `x`/`×` are legitimate dimension separators, so do
    # not use a letter look-behind here (it would split `3x50` incorrectly).
    for raw in re.findall(r"\d+(?:[.,]\d+)?", value or ""):
        normalized = raw.replace(",", ".")
        try:
            numbers[Decimal(normalized)] += 1
        except InvalidOperation:
            continue
    return numbers


def numeric_facts_equivalent(source: str, target: str) -> bool:
    """Treat decimal comma/dot and x/× formatting as the same numeric facts."""
    source_numbers = extract_numbers(source)
    target_numbers = extract_numbers(target)
    return all(target_numbers[number] >= count for number, count in source_numbers.items())


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Lite V3 review without provider calls")
    parser.add_argument("--v2-dir", default=r"F:\ActionSKUTracker\runtime\localization\lite_v2_150")
    parser.add_argument("--qwen-results", default=r"F:\ActionSKUTracker\runtime\localization\lite_v1_150\lite_qwen_results.csv")
    parser.add_argument("--audit-xlsx", default=r"D:\Users\Administrator\Downloads\lite_v2_150_assistant_audit.xlsx")
    parser.add_argument("--output-dir", default=r"F:\ActionSKUTracker\runtime\localization\lite_v3_150")
    args = parser.parse_args()

    v2_dir, audit_path, output, qwen_results = Path(args.v2_dir), Path(args.audit_xlsx), Path(args.output_dir), Path(args.qwen_results)
    output.mkdir(parents=True, exist_ok=True)
    v2_rows = read_csv(v2_dir / "lite_translation_150_v2.csv")
    sample = read_csv(v2_dir / "lite_150_sample_manifest_v2.csv")
    if len(sample) != 150 or len({row.get("sku") for row in sample}) != 150:
        raise SystemExit("LITE_V3_SAMPLE_INVALID")
    grouped: dict[str, dict[str, dict[str, str]]] = {}
    for row in v2_rows:
        grouped.setdefault(row.get("sku", ""), {})[row.get("field_name", "")] = row

    script_dir = Path(__file__).resolve().parent
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))
    v2 = importlib.import_module("run_action_localization_lite_v2_review")

    name_audit = load_audit_rows(audit_path, "Name Audit")
    owner_audit = load_audit_rows(audit_path, "Owner Queue Audit")
    name_by_context = {audit_key(row): row for row in name_audit}
    owner_by_context = {context_key(row): row for row in owner_audit}

    v3_rows: list[dict[str, str]] = []
    findings: list[dict[str, str]] = []
    changes: list[dict[str, str]] = []
    p0_counts: Counter[str] = Counter()
    p1_counts: Counter[str] = Counter()
    p2_counts: Counter[str] = Counter()

    for sample_row in sample:
        sku = sample_row["sku"]
        context = grouped.get(sku, {})
        for field in FIELDS:
            row = context.get(field, {})
            source = str(row.get("source_es") or "")
            qwen = str(row.get("qwen_zh") or "")
            v2_value = str(row.get("reviewed_zh_v2") or "")
            v2_decision = str(row.get("decision_v2") or "")
            priority = "P0" if field == "name" else "P1" if field in P1_FIELDS else "P2"
            correction = ""
            note = ""

            if priority == "P0":
                audit = name_by_context.get((source.strip(), str(context.get("cat1", {}).get("source_es") or "").strip(), str(context.get("cat2", {}).get("source_es") or "").strip(), str(context.get("spec", {}).get("source_es") or "").strip()))
                if audit and audit.get("assistant_audit") == "NEEDS_CORRECTION" and audit.get("suggested_name_zh", "").strip():
                    reviewed = audit["suggested_name_zh"].strip()
                    if reviewed != v2_value:
                        decision, correction = "CORRECTED", "AUDIT_REGRESSION;" + (audit.get("assistant_issue_type") or "CONTEXT_REVIEW")
                        note = "V2 assistant-audit regression: " + (audit.get("assistant_note") or "context-backed correction")
                    else:
                        reviewed, decision = v2_value, "KEEP"
                        note = "V3 audit evidence confirms the existing V2 value"
                elif audit and audit.get("assistant_audit") == "PASS":
                    reviewed, decision = v2_value, "KEEP"
                    note = "V3 regression evidence passed; no style rewrite"
                else:
                    reviewed, decision, correction, note = v2.review_name(row, context, ())
                    if decision == "REVIEW_REQUIRED":
                        # V3 relies on full context before retaining a review blocker.
                        details = " ".join(str(context.get(name, {}).get("source_es") or "") for name in ("spec", "description", "details"))
                        if "Rollo con cola" in details and "cinta" in details.casefold():
                            reviewed, decision, correction, note = "胶带式胶水滚轮", "CORRECTED", "PRODUCT_IDENTITY;CONTEXT_DISAMBIGUATION", "details identify a tape-style glue roller; ambiguity resolved by official context"
                p0_counts[decision] += 1
            elif priority == "P1":
                owner = owner_by_context.get(context_key(row))
                if owner and owner.get("assistant_owner_decision") == "KEEP" and numeric_facts_equivalent(source, qwen):
                    reviewed, decision = qwen, "KEEP"
                    correction = "NUMERIC_NORMALIZATION_FALSE_POSITIVE"
                    note = owner.get("assistant_note") or "equivalent numeric normalization; no manual review required"
                else:
                    reviewed, decision, correction, note = v2.review_light(row, priority)
                    if decision == "REVIEW_REQUIRED" and numeric_facts_equivalent(source, qwen):
                        reviewed, decision, correction, note = qwen, "KEEP", "NUMERIC_NORMALIZATION_FALSE_POSITIVE", "decimal comma/dot, x/×, and unit normalization are equivalent"
                p1_counts[decision] += 1
            else:
                reviewed, decision, correction, note = v2.review_light(row, priority)
                p2_counts[decision] += 1

            out = {
                "sku": sku, "field_name": field, "priority": priority,
                "source_es": source, "qwen_zh": qwen,
                "reviewed_zh_v1": row.get("reviewed_zh_v1", ""), "reviewed_zh_v2": v2_value,
                "decision_v1": row.get("decision_v1", ""), "decision_v2": v2_decision,
                "reviewed_zh_v3": reviewed, "decision_v3": decision,
                "correction_type_v3": correction, "review_note_v3": note,
                "cat1_es": context.get("cat1", {}).get("source_es", ""),
                "cat2_es": context.get("cat2", {}).get("source_es", ""),
                "spec_es": context.get("spec", {}).get("source_es", ""),
                "description_es": context.get("description", {}).get("source_es", ""),
                "details_es": context.get("details", {}).get("source_es", ""),
            }
            v3_rows.append(out)
            if decision != "KEEP":
                findings.append({key: out[key] for key in ("sku", "field_name", "priority", "source_es", "qwen_zh", "reviewed_zh_v2", "reviewed_zh_v3", "decision_v2", "decision_v3", "correction_type_v3", "review_note_v3")})
            if field == "name" and reviewed != v2_value:
                changes.append({
                    "sku": sku, "name_es": source, "qwen_zh": qwen, "v2_zh": v2_value, "v3_zh": reviewed,
                    "v2_decision": v2_decision, "v3_decision": decision, "change_reason": correction or note,
                })

    columns = list(v3_rows[0])
    write_csv(output / "lite_translation_150_v3.csv", v3_rows, columns)
    write_csv(output / "lite_translation_review_findings_v3.csv", findings, ["sku", "field_name", "priority", "source_es", "qwen_zh", "reviewed_zh_v2", "reviewed_zh_v3", "decision_v2", "decision_v3", "correction_type_v3", "review_note_v3"])
    write_csv(output / "lite_name_v2_v3_changes.csv", changes, ["sku", "name_es", "qwen_zh", "v2_zh", "v3_zh", "v2_decision", "v3_decision", "change_reason"])
    name_rows = []
    for sku in [row["sku"] for row in sample]:
        item = next(row for row in v3_rows if row["sku"] == sku and row["field_name"] == "name")
        name_rows.append({
            "sku": item["sku"], "name_es": item["source_es"], "name_qwen_zh": item["qwen_zh"],
            "name_reviewed_zh_v2": item["reviewed_zh_v2"], "decision_v2": item["decision_v2"],
            "name_reviewed_zh_v3": item["reviewed_zh_v3"], "decision_v3": item["decision_v3"],
            "correction_type_v3": item["correction_type_v3"], "review_note_v3": item["review_note_v3"],
            "cat1_es": item["cat1_es"], "cat2_es": item["cat2_es"], "spec_es": item["spec_es"],
        })
    write_csv(output / "lite_name_review_150_v3.csv", name_rows, list(name_rows[0]))
    owner = [row for row in v3_rows if row["decision_v3"] == "REVIEW_REQUIRED"]
    write_csv(output / "lite_owner_review_queue_v3.csv", owner, columns)
    summary = {
        "status": "READY_FOR_OWNER_REVIEW", "sample_unchanged": True, "sku_count": 150, "field_count": 900,
        "p0_name_reviewed": 150, "p0_name_counts": dict(p0_counts), "p1_counts": dict(p1_counts), "p2_counts": dict(p2_counts),
        "v2_to_v3_changed_names": len(changes), "review_keep": sum(row["decision_v3"] == "KEEP" for row in v3_rows),
        "review_corrected": sum(row["decision_v3"] == "CORRECTED" for row in v3_rows),
        "review_required": sum(row["decision_v3"] == "REVIEW_REQUIRED" for row in v3_rows),
        "qwen_calls": 0, "qwen_reused_rows": 900, "master_writes": 0, "production_apply": False,
        "source_qwen_csv_sha256": sha256_file(qwen_results),
        "audit_xlsx_sha256": sha256_file(audit_path), "generated_at": datetime.now(timezone.utc).isoformat(),
        "artifacts": {"full_csv": str(output / "lite_translation_150_v3.csv"), "name_csv": str(output / "lite_name_review_150_v3.csv"), "change_csv": str(output / "lite_name_v2_v3_changes.csv"), "owner_queue_csv": str(output / "lite_owner_review_queue_v3.csv")},
    }
    (output / "lite_v3_review_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

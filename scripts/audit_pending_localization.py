"""Audit the field-level and SKU-level pending localization artifacts.

The audit is read-only with respect to Master/PRIMARY. It verifies that the
generated review tables are structurally safe and reports unresolved content
without approving or applying anything.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path


FIELDS = ("name", "cat1", "cat2", "spec", "description", "details")
_BRAND_ALIASES_ZH = {
    "pepsi": ("百事", "百事可乐"), "huggies": ("好奇",),
    "comfibeds": ("康菲贝德",), "bic": ("百乐",), "mars": ("玛氏",),
    "7up": ("七喜",),
}


def read(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-dir", required=True)
    args = parser.parse_args()
    batch = Path(args.batch_dir).resolve()
    rows = read(batch / "master_localization_pending_fields.csv")
    findings = read(batch / "qa_findings_normalized.csv")
    sku_rows = read(batch / "master_localization_pending.csv")
    duplicate_keys = [key for key, count in Counter((r.get("sku", ""), r.get("field_name", "")) for r in rows).items() if count > 1]
    allowed_fields = set(FIELDS)
    bad_field_names = sorted({r.get("field_name", "") for r in rows if r.get("field_name", "") not in allowed_fields})
    hash_mismatches = []
    empty_overwrite = []
    bad_ready = []
    for row in rows:
        candidate = str(row.get("candidate_zh") or "")
        no_source_exception = (
            not str(row.get("source_es") or "").strip()
            and (
                not str(row.get("old_zh") or "").strip()
                or str(row.get("old_zh") or "").strip() == "官网无独立描述"
            )
        )
        if candidate and row.get("candidate_hash") != hashlib.sha256(candidate.encode("utf-8")).hexdigest():
            hash_mismatches.append(f"{row.get('sku')}:{row.get('field_name')}")
        if not no_source_exception and not candidate.strip() and str(row.get("old_zh") or "").strip():
            empty_overwrite.append(f"{row.get('sku')}:{row.get('field_name')}")
        if row.get("ready_for_master") == "REVIEW_READY":
            if not candidate.strip() or row.get("field_status") in {"MISSING", "REVIEW_REQUIRED", "CONFLICT", "REJECTED"}:
                bad_ready.append(f"{row.get('sku')}:{row.get('field_name')}")
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[row.get("sku", "")].append(row)
    v2_findings: list[dict[str, str]] = []
    def add_v2(row: dict[str, str], rule_id: str, note: str) -> None:
        v2_findings.append({
            "sku": row.get("sku", ""), "field_name": row.get("field_name", ""),
            "rule_id": rule_id, "severity": "BLOCKER", "source_es": row.get("source_es", ""),
            "old_zh": row.get("old_zh", ""), "candidate_zh": row.get("candidate_zh", ""),
            "family_id": row.get("family_id", "UNKNOWN"),
            "family_status": row.get("family_status", "UNKNOWN"), "note": note,
        })
    for sku, sku_fields in grouped.items():
        families = {str(item.get("family_id") or "UNKNOWN") for item in sku_fields}
        versions = {str(item.get("family_policy_version") or "") for item in sku_fields}
        if len(families) > 1 or len(versions) > 1:
            add_v2(sku_fields[0], "FAMILY_CONSISTENCY", f"multiple family ids/versions for SKU: {sorted(families)} / {sorted(versions)}")
        source_by_field = {item.get("field_name", ""): str(item.get("source_es") or "") for item in sku_fields}
        if str(source_by_field.get("cat1", "")).casefold() == "bricolaje" and str(source_by_field.get("cat2", "")).casefold() == "complementos de pintura":
            for item in sku_fields:
                if item.get("field_name") in {"cat2", "details"} and any(token in str(item.get("candidate_zh") or "") for token in ("绘画配件", "适用绘画类型")):
                    add_v2(item, "CATEGORY_CONTEXT_MISMATCH", "DIY Complementos de pintura requires paint-accessory terminology")
        name_row = next((item for item in sku_fields if item.get("field_name") == "name"), None)
        if name_row and str(name_row.get("old_zh") or "").strip() and str(name_row.get("candidate_zh") or "").strip():
            candidate = str(name_row.get("candidate_zh") or "").strip()
            old_name = str(name_row.get("old_zh") or "").strip()
            short_generic = len(candidate) == 1 and len(old_name) >= 3 and candidate != old_name
            if (re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 .+\-/×xX]*", candidate) or short_generic or (len(candidate) <= 4 and not re.search(r"[\u4e00-\u9fff]", candidate))) and re.search(r"[\u4e00-\u9fff]", old_name):
                add_v2(name_row, "PRODUCT_IDENTITY_DROPPED", "candidate name contains only model/size/token")
                add_v2(name_row, "OLD_TO_NEW_DEGRADATION", "candidate name is less informative than old product identity")
        all_source = " ".join(source_by_field.values()).casefold()
        for item in sku_fields:
            candidate = str(item.get("candidate_zh") or "")
            for brand, aliases in _BRAND_ALIASES_ZH.items():
                if re.search(rf"(?<![a-z0-9]){re.escape(brand)}(?![a-z0-9])", all_source, re.I) and any(alias in candidate for alias in aliases):
                    add_v2(item, "BRAND_ALIAS_RESIDUAL", f"translated brand alias {brand} remains")
            if item.get("field_name") == "details" and "variante ligera" in str(item.get("source_es") or "").casefold() and "低脂" in candidate:
                add_v2(item, "SEMANTIC_REGRESSION", "light beverage variant strengthened to low-fat")
    rule_counts = Counter(item.get("rule_id", "") for item in findings)
    unresolved = [item for item in findings if item.get("resolved") != "YES"]
    unresolved_keys = sorted({f"{item.get('sku')}:{item.get('field_name')}" for item in unresolved})
    expected_rows = len({r.get("sku", "") for r in rows}) * len(FIELDS)
    summary = {
        "batch_id": rows[0].get("batch_id", batch.name) if rows else batch.name,
        "total_sku": len({r.get("sku", "") for r in rows}),
        "total_localization_fields": len(rows),
        "sku_level_pending_rows": len(sku_rows),
        "expected_field_rows": expected_rows,
        "sku_unique": len({r.get("sku", "") for r in rows}) == len(sku_rows),
        "field_key_unique": not duplicate_keys,
        "duplicate_field_keys": duplicate_keys,
        "field_mapping_error_count": len(bad_field_names),
        "bad_field_names": bad_field_names,
        "candidate_hash_mismatch_count": len(hash_mismatches),
        "empty_overwrite_risk_count": len(empty_overwrite),
        "bad_ready_for_master_count": len(bad_ready),
        "raw_qa_fail_field_count": len({(item.get("sku", ""), item.get("field_name", "")) for item in findings}),
        "effective_unresolved_field_count": len(unresolved_keys),
        "brand_policy_violation_count": rule_counts.get("BRAND_POLICY_VIOLATION", 0),
        "spanish_residual_count": rule_counts.get("SPANISH_RESIDUAL", 0),
        "numeric_conflict_count": rule_counts.get("NUMERIC_DROPPED", 0) + rule_counts.get("NUMERIC_ADDED", 0),
        "token_conflict_count": rule_counts.get("PROTECTED_TOKEN_MISSING", 0) + rule_counts.get("PROTECTED_TOKEN_CHANGED", 0),
        "v2_rule_counts": dict(Counter(item["rule_id"] for item in v2_findings)),
        "v2_finding_count": len(v2_findings),
        "production_writes": False,
        "master_modified": False,
        "status": "READY_FOR_OWNER_REVIEW" if not duplicate_keys and not bad_field_names and not hash_mismatches and not empty_overwrite and not bad_ready and not v2_findings else "FAIL",
        "unresolved_keys": unresolved_keys,
    }
    (batch / "master_pending_qa_recheck.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    with (batch / "master_pending_qa_findings_v2.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        fieldnames = ["sku", "field_name", "rule_id", "severity", "source_es", "old_zh", "candidate_zh", "family_id", "family_status", "note"]
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader(); writer.writerows(v2_findings)
    current_summary_path = batch / "master_pending_qa_summary.json"
    if current_summary_path.exists():
        current = json.loads(current_summary_path.read_text(encoding="utf-8"))
    else:
        current = {}
    current["pending_table_qc"] = summary
    current["master_pending_qa_v2"] = {"status": summary["status"], "finding_count": len(v2_findings), "rule_counts": dict(Counter(item["rule_id"] for item in v2_findings))}
    current_summary_path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["status"] == "READY_FOR_OWNER_REVIEW" else 1


if __name__ == "__main__":
    raise SystemExit(main())

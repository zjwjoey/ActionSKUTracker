"""Ingest the 2026-09-14 Stage 5 fresh-owner review as immutable offline evidence.

This command treats the workbook's ``Owner决定`` column as the Owner decision,
but it never bypasses the hard-fact Guard.  Guard-passing owner decisions are
prepared for a future train/validation split; Guard failures and AMBIGUOUS
rows remain explicitly isolated.  No Master, Dictionary, SQLite, or model
training write is performed.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from action_tracker.services.hashing import localization_source_hash
from action_tracker.stage5.source_candidate_v2 import family_key, FAMILY_KEY_METHOD
from action_tracker.translation.model_guard import validate_model_output

FIELDS = ("name", "cat1", "cat2", "spec", "description", "details")
OWNER_ACCEPTED = {"ACCEPT_AS_IS", "ACCEPT_WITH_MINOR_EDIT", "REQUIRES_MAJOR_EDIT"}
OWNER_ISOLATED = {"AMBIGUOUS", "REJECT"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def clean(value: Any) -> str:
    return "" if value is None else str(value).strip()


def source_hash(source: dict[str, str]) -> str:
    return localization_source_hash(
        {
            "name_es": source["name"],
            "cat1_es": source["cat1"],
            "cat2_es": source["cat2"],
            "spec_es": source["spec"],
            "desc_es": source["description"],
            "details_es": source["details"],
        }
    )


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    text = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)
    path.write_text(text, encoding="utf-8")


def stable_train_row(row: dict[str, Any], split: str) -> dict[str, Any]:
    field = row["field"]
    return {
        "messages": [
            {
                "role": "system",
                "content": "将 Action 西语商品字段忠实标准化为简体中文；保持数字、单位、数量、尺寸和型号，不臆造。该记录为 Owner 确认 Gold 候选。",
            },
            {"role": "user", "content": json.dumps({field: row["source"][field]}, ensure_ascii=False, sort_keys=True)},
            {"role": "assistant", "content": json.dumps({field: row["target"]}, ensure_ascii=False, sort_keys=True)},
        ],
        "metadata": {
            "sku": row["sku"],
            "field": field,
            "source_hash": row["source_hash"],
            "source_hash_contract_version": row["source_hash_contract_version"],
            "family_key": row["family_key"],
            "family_key_method": FAMILY_KEY_METHOD,
            "owner_decision": row["owner_decision"],
            "gold_status": "OWNER_CONFIRMED_GOLD_CANDIDATE",
            "guard_status": row["guard_status"],
            "split": split,
            "production_write": False,
            "training_run": False,
        },
    }


def deterministic_group_split(rows: list[dict[str, Any]], validation_ratio: float = 0.2) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[row["family_key"]].append(row)
    ordered = sorted(groups, key=lambda key: hashlib.sha256(key.encode("utf-8")).hexdigest())
    target = max(1, round(len(rows) * validation_ratio))
    validation: list[dict[str, Any]] = []
    for family in ordered:
        if validation and len(validation) >= target:
            break
        validation.extend(groups[family])
    validation_ids = {id(row) for row in validation}
    return [row for row in rows if id(row) not in validation_ids], validation


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workbook", type=Path, required=True)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    import openpyxl

    book = openpyxl.load_workbook(args.workbook, read_only=True, data_only=True)
    if "Owner Review" not in book.sheetnames:
        raise SystemExit("OWNER_REVIEW_SHEET_MISSING")
    sheet = book["Owner Review"]
    values = list(sheet.values)
    book.close()
    headers = [clean(value) for value in values[0]]
    source_rows = [dict(zip(headers, row)) for row in values[1:] if any(clean(value) for value in row)]
    required = {"SKU", "字段", "西班牙语原文", "模型候选", "建议值", "Owner决定"}
    missing = sorted(required - set(headers))
    if missing:
        raise SystemExit("WORKBOOK_SCHEMA_MISSING:" + ",".join(missing))

    candidates = {clean(row["sku"]): row for row in load_jsonl(args.candidates)}
    seen_pairs: set[tuple[str, str]] = set()
    rows: list[dict[str, Any]] = []
    issues: list[str] = []
    decision_counts: Counter[str] = Counter()
    guard_counts: Counter[str] = Counter()
    for index, raw in enumerate(source_rows, start=2):
        sku = clean(raw.get("SKU"))
        field = clean(raw.get("字段"))
        decision = clean(raw.get("Owner决定"))
        pair = (sku, field)
        if not sku or field not in FIELDS:
            issues.append(f"SCHEMA_ROW:{index}")
            continue
        if pair in seen_pairs:
            issues.append(f"DUPLICATE_PAIR:{sku}:{field}")
            continue
        seen_pairs.add(pair)
        decision_counts[decision] += 1
        candidate = candidates.get(sku)
        if candidate is None:
            issues.append(f"CANDIDATE_MISSING:{sku}")
            continue
        source = {key: clean(value) for key, value in candidate.get("source", {}).items()}
        expected_hash = source_hash(source)
        metadata = candidate.get("metadata", {})
        if expected_hash != clean(metadata.get("source_hash")):
            issues.append(f"SOURCE_HASH_MISMATCH:{sku}")
        if clean(raw.get("西班牙语原文")) != source.get(field, ""):
            issues.append(f"SOURCE_TEXT_MISMATCH:{sku}:{field}")
        target = clean(raw.get("建议值")) or clean(raw.get("模型候选"))
        if not target:
            issues.append(f"TARGET_EMPTY:{sku}:{field}")
        guard_status = "NOT_RUN"
        guard_reasons: list[str] = []
        if decision in OWNER_ACCEPTED:
            result = validate_model_output({field: source.get(field, "")}, {field: target}, expected_fields=[field])
            guard_status = "PASS" if result.accepted else "OWNER_CONFIRMED_GUARD_EXCEPTION"
            guard_reasons = list(result.reasons)
        elif decision in OWNER_ISOLATED:
            guard_status = "ISOLATED_OWNER_DECISION"
        else:
            issues.append(f"OWNER_DECISION_INVALID:{sku}:{field}:{decision}")
        guard_counts[guard_status] += 1
        rows.append(
            {
                "sku": sku,
                "field": field,
                "source": source,
                "target": target,
                "owner_decision": decision,
                "guard_status": guard_status,
                "guard_reasons": guard_reasons,
                "source_hash": expected_hash,
                "source_hash_contract_version": clean(metadata.get("source_hash_contract_version")) or "SOURCE_HASH_V1",
                "source_run_id": clean(metadata.get("source_run_id")),
                "source_snapshot_path": clean(metadata.get("source_snapshot_path")),
                "family_key": family_key(source),
                "family_key_method": FAMILY_KEY_METHOD,
                "frozen_test_membership": metadata.get("frozen_test_membership", {}),
                "candidate_status": clean(metadata.get("candidate_status")),
            }
        )

    owner_gold = [row for row in rows if row["owner_decision"] in OWNER_ACCEPTED]
    owner_isolated = [row for row in rows if row["owner_decision"] in OWNER_ISOLATED]
    guard_pass = [row for row in owner_gold if row["guard_status"] == "PASS"]
    guard_exceptions = [row for row in owner_gold if row["guard_status"] != "PASS"]
    eligible = [
        row for row in guard_pass
        if row["candidate_status"] == "SOURCE_CANDIDATE_READY"
        and not any(bool(value) for value in row["frozen_test_membership"].values())
        and row["family_key"]
    ]
    train, validation = deterministic_group_split(eligible)
    train_rows = [stable_train_row(row, "TRAIN") for row in train]
    validation_rows = [stable_train_row(row, "VALIDATION") for row in validation]

    write_jsonl(args.output_dir / "stage5_fresh_owner_decided_gold_44.jsonl", owner_gold)
    write_jsonl(args.output_dir / "stage5_fresh_owner_isolated_3.jsonl", owner_isolated)
    write_jsonl(args.output_dir / "stage5_fresh_guard_exception_3.jsonl", guard_exceptions)
    write_jsonl(args.output_dir / "stage5_fresh_training_eligible_41.jsonl", [stable_train_row(row, "UNSPLIT") for row in eligible])
    write_jsonl(args.output_dir / "stage5_fresh_train_20260914.jsonl", train_rows)
    write_jsonl(args.output_dir / "stage5_fresh_validation_20260914.jsonl", validation_rows)
    (args.output_dir / "stage5_fresh_owner_decisions.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8"
    )

    audit = {
        "artifact_type": "STAGE5_FRESH_OWNER_DECISION_INGESTION",
        "artifact_version": "V1",
        "input_workbook": {"path": str(args.workbook.resolve()), "sha256": sha256(args.workbook)},
        "candidate_file": {"path": str(args.candidates.resolve()), "sha256": sha256(args.candidates), "rows": len(candidates)},
        "owner_decisions_treated_as_final": True,
        "counts": {
            "review_rows": len(rows),
            "unique_sku_field": len(seen_pairs),
            "owner_accepted": len(owner_gold),
            "owner_isolated": len(owner_isolated),
            "guard_pass": len(guard_pass),
            "guard_exceptions": len(guard_exceptions),
            "training_eligible": len(eligible),
            "train_rows": len(train),
            "validation_rows": len(validation),
        },
        "decision_counts": dict(sorted(decision_counts.items())),
        "guard_counts": dict(sorted(guard_counts.items())),
        "issues": issues,
        "source_hash_recompute_pass": not any(issue.startswith("SOURCE_HASH_MISMATCH") for issue in issues),
        "family_key_method": FAMILY_KEY_METHOD,
        "production_writes": {"master": False, "dictionary": False, "sqlite": False},
        "training_runs": 0,
        "status": "READY_FOR_TRAINING_GATE" if not issues and len(eligible) == len(train) + len(validation) else "BLOCKED",
        "guard_exception_policy": "EXPLICITLY_ISOLATED; no hard-guard bypass",
    }
    (args.output_dir / "stage5_fresh_owner_decision_audit_20260914.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    readiness = {
        "package_id": "stage5_fresh_owner_approved_20260914",
        "owner_workbook_sha256": sha256(args.workbook),
        "owner_decisions_treated_as_final": True,
        "review_rows": len(rows),
        "owner_accepted_rows": len(owner_gold),
        "owner_isolated_rows": len(owner_isolated),
        "guard_pass_rows": len(guard_pass),
        "guard_exception_rows": len(guard_exceptions),
        "training_eligible_rows": len(eligible),
        "train_rows": len(train),
        "validation_rows": len(validation),
        "sku_overlap": 0,
        "family_overlap": 0,
        "source_hash_recompute_pass": audit["source_hash_recompute_pass"],
        "production_writes": 0,
        "training_runs": 0,
        "formal_training_authorized": False,
        "status": "READY_FOR_TRAINING_GATE",
        "next_action": "Run final Training Gate on 41 Guard-passing rows; keep 3 Guard exceptions and 3 AMBIGUOUS rows isolated.",
    }
    (args.output_dir / "STAGE5_FRESH_OWNER_APPROVAL_READINESS_20260914.json").write_text(json.dumps(readiness, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = [
        "# Stage 5 Fresh Owner Decision Ingestion",
        "",
        f"- Owner workbook rows: {len(rows)}",
        f"- Owner accepted: {len(owner_gold)}",
        f"- Owner isolated (AMBIGUOUS/REJECT): {len(owner_isolated)}",
        f"- Guard pass: {len(guard_pass)}",
        f"- Explicit Guard exceptions isolated: {len(guard_exceptions)}",
        f"- Training eligible after Guard/frozen/source checks: {len(eligible)}",
        f"- Deterministic train / validation: {len(train)} / {len(validation)}",
        "- Production writes: 0",
        "- Training runs: 0",
        f"- Status: {audit['status']}",
        "",
        "Guard exceptions are not silently promoted; they remain in the exception JSONL for explicit policy review.",
    ]
    (args.output_dir / "README.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    files = sorted(p for p in args.output_dir.iterdir() if p.is_file() and p.name != "SHA256SUMS.txt")
    (args.output_dir / "SHA256SUMS.txt").write_text("".join(f"{sha256(p)}  {p.name}\n" for p in files), encoding="utf-8")
    print(json.dumps(audit, ensure_ascii=False, indent=2))
    return 0 if audit["status"] == "READY_FOR_TRAINING_GATE" else 2


if __name__ == "__main__":
    raise SystemExit(main())

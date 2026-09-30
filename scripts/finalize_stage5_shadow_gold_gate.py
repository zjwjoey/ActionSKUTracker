"""Finalize the Stage 5 shadow review as an offline Gold-candidate gate.

This command records explicit Owner Guard exceptions and emits immutable
evidence only. It never starts training and never writes Gold, Dictionary,
Master, SQLite, or production configuration.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


EXPLICIT_EXCEPTIONS = {
    ("3218163", "description"): {
        "reason": "NUMERIC_HALLUCINATED",
        "rationale": "西语 dos asas 明确表示两个手柄；Guard 将该事实误判为新增数字。",
    },
    ("3225101", "description"): {
        "reason": "NUMERIC_HALLUCINATED",
        "rationale": "西语原文两次出现 2 cachorros，中文最终值保留两次，属于重复事实保留而非臆造。",
    },
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8") .splitlines() if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner-package", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    owner_path = args.owner_package.resolve()
    rows = load_jsonl(owner_path)
    if len(rows) != 47 or len({row.get("review_id") for row in rows}) != 47:
        raise SystemExit("OWNER_DECISION_CARDINALITY_INVALID")

    accepted = [row for row in rows if row.get("decision_status") == "OWNER_ACCEPTED"]
    isolated = [row for row in rows if row.get("decision_status") != "OWNER_ACCEPTED"]
    exception_hits: list[dict[str, Any]] = []
    non_exception_guard_failures: list[dict[str, Any]] = []
    for row in accepted:
        key = (str(row.get("sku", "")), str(row.get("field", "")))
        reasons = list(row.get("guard_reasons") or [])
        if row.get("guard_status") == "PASS":
            continue
        exception = EXPLICIT_EXCEPTIONS.get(key)
        if exception and exception["reason"] in reasons:
            exception_hits.append({
                "sku": key[0], "field": key[1], "reason": exception["reason"],
                "rationale": exception["rationale"], "owner_confirmed": True,
            })
        else:
            non_exception_guard_failures.append({"sku": key[0], "field": key[1], "reasons": reasons})

    expected_exception_keys = set(EXPLICIT_EXCEPTIONS)
    actual_exception_keys = {(item["sku"], item["field"]) for item in exception_hits}
    checks = {
        "owner_rows_47": len(rows) == 47,
        "accepted_rows_39": len(accepted) == 39,
        "isolated_rows_8": len(isolated) == 8,
        "explicit_exception_set_exact": actual_exception_keys == expected_exception_keys,
        "non_exception_guard_failures_zero": len(non_exception_guard_failures) == 0,
        "source_hash_present_all_accepted": all(bool(row.get("source_hash")) for row in accepted),
        "production_writes_false": True,
        "training_runs_zero": True,
    }
    gate_pass = all(checks.values())
    gold_rows: list[dict[str, Any]] = []
    for row in accepted:
        gold_rows.append({
            "sku": row.get("sku"), "field": row.get("field"),
            "source_spanish_value": row.get("source_spanish_value"),
            "reviewed_value": row.get("reviewed_value"),
            "source_hash": row.get("source_hash"),
            "batch_id": row.get("batch_id"),
            "gold_status": "OWNER_CONFIRMED_GOLD_CANDIDATE",
            "guard_status": row.get("guard_status"),
            "explicit_guard_exception": [item for item in exception_hits if item["sku"] == row.get("sku") and item["field"] == row.get("field")],
            "production_write": False, "training_run": False,
        })
    report = {
        "artifact_type": "STAGE5_SHADOW_FINAL_GOLD_GATE",
        "artifact_version": "V1",
        "owner_package": {"path": str(owner_path), "sha256": sha256(owner_path)},
        "counts": {
            "review_rows": len(rows), "gold_candidate_fields": len(gold_rows),
            "isolated_retry_fields": len(isolated), "explicit_guard_exceptions": len(exception_hits),
            "non_exception_guard_failures": len(non_exception_guard_failures),
            "unique_skus": len({str(row.get('sku')) for row in accepted}),
        },
        "checks": checks,
        "explicit_exceptions": exception_hits,
        "non_exception_guard_failures": non_exception_guard_failures,
        "production_writes": {"master": False, "dictionary": False, "sqlite": False, "gold": False},
        "training_runs": 0,
        "status": "PASS_OFFLINE_GOLD_CANDIDATE" if gate_pass else "BLOCKED_NO_WRITE",
        "next_action": "Run the final leakage/source-hash Training Gate; do not apply to production from this artifact.",
    }
    (args.output_dir / "stage5_shadow_gold_candidates.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in gold_rows), encoding="utf-8"
    )
    (args.output_dir / "stage5_shadow_gold_isolated_retry.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in isolated), encoding="utf-8"
    )
    (args.output_dir / "stage5_shadow_final_gold_gate.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md = [
        "# Stage 5 Shadow Final Gold Gate — 2026-09-15",
        "",
        f"- Status: **{report['status']}**",
        f"- Gold candidate fields: {len(gold_rows)}",
        f"- Isolated retry fields: {len(isolated)}",
        f"- Explicit Guard exceptions: {len(exception_hits)}",
        "- Gold/Dictionary/Master/SQLite/production writes: 0",
        "- Training runs: 0",
        "",
        "## Explicit Guard exceptions",
        "",
    ]
    for item in exception_hits:
        md.append(f"- `{item['sku']} / {item['field']}`: {item['reason']} — {item['rationale']}")
    md += ["", "## Gate checks", ""]
    for key, value in checks.items():
        md.append(f"- `{key}`: {'PASS' if value else 'FAIL'}")
    md += ["", report["next_action"], ""]
    (args.output_dir / "README.md").write_text("\n".join(md), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if gate_pass else 2


if __name__ == "__main__":
    raise SystemExit(main())

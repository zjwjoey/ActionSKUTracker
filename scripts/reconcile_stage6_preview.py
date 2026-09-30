"""Reconcile Stage 6 preview versus its blocked-conflict report.

This is a read-only preflight.  It never writes Master, SQLite, dictionary,
or any production state; it only emits immutable, filtered preview artifacts.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "runtime/stage6/20260913/offline_preview_v1"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def immutable(path: Path, payload: bytes) -> None:
    if path.exists() and path.read_bytes() != payload:
        raise RuntimeError(f"IMMUTABLE_STAGE6_RECONCILIATION_CHANGED:{path}")
    if not path.exists():
        path.write_bytes(payload)


def jsonl_bytes(rows: list[dict[str, Any]]) -> bytes:
    return b"".join(
        (json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        for row in rows
    )


def csv_bytes(rows: list[dict[str, Any]]) -> bytes:
    columns = (
        "sku", "field", "candidate_id", "review_id", "owner_disposition",
        "current_source_hash", "reviewed_source_hash", "current_target_hash",
        "reviewed_value_hash", "expected_target_hash", "policy_manifest_hash",
        "apply_action", "conflict_status", "conflict_reason",
    )
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({
            key: json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            if isinstance(value, (list, dict)) else value
            for key, value in row.items()
        })
    return ("\ufeff" + stream.getvalue()).encode("utf-8")


def main() -> int:
    preview_path = OUT / "stage6_apply_preview.jsonl"
    conflict_path = OUT / "stage6_conflict_report.csv"
    replay_path = OUT / "stage6_replay_report.json"
    rollback_path = OUT / "stage6_rollback_plan.json"
    manifest_path = OUT / "stage6_manifest.json"
    rows = [json.loads(line) for line in preview_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    conflict_lines = list(csv.DictReader(conflict_path.open(encoding="utf-8-sig", newline="")))
    replay = json.loads(replay_path.read_text(encoding="utf-8"))
    rollback = json.loads(rollback_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    eligible = [row for row in rows if not row.get("conflict_reason")]
    would_update = [row for row in eligible if row.get("apply_action") == "WOULD_UPDATE"]
    no_change = [row for row in eligible if row.get("apply_action") == "NO_CHANGE"]
    blocked_preview = [row for row in rows if row.get("conflict_reason")]

    if len(rows) != 91 or len(eligible) != 55 or len(would_update) != 48 or len(no_change) != 7:
        raise RuntimeError("STAGE6_PREVIEW_RECONCILIATION_COUNT_MISMATCH")
    if len(conflict_lines) != 48:
        raise RuntimeError("STAGE6_CONFLICT_REPORT_COUNT_MISMATCH")
    if len(blocked_preview) != 36:
        raise RuntimeError("STAGE6_PREVIEW_SOURCE_CONFLICT_COUNT_MISMATCH")
    if sum(1 for row in conflict_lines if "SOURCE_CONFLICT" in row.get("conflict_reason", "")) != 36:
        raise RuntimeError("STAGE6_SOURCE_CONFLICT_REPORT_COUNT_MISMATCH")
    if sum(1 for row in conflict_lines if "NOT_OWNER_APPROVED" in row.get("conflict_reason", "")) != 12:
        raise RuntimeError("STAGE6_NOT_OWNER_APPROVED_REPORT_COUNT_MISMATCH")

    # Freshness checks are evaluated on the eligible set, not on blocked rows.
    source_fresh = all(row["current_source_hash"] == row["reviewed_source_hash"] for row in eligible)
    review_fresh = all(bool(row.get("reviewed_value_hash")) for row in eligible)
    target_fresh = all(row["current_target_hash"] == row["expected_target_hash"] for row in eligible)
    policy_hashes = {row["policy_manifest_hash"] for row in eligible}
    policy_fresh = len(policy_hashes) == 1 and next(iter(policy_hashes)) == manifest["policy_manifest_hash"]
    conflict_classes_zero = all(not row.get("conflict_reason") for row in eligible)

    # The rollback plan must cover exactly the 48 actual updates and remain a plan.
    rollback_rows = rollback.get("rows", [])
    rollback_by_key = {(row.get("sku"), row.get("field")): row for row in rollback_rows}
    rollback_fresh = (
        rollback.get("production_backup_created") is False
        and len(rollback_rows) == 48
        and all((row["sku"], row["field"]) in rollback_by_key for row in would_update)
        and all(rollback_by_key[(row["sku"], row["field"])] ["target_hash_before"] == row["current_target_hash"] for row in would_update)
    )
    replay_ok = (
        replay.get("stable") is True
        and replay.get("production_writes") == 0
        and replay.get("apply_events") == 0
        and replay.get("preview_row_count_first") == 91
        and replay.get("preview_row_count_second") == 91
        and replay.get("conflict_count_first") == 48
        and replay.get("conflict_count_second") == 48
        and replay.get("preview_hash_first") == replay.get("preview_hash_second")
    )

    outputs = {
        "stage6_eligible_55.jsonl": jsonl_bytes(eligible),
        "stage6_eligible_55.csv": csv_bytes(eligible),
        "stage6_would_update_48.jsonl": jsonl_bytes(would_update),
        "stage6_would_update_48.csv": csv_bytes(would_update),
        "stage6_no_change_7.jsonl": jsonl_bytes(no_change),
        "stage6_no_change_7.csv": csv_bytes(no_change),
    }
    for name, payload in outputs.items():
        immutable(OUT / name, payload)

    protected = {
        "master": sha256(ROOT / "runtime/master/Action_Master.xlsx"),
        "sqlite": sha256(ROOT / "runtime/db/action_tracker.db"),
        "frozen_test": sha256(ROOT / "runtime/training/qwen3_8b/20260911/stage4_test_only_485.jsonl"),
    }
    result = {
        "contract_id": "STAGE6_PREVIEW_RECONCILIATION_V1",
        "production_writes": 0,
        "conflict_report_rows": len(conflict_lines),
        "conflict_report_counts": {"SOURCE_CONFLICT": 36, "NOT_OWNER_APPROVED": 12},
        "apply_preview_rows": len(rows),
        "eligible_rows": len(eligible),
        "would_update_rows": len(would_update),
        "no_change_rows": len(no_change),
        "eligible_conflict_class_count": sum(len(row.get("conflict_reason", [])) for row in eligible),
        "freshness": {
            "source": source_fresh,
            "review": review_fresh,
            "target": target_fresh,
            "policy": policy_fresh,
        },
        "replay_preflight": replay_ok,
        "rollback_preflight": rollback_fresh,
        "protected_hashes": protected,
        "protected_files_unchanged": manifest.get("protected_files_unchanged") is True,
        "status": "READY_FOR_OWNER_PRODUCTION_APPLY_AUTHORIZATION"
        if all((source_fresh, review_fresh, target_fresh, policy_fresh, conflict_classes_zero, replay_ok, rollback_fresh))
        else "BLOCKED_RECONCILIATION",
        "artifacts": {name: sha256(OUT / name) for name in sorted(outputs)},
    }
    immutable(OUT / "stage6_reconciliation_v1.json", (json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8"))
    report = (
        "# Stage 6 Apply Preview Reconciliation\n\n"
        f"- Conflict report: 48 rows = 36 `SOURCE_CONFLICT` + 12 `NOT_OWNER_APPROVED`.\n"
        f"- Apply preview: 91 rows = 55 eligible (48 `WOULD_UPDATE` + 7 `NO_CHANGE`) + 36 source-conflict rows.\n"
        f"- Eligible conflict classes: 0.\n"
        f"- Hash freshness: source={source_fresh}, review={review_fresh}, target={target_fresh}, policy={policy_fresh}.\n"
        f"- Replay preflight: {replay_ok}; rollback preflight: {rollback_fresh}.\n"
        "- Production writes: 0; Master/SQLite/dictionary remain untouched.\n"
        f"- Status: `{result['status']}`.\n"
    ).encode("utf-8")
    immutable(OUT / "stage6_reconciliation_v1.md", report)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

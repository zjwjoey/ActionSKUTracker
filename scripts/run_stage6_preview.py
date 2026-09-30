"""Run Stage 6 field-level offline preview; production writes are impossible."""
from __future__ import annotations

import csv
import io
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from action_tracker.excel.reader import load_current
from action_tracker.services.hashing import localization_source_hash
from action_tracker.stage5.owner_review import (
    certify_owner_review, load_replayed_candidates, load_signed_rows,
)
from action_tracker.stage5.pipeline import canonical_json, sha256_file
from action_tracker.stage5.source_candidate_v2 import (
    SOURCE_FIELD_KEYS, source_consistency_rules_manifest, source_from_mapping, source_hash,
)
from action_tracker.stage6.preview import digest, preview_hash, preview_one


REPLAY = ROOT / "runtime/stage5/20260913/engineering_replay_v3"
SIGNED_REPORT = ROOT / "runtime/training/qwen3_8b/20260911/stage4_stage5_owner_signed_recheck_20260912.json"
OUTPUT = ROOT / "runtime/stage6/20260913/offline_preview_v1"
RUNS = (
    "2026-09-07_025601", "2026-09-08_035740", "2026-09-09_032720",
    "2026-09-10_030315", "2026-09-12_024403",
)


def write_immutable(path: Path, content: bytes) -> None:
    if path.exists():
        if path.read_bytes() != content:
            raise ValueError(f"IMMUTABLE_STAGE6_ARTIFACT_CHANGED:{path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def csv_bytes(rows: list[dict], columns: tuple[str, ...]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: canonical_json(value) if isinstance(value, (dict, list)) else value for key, value in row.items()})
    return ("\ufeff" + stream.getvalue()).encode("utf-8")


def stage5_sources() -> dict[tuple[str, str], dict[str, str]]:
    rows: dict[tuple[str, str], dict[str, str]] = {}
    for batch in ("01", "02", "03"):
        path = ROOT / f"runtime/stage5/20260912/inputs/stage5_batch_{batch}.jsonl"
        for line in path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            source = json.loads(row["messages"][0]["content"])
            key = (str(row["metadata"]["sku"]), row["metadata"]["source_hash"])
            if source_hash(source) != key[1]:
                raise ValueError(f"STAGE5_INPUT_SOURCE_HASH_MISMATCH:{key[0]}")
            rows[key] = source
    return rows


def official_snapshot_paths(pairs: set[tuple[str, str]]) -> dict[tuple[str, str], str]:
    matched: dict[tuple[str, str], str] = {}
    for run_id in RUNS:
        path = ROOT / "runtime/snapshots" / run_id[:10] / run_id / "products_normalized.csv"
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                sku = str(row.get("sku") or "").strip()
                if not any(pair[0] == sku for pair in pairs):
                    continue
                source = source_from_mapping({field: row.get(key) for field, key in SOURCE_FIELD_KEYS.items()})
                key = (sku, source_hash(source))
                if key in pairs:
                    matched[key] = str(path.resolve())
    return matched


def policy_manifest(owner_workbook_hash: str) -> dict[str, object]:
    contract_path = ROOT / "config/stage6/stage6_preview_contract.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    if contract["production_write_enabled"] is not False:
        raise ValueError("PRODUCTION_WRITE_MUST_BE_DISABLED")
    stage5_contract = json.loads((ROOT / "config/stage5/stage5_pipeline_contract.json").read_text(encoding="utf-8"))
    payload = {
        "contract_id": contract["contract_id"],
        "target_schema_version": contract["target_schema_version"],
        "source_hash_contract_version": contract["source_hash_contract_version"],
        "dictionary_version_sha256": sha256_file(ROOT / "data/dictionary/baseline_manifest.json"),
        "resolver_version": stage5_contract["resolver"]["version"],
        "guard_version_sha256": sha256_file(ROOT / "src/action_tracker/translation/model_guard.py"),
        "guard_policy_sha256": sha256_file(ROOT / "config/stage5/stage5_guard_policy.json"),
        "title_display_policy_sha256": sha256_file(ROOT / "config/stage5/title_display_policy.json"),
        "source_consistency_rules": source_consistency_rules_manifest(),
        "review_policy_version": contract["review_policy_version"],
        "apply_policy_version": contract["apply_policy_version"],
        "stage6_contract_sha256": sha256_file(contract_path),
        "owner_signed_workbook_sha256": owner_workbook_hash,
        "allow_production_write": False,
    }
    return {**payload, "policy_manifest_hash": digest(payload)}


def build_preview() -> tuple[list[dict], list[dict], dict[str, object]]:
    signed_report = json.loads(SIGNED_REPORT.read_text(encoding="utf-8"))
    signed_entry = signed_report["owner_signed_input"]
    owner_rows = load_signed_rows(Path(signed_entry["path"]), signed_entry["sha256"])
    candidates, replay_manifests = load_replayed_candidates(REPLAY)
    review = certify_owner_review(owner_rows, candidates)
    if review["issues"]:
        raise ValueError(f"STAGE5_OWNER_REVIEW_NOT_CERTIFIED:{review['issues'][:5]}")
    sources = stage5_sources()
    pairs = {(item["candidate"]["sku"], item["candidate"]["source_hash"]) for item in review["approved"]}
    snapshots = official_snapshot_paths(pairs)
    target_path = ROOT / "runtime/master/Action_Master.xlsx"
    target = load_current(target_path)
    policy = policy_manifest(signed_entry["sha256"])
    rows: list[dict] = []
    for item in review["approved"]:
        owner, candidate = item["owner"], item["candidate"]
        key = (candidate["sku"], candidate["source_hash"])
        row = preview_one(
            owner, candidate, sources.get(key), target.get(candidate["sku"]),
            policy_hash=str(policy["policy_manifest_hash"]), source_snapshot_path=snapshots.get(key),
        )
        rows.append(row)
    rows.sort(key=lambda row: (row["sku"], row["field"], row["candidate_id"]))
    isolated = [
        {**row, "conflict_status": "DO_NOT_APPLY", "conflict_reason": ["NOT_OWNER_APPROVED"],
         "apply_action": "BLOCKED_CONFLICT"}
        for row in review["isolated"]
    ]
    return rows, isolated, {
        "policy": policy, "review_counts": review["counts"],
        "replay_manifest_sha256": replay_manifests,
        "owner_signed_workbook": signed_entry,
        "master_sha256": sha256_file(target_path),
    }


def main() -> int:
    master = ROOT / "runtime/master/Action_Master.xlsx"
    sqlite = ROOT / "runtime/db/action_tracker.db"
    frozen = ROOT / "runtime/training/qwen3_8b/20260911/stage4_test_only_485.jsonl"
    protected_before = {str(p): sha256_file(p) for p in (master, sqlite, frozen)}
    rows, isolated, context = build_preview()
    # Independent in-process replay; subsequent CLI invocation also checks
    # immutable output bytes rather than overwriting an existing preview.
    replay_rows, replay_isolated, replay_context = build_preview()
    stable = (
        preview_hash(rows) == preview_hash(replay_rows)
        and canonical_json(isolated) == canonical_json(replay_isolated)
        and context == replay_context
    )
    if not stable:
        raise ValueError("STAGE6_REPLAY_MISMATCH")
    protected_after = {str(p): sha256_file(p) for p in (master, sqlite, frozen)}
    if protected_before != protected_after:
        raise ValueError("PROTECTED_PRODUCTION_FILE_CHANGED_DURING_PREVIEW")
    conflicts = [row for row in rows if row["conflict_reason"]] + isolated
    conflict_counts = Counter(reason for row in conflicts for reason in row["conflict_reason"])
    certified = [row for row in rows if not row["conflict_reason"]]
    if len(rows) != 91 or len(isolated) != 12 or len(certified) + len(conflicts) != 103:
        raise ValueError("STAGE6_REVIEW_RECONCILIATION_FAILED")

    policy = context["policy"]
    artifacts = {
        "stage6_policy_manifest.json": json_bytes(policy),
        "stage6_apply_preview.jsonl": b"".join((canonical_json(row) + "\n").encode("utf-8") for row in rows),
        "stage6_apply_preview.csv": csv_bytes(rows, (
            "sku", "field", "candidate_id", "review_id", "owner_disposition",
            "current_source_hash", "reviewed_source_hash", "current_target_value",
            "current_target_hash", "reviewed_value", "reviewed_value_hash",
            "expected_target_hash", "apply_action", "conflict_status", "conflict_reason",
        )),
        "stage6_conflict_report.csv": csv_bytes(conflicts, (
            "sku", "field", "candidate_id", "owner_disposition", "apply_action", "conflict_reason",
        )),
        "stage6_input_certification.json": json_bytes({
            "owner_reviewed": 103, "owner_approved": 91, "owner_isolated": 12,
            "certified_field_count": len(certified), "blocked_owner_approved_count": len(rows) - len(certified),
            "source_snapshot_bound_count": sum(bool(row["source_snapshot_path"]) for row in rows),
            "source_fresh_count": sum(row["current_source_hash"] == row["reviewed_source_hash"] for row in rows),
            "policy_manifest_hash": policy["policy_manifest_hash"],
            "state": "INPUT_CERTIFIED_FOR_NONCONFLICTING_SUBSET",
        }),
        "stage6_replay_report.json": json_bytes({
            "preview_row_count_first": len(rows), "preview_row_count_second": len(replay_rows),
            "conflict_count_first": len(conflicts),
            "conflict_count_second": sum(bool(row["conflict_reason"]) for row in replay_rows) + len(replay_isolated),
            "preview_hash_first": preview_hash(rows), "preview_hash_second": preview_hash(replay_rows),
            "stable": stable, "production_writes": 0, "apply_events": 0,
        }),
    }
    apply_run_id = digest([policy["policy_manifest_hash"], context["master_sha256"], context["owner_signed_workbook"]["sha256"]])
    rollback_rows = [{
        "sku": row["sku"], "field": row["field"], "before_value": row["current_target_value"],
        "after_value": row["reviewed_value"], "target_hash_before": row["current_target_hash"],
        "reviewed_value_hash": row["reviewed_value_hash"], "apply_run_id": apply_run_id,
        "reverse_rule": "Only after separately authorized Apply; restore before_value if target still hashes to reviewed_value_hash.",
    } for row in certified if row["apply_action"] == "WOULD_UPDATE"]
    artifacts["stage6_rollback_plan.json"] = json_bytes({
        "kind": "PREVIEW_ONLY_ROLLBACK_DESIGN", "production_backup_created": False,
        "apply_run_id": apply_run_id, "rows": rollback_rows,
    })
    for name, content in artifacts.items():
        write_immutable(OUTPUT / name, content)
    manifest_path = OUTPUT / "stage6_manifest.json"
    created_at = json.loads(manifest_path.read_text(encoding="utf-8"))["generated_at"] if manifest_path.exists() else datetime.now(timezone.utc).isoformat()
    manifest = {
        "contract_id": "STAGE6_OFFLINE_PREVIEW_V1", "generated_at": created_at,
        "status": "REPLAY_VERIFIED" if stable and certified else "INPUT_CERTIFICATION_BLOCKED",
        "scope": "CERTIFIED_NONCONFLICTING_FIELDS_ONLY",
        "owner_approved_count": 91, "certified_count": len(certified),
        "preview_row_count": len(rows), "would_update_count": sum(row["apply_action"] == "WOULD_UPDATE" for row in rows),
        "no_change_count": sum(row["apply_action"] == "NO_CHANGE" for row in rows),
        "blocked_count": len(conflicts), "conflict_counts": dict(sorted(conflict_counts.items())),
        "policy_manifest_hash": policy["policy_manifest_hash"],
        "source_review_sha256": context["owner_signed_workbook"]["sha256"],
        "master_sha256": context["master_sha256"],
        "protected_files_unchanged": protected_before == protected_after,
        "artifacts": {name: sha256_file(OUTPUT / name) for name in sorted(artifacts)},
        "production_writes": 0, "apply_events": 0,
        "owner_approved": False, "production_apply_authorized": False,
    }
    write_immutable(manifest_path, json_bytes(manifest))
    report = (
        "# Stage 6 offline preview engineering\n\n"
        f"- Status: `{manifest['status']}` for {len(certified)} certified nonconflicting fields only.\n"
        f"- Signed Stage 5 review: 103; owner-accepted: 91; major/ambiguous isolated: 12.\n"
        f"- Preview: {len(rows)} rows; would update: {manifest['would_update_count']}; no change: {manifest['no_change_count']}.\n"
        f"- Blocked: {len(conflicts)}; classes: {dict(sorted(conflict_counts.items()))}.\n"
        f"- Replay hash: `{preview_hash(rows)}`; same on two runs.\n"
        "- Rollback file is a plan, not a production backup.\n"
        "- No production Apply, Master/SQLite/dictionary write, or owner authorization occurred.\n"
    )
    write_immutable(OUTPUT / "stage6_engineering_report.md", report.encode("utf-8"))
    print(json.dumps({
        "status": manifest["status"], "certified": len(certified), "preview": len(rows),
        "conflicts": dict(sorted(conflict_counts.items())), "production_writes": 0,
        "output_dir": str(OUTPUT),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

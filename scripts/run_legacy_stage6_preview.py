"""Run a read-only Stage6 preview for explicitly approved legacy artifacts."""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import sqlite3
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from action_tracker.excel.reader import load_current
from action_tracker.services.hashing import field_source_hash
from action_tracker.stage6.legacy_provenance import (
    adapt_legacy_record, legacy_conflicts, normalize_source_text,
)
from action_tracker.stage6.preview import preview_one

FIELD_TARGET = {
    "name": "name_zh", "cat1": "cat1_zh", "cat2": "cat2_zh",
    "spec": "spec_zh", "description": "desc_zh", "details": "details_zh",
}
FIELD_SQLITE = {
    "name": "name", "cat1": "cat1", "cat2": "cat2",
    "spec": "spec", "description": "description", "details": "details",
}
SOURCE_MAP = {
    "name": "name_es", "cat1": "cat1_es", "cat2": "cat2_es",
    "spec": "spec_es", "description": "desc_es", "details": "details_es",
}
ARTIFACT_TO_FIELD = {
    "name": "name", "cat1": "cat1", "cat2": "cat2",
    "spec": "spec", "description": "description", "details": "details",
}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, Any]], headers: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(row.get(key), ensure_ascii=False) if isinstance(row.get(key), (list, dict)) else row.get(key, "") for key in headers})


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def state_hashes(master_path: Path, sqlite_path: Path, dictionary_dir: Path) -> dict[str, str]:
    paths = [master_path, sqlite_path]
    if dictionary_dir.exists():
        paths.extend(sorted(path for path in dictionary_dir.rglob("*") if path.is_file()))
    return {str(path.resolve()): file_hash(path) for path in paths if path.exists()}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--owner-csv", type=Path, required=True)
    parser.add_argument("--queue-csv", type=Path, required=True)
    parser.add_argument("--revalidation-csv", type=Path, required=True)
    parser.add_argument("--migration-candidates-csv", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--master", type=Path, required=True)
    parser.add_argument("--sqlite", type=Path, required=True)
    parser.add_argument("--dictionary-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--stamp", default=dt.datetime.now().strftime("%Y%m%d_%H%M%S"))
    parser.add_argument("--targeted-tests-result", default="NOT_RECORDED")
    parser.add_argument("--full-pytest-result", default="NOT_RECORDED")
    parser.add_argument("--tests-failed", type=int, default=0)
    return parser.parse_args()


def _artifact_path(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    resolved_root = root.resolve()
    if resolved_root not in path.parents:
        raise ValueError(f"ARTIFACT_PATH_OUTSIDE_EVIDENCE_ROOT:{relative}")
    if not path.is_file():
        raise ValueError(f"REVIEW_ARTIFACT_MISSING:{relative}")
    return path


def _verify_review_artifact(
    evidence_root: Path, row: dict[str, str], cache: dict[str, tuple[Path, Any]],
) -> tuple[Path, str, dict[str, Any]]:
    relative = row["review_artifact"]
    if relative not in cache:
        path = _artifact_path(evidence_root, relative)
        if path.suffix.lower() == ".csv":
            payload: Any = read_csv(path)
            kind = "FIELD_REVIEW_CSV"
        elif path.suffix.lower() == ".json":
            payload = json.loads(path.read_text(encoding="utf-8"))
            kind = "FIELD_APPLY_PREVIEW_JSON"
        else:
            raise ValueError(f"UNSUPPORTED_REVIEW_ARTIFACT:{relative}")
        cache[relative] = (path, (kind, payload))
    path, (kind, payload) = cache[relative]
    if kind == "FIELD_REVIEW_CSV":
        matches = [item for item in payload if str(item.get("sku") or "").strip() == row["sku"] and str(item.get("field_name") or "").strip() == row["field"]]
        if len(matches) != 1:
            raise ValueError(f"REVIEW_ARTIFACT_ROW_NOT_UNIQUE:{row['sku']}:{row['field']}:{len(matches)}")
        item = matches[0]
        actuals = {
            "historical_source_text": item.get("source_value", ""),
            "reviewed_value": item.get("reviewed_value", ""),
            "review_decision": item.get("decision", ""),
            "legacy_source_hash": item.get("source_hash", ""),
        }
        if item.get("hard_qa_status") not in {"PASS", "PASS_WITH_WARNINGS", ""}:
            raise ValueError(f"REVIEW_ARTIFACT_QA_NOT_ACCEPTABLE:{row['sku']}:{row['field']}")
    else:
        matches = [item for item in payload if str(item.get("sku") or "").strip() == row["sku"]]
        if len(matches) != 1:
            raise ValueError(f"REVIEW_ARTIFACT_ROW_NOT_UNIQUE:{row['sku']}:{row['field']}:{len(matches)}")
        item = matches[0]
        actuals = {
            "historical_source_text": item.get("name_es", ""),
            "reviewed_value": item.get("release_candidate_value", ""),
            "review_decision": item.get("codex_decision_v2", ""),
            "legacy_source_hash": item.get("source_hash", ""),
        }
        if item.get("match_status") != "MATCHED" or str(item.get("block_reason") or "").strip():
            raise ValueError(f"REVIEW_ARTIFACT_MATCH_NOT_CLEAN:{row['sku']}:{row['field']}")
    for key in ("historical_source_text", "reviewed_value", "review_decision", "legacy_source_hash"):
        if normalize_source_text(actuals[key]) != normalize_source_text(row.get(key, "")):
            raise ValueError(f"REVIEW_ARTIFACT_VALUE_MISMATCH:{row['sku']}:{row['field']}:{key}")
    return path, kind, item


def _sqlite_field(conn: sqlite3.Connection, sku: str, field: str) -> str | None:
    column = FIELD_SQLITE[field]
    query = f"SELECT {column} FROM product_localizations WHERE official_sku=? AND language='zh'"
    row = conn.execute(query, (sku,)).fetchone()
    return None if row is None else ("" if row[0] is None else str(row[0]))


def _historical_metadata(artifact_kind: str, row: dict[str, Any]) -> dict[str, Any]:
    keys = (
        ("legacy_translator_model", "translator_model"),
        ("legacy_review_model", "review_model"),
        ("legacy_translator_prompt_version", "translator_prompt_version"),
        ("legacy_review_policy_version", "review_policy_version"),
        ("legacy_policy_version", "policy_version"),
        ("legacy_candidate_source", "candidate_source"),
        ("legacy_candidate_value", "candidate_value"),
        ("legacy_review_note", "review_note"),
        ("legacy_issue_type", "issue_type"),
        ("legacy_hard_qa_status", "hard_qa_status"),
        ("legacy_qwen_candidate", "qwen_zh"),
        ("legacy_source_hash_contract", "source_hash_contract"),
        ("legacy_review_batch", "review_batch"),
        ("legacy_correction_type", "correction_type_v2"),
    )
    return {dest: row[source] for dest, source in keys if row.get(source) not in (None, "")}


def main() -> int:
    args = parse_args()
    owner_rows = read_csv(args.owner_csv)
    queue_rows = read_csv(args.queue_csv)
    revalidation_rows = read_csv(args.revalidation_csv)
    candidate_rows = read_csv(args.migration_candidates_csv)
    owner_by_key = {(row["sku"], row["field"]): row for row in owner_rows}
    queue_by_key = {(row["sku"], row["field"]): row for row in queue_rows}
    revalidation_by_key = {(row["sku"], row["field"]): row for row in revalidation_rows}
    candidate_by_key = {(row["sku"], row["field"]): row for row in candidate_rows}
    if len(owner_by_key) != len(owner_rows) or len(queue_by_key) != len(queue_rows) or len(revalidation_by_key) != len(revalidation_rows) or len(candidate_by_key) != len(candidate_rows):
        raise ValueError("DUPLICATE_SKU_FIELD_KEY")
    if set(owner_by_key) != set(queue_by_key):
        raise ValueError("OWNER_QUEUE_KEY_SET_MISMATCH")
    if not set(owner_by_key) <= set(revalidation_by_key) or not set(owner_by_key) <= set(candidate_by_key):
        raise ValueError("OWNER_EVIDENCE_JOIN_INCOMPLETE")

    owner_file_hash = file_hash(args.owner_csv)
    protected_before = state_hashes(args.master, args.sqlite, args.dictionary_dir)
    master = load_current(args.master)
    conn = sqlite3.connect(f"file:{args.sqlite.resolve().as_posix()}?mode=ro", uri=True)
    conn.execute("PRAGMA query_only=ON")
    artifact_cache: dict[str, tuple[Path, Any]] = {}
    candidate_output: list[dict[str, Any]] = []
    preview_output: list[dict[str, Any]] = []
    readiness_output: list[dict[str, Any]] = []
    status_counts: Counter[str] = Counter()
    conflict_counts: Counter[str] = Counter()
    decisions = Counter(row.get("owner_decision", "") for row in owner_rows)
    preview_policy_hash = hashlib.sha256(b"stage6-legacy-read-only-preview-v1").hexdigest()

    for key, approval in sorted(owner_by_key.items()):
        sku, field = key
        revalidation = revalidation_by_key[key]
        queue_row = queue_by_key[key]
        candidate_row = candidate_by_key[key]
        master_row = master.get(sku)
        if master_row is None:
            status_counts["LEGACY_PROVENANCE_BLOCKED"] += 1
            candidate_output.append({"sku": sku, "field": field, "owner_decision": approval.get("owner_decision"), "legacy_provenance_status": "BLOCKED_CONFLICT", "block_reason": ["MASTER_SKU_MISSING"]})
            continue
        artifact_path, artifact_kind, artifact_row = _verify_review_artifact(args.evidence_root, revalidation, artifact_cache)
        if approval.get("candidate_id") != queue_row.get("candidate_id") or approval.get("source_hash") != queue_row.get("source_hash"):
            raise ValueError(f"OWNER_QUEUE_BINDING_MISMATCH:{sku}:{field}")
        for name in ("spanish_source", "reviewed_value", "master_value", "sqlite_value", "source_hash"):
            if normalize_source_text(approval.get(name)) != normalize_source_text(queue_row.get(name)):
                raise ValueError(f"OWNER_QUEUE_DATA_MISMATCH:{sku}:{field}:{name}")
        for name in ("reviewed_value", "review_decision", "current_source_text", "current_field_hash", "historical_source_text", "historical_field_hash"):
            candidate_name = {
                "current_source_text": "source_spanish_value",
                "current_field_hash": "source_hash",
                "historical_source_text": "source_spanish_value",
                "historical_field_hash": "source_hash",
            }.get(name, name)
            expected = candidate_row.get(candidate_name, "")
            observed = revalidation.get(name, "")
            if name == "historical_source_text":
                observed = artifact_row.get("source_value", artifact_row.get("name_es", ""))
            if name == "reviewed_value":
                observed = artifact_row.get("reviewed_value", artifact_row.get("release_candidate_value", ""))
            if name == "review_decision":
                observed = artifact_row.get("decision", artifact_row.get("codex_decision_v2", ""))
            if name in {"current_source_text", "current_field_hash", "historical_field_hash"}:
                continue
            if normalize_source_text(expected) != normalize_source_text(observed):
                raise ValueError(f"MIGRATION_CANDIDATE_ARTIFACT_MISMATCH:{sku}:{field}:{name}")
        if normalize_source_text(candidate_row.get("source_spanish_value")) != normalize_source_text(revalidation.get("current_source_text")):
            raise ValueError(f"CANDIDATE_CURRENT_SOURCE_MISMATCH:{sku}:{field}")
        if candidate_row.get("source_hash") != revalidation.get("current_field_hash") or candidate_row.get("hash_scope") != "field":
            raise ValueError(f"CANDIDATE_CURRENT_FIELD_HASH_MISMATCH:{sku}:{field}")
        if approval.get("owner_decision") not in {"ACCEPT", "REJECT", "HOLD"}:
            raise ValueError(f"INVALID_OWNER_DECISION:{sku}:{field}")
        current_sqlite_value = _sqlite_field(conn, sku, field)
        source = {
            "name": master_row.get("name_es", "") or "", "cat1": master_row.get("cat1_es", "") or "",
            "cat2": master_row.get("cat2_es", "") or "", "spec": master_row.get("spec_es", "") or "",
            "description": master_row.get("desc_es", "") or "", "details": master_row.get("details_es", "") or "",
        }
        candidate, owner, provenance = adapt_legacy_record(
            revalidation, approval, artifact_kind=artifact_kind,
            artifact_sha256=file_hash(artifact_path),
            owner_approval_artifact=str(args.owner_csv.resolve()),
            owner_approval_sha256=owner_file_hash,
            current_sqlite_value=current_sqlite_value,
            historical_metadata=_historical_metadata(artifact_kind, artifact_row),
        )
        target = FIELD_TARGET[field]
        if normalize_source_text(master_row.get(target)) != normalize_source_text(approval.get("master_value")):
            baseline_conflict = ["MASTER_BASELINE_CHANGED"]
        else:
            baseline_conflict = []
        preconflicts = set(legacy_conflicts(
            provenance.as_dict(), owner, candidate, source, master_row,
            field_target=target,
        )) | set(baseline_conflict)
        if approval.get("owner_decision") == "REJECT":
            preconflicts.add("OWNER_REJECTED")
        elif approval.get("owner_decision") == "HOLD":
            preconflicts.add("OWNER_HOLD")
        elif approval.get("owner_decision") != "ACCEPT":
            preconflicts.add("OWNER_NOT_APPROVED")
        if candidate.get("parsed_candidate") != approval.get("reviewed_value"):
            preconflicts.add("CANDIDATE_VALUE_MISMATCH")
        is_sufficient = not preconflicts
        if is_sufficient:
            status = "LEGACY_PROVENANCE_SUFFICIENT_FOR_PREVIEW"
        else:
            status = "LEGACY_PROVENANCE_BLOCKED"
        status_counts[status] += 1
        candidate_output.append({
            "candidate_id": candidate["candidate_id"], "sku": sku, "field": field,
            "provenance_type": candidate["provenance_type"],
            "artifact_path": provenance.artifact_path, "artifact_kind": artifact_kind,
            "artifact_sha256": provenance.artifact_sha256,
            "historical_source_text": provenance.historical_source_text,
            "historical_source_hash": provenance.historical_source_hash,
            "current_source_text": provenance.current_source_text,
            "current_source_hash": provenance.current_source_hash,
            "source_revalidated": provenance.source_revalidated,
            "source_revalidation_method": provenance.source_revalidation_method,
            "reviewed_value": provenance.reviewed_value,
            "review_decision": provenance.review_decision, "reviewed_at": provenance.reviewed_at,
            "review_evidence_id": provenance.review_evidence_id,
            "owner_decision": provenance.owner_decision, "owner_note": provenance.owner_note,
            "owner_approval_artifact": provenance.owner_approval_artifact,
            "owner_approval_sha256": provenance.owner_approval_sha256,
            "master_baseline": provenance.master_baseline, "sqlite_baseline": provenance.sqlite_baseline,
            "legacy_missing_fields": list(provenance.legacy_missing_fields),
            "historical_metadata": dict(provenance.historical_metadata),
            "evidence_absence_reason": dict(provenance.evidence_absence_reason),
            "legacy_provenance_status": status, "block_reason": sorted(preconflicts),
        })
        if approval.get("owner_decision") != "ACCEPT" or not is_sufficient:
            conflict_counts.update(preconflicts)
            continue
        preview = preview_one(
            owner, candidate, source, master_row,
            policy_hash=preview_policy_hash, source_snapshot_path=None,
        )
        reasons = preview.get("conflict_reason") or []
        conflict_counts.update(reasons)
        stage6_status = (
            "STAGE6_PREVIEW_PASS_OWNER_APPROVED"
            if preview["apply_action"] in {"NO_CHANGE", "WOULD_UPDATE"} and not reasons
            else "STAGE6_PREVIEW_BLOCKED"
        )
        preview_output.append({
            **preview, "stage6_status": stage6_status,
            "owner_decision": approval.get("owner_decision"),
            "owner_approval_status": "OWNER_APPROVED_FROM_COMPLETED_FILE",
            "legacy_missing_fields": list(provenance.legacy_missing_fields),
            "evidence_absence_reason": dict(provenance.evidence_absence_reason),
        })
        readiness_output.append({
            "candidate_id": candidate["candidate_id"], "sku": sku, "field": field,
            "stage6_action": preview["apply_action"], "stage6_status": stage6_status,
            "readiness_status": "READY_FOR_APPLY_PLAN" if stage6_status == "STAGE6_PREVIEW_PASS_OWNER_APPROVED" else "BLOCKED",
            "owner_decision": approval.get("owner_decision"),
            "current_target_value": preview.get("current_target_value"),
            "approved_value": approval.get("reviewed_value"),
            "current_source_hash": preview.get("current_source_hash"),
            "candidate_source_hash": preview.get("reviewed_source_hash"),
            "conflict_reason": reasons,
        })

    conn.close()
    protected_after = state_hashes(args.master, args.sqlite, args.dictionary_dir)
    if protected_before != protected_after:
        raise RuntimeError("PROTECTED_PRODUCTION_INPUT_CHANGED_DURING_PREVIEW")

    candidate_headers = list(candidate_output[0]) if candidate_output else []
    preview_headers = list(preview_output[0]) if preview_output else [
        "sku", "field", "candidate_id", "provenance_type", "apply_action", "stage6_status",
        "conflict_status", "conflict_reason", "current_source_hash", "reviewed_source_hash",
    ]
    readiness_headers = list(readiness_output[0]) if readiness_output else []
    output_paths = {
        "candidates": args.output_dir / f"localization_legacy_stage6_candidates_{args.stamp}.csv",
        "preview": args.output_dir / f"localization_stage6_legacy_preview_{args.stamp}.csv",
        "readiness": args.output_dir / f"localization_legacy_apply_readiness_{args.stamp}.csv",
    }
    write_csv(output_paths["candidates"], candidate_output, candidate_headers)
    write_csv(output_paths["preview"], preview_output, preview_headers)
    write_csv(output_paths["readiness"], readiness_output, readiness_headers)
    action_counts = Counter(row.get("apply_action", "") for row in preview_output)
    readiness_counts = Counter(row.get("readiness_status", "") for row in readiness_output)
    report_path = args.output_dir / f"legacy_artifact_stage6_adapter_report_{args.stamp}.md"
    current_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True,
    ).stdout.strip()
    with report_path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write("# LEGACY ARTIFACT STAGE6 ADAPTER REPORT\n\n")
        handle.write(f"Generated: {dt.datetime.now(dt.timezone.utc).isoformat()}\n\n")
        handle.write(f"## GIT\n\n- Base main: `788e290ece1ec4bb4a2ba110b0ab2ffe1f451864`\n- Branch: `fix/legacy-artifact-stage6-provenance`\n- Commit: `{current_commit}`\n\n")
        handle.write("## LEGACY INPUT\n\n")
        handle.write(f"- Owner ACCEPT: {decisions.get('ACCEPT', 0)}\n- REJECT excluded: {decisions.get('REJECT', 0)}\n- HOLD excluded: {decisions.get('HOLD', 0)}\n")
        handle.write("- Owner CSV SHA-256: `" + owner_file_hash + "`\n\n")
        handle.write("## PROVENANCE ADAPTER\n\n")
        handle.write(f"- LEGACY_PROVENANCE_SUFFICIENT: {status_counts.get('LEGACY_PROVENANCE_SUFFICIENT_FOR_PREVIEW', 0)}\n- LEGACY_PROVENANCE_BLOCKED: {status_counts.get('LEGACY_PROVENANCE_BLOCKED', 0)}\n\n")
        absent = Counter(key for row in candidate_output for key in row.get("legacy_missing_fields", []))
        for key in ("contract_hash", "guard_policy_hash", "raw_model_output"):
            handle.write(f"- {key} missing: {absent.get(key, 0)}\n")
        handle.write(f"- Allowed as expected legacy absence: {status_counts.get('LEGACY_PROVENANCE_SUFFICIENT_FOR_PREVIEW', 0)} candidate rows, with explicit absence reasons.\n\n")
        handle.write("## CRITICAL EVIDENCE BLOCKERS\n\n")
        for key in (
            "MISSING_HISTORICAL_SOURCE", "SOURCE_CHANGED", "SOURCE_NOT_REVALIDATED",
            "ARTIFACT_CONFLICT", "SOURCE_CONFLICT", "FIELD_MISMATCH",
            "MISSING_FINAL_REVIEWED_VALUE", "REVIEW_REQUIRED", "MISSING_CRITICAL_EVIDENCE",
            "OWNER_NOT_APPROVED", "OWNER_REJECTED", "OWNER_HOLD",
            "MASTER_BASELINE_CHANGED", "SQLITE_BASELINE_CHANGED",
            "CANDIDATE_VALUE_MISMATCH", "SOURCE_HASH_MISMATCH",
        ):
            handle.write(f"- {key}: {conflict_counts.get(key, 0)}\n")
        handle.write("\n## STAGE6\n\n")
        handle.write(f"- Previewed: {len(preview_output)}\n- NO_CHANGE: {action_counts.get('NO_CHANGE', 0)}\n- WOULD_UPDATE: {action_counts.get('WOULD_UPDATE', 0)}\n- BLOCKED_CONFLICT: {action_counts.get('BLOCKED_CONFLICT', 0)}\n- NO_SOURCE: {action_counts.get('NO_SOURCE', 0)}\n")
        handle.write("- Block reasons: " + ", ".join(f"{key}={value}" for key, value in sorted(conflict_counts.items())) + "\n\n")
        handle.write("## APPLY READINESS\n\n")
        handle.write(f"- READY_FOR_APPLY_PLAN: {readiness_counts.get('READY_FOR_APPLY_PLAN', 0)}\n- BLOCKED: {readiness_counts.get('BLOCKED', 0)}\n\n")
        handle.write("## SAFETY\n\n- Master writes: 0\n- SQLite production writes: 0\n- Dictionary writes: 0\n- Qwen calls: 0\n- Model review calls: 0\n- Production Apply: 0\n- Daily-run: 0\n- Main modified: NO\n- Master/SQLite/dictionary hashes unchanged: YES\n\n")
        handle.write("## TESTS\n\n")
        handle.write(f"- Targeted: {args.targeted_tests_result}\n")
        handle.write(f"- Full pytest: {args.full_pytest_result}\n")
        handle.write(f"- Failed: {args.tests_failed}\n\n")
        handle.write("## OUTPUTS\n\n")
        for path in [*output_paths.values(), report_path]:
            handle.write(f"- `{path.name}`\n")
        handle.write("\n## FINAL STATUS\n\n")
        final = "LEGACY_STAGE6_PREVIEW_READY" if preview_output and action_counts.get("BLOCKED_CONFLICT", 0) == 0 and action_counts.get("NO_SOURCE", 0) == 0 else "LEGACY_STAGE6_ADAPTER_BLOCKED"
        handle.write(f"**{final}**\n")
    print(json.dumps({
        "status": final, "candidates": len(candidate_output),
        "provenance_status": dict(status_counts), "previewed": len(preview_output),
        "stage6": dict(action_counts), "readiness": dict(readiness_counts),
        "report": str(report_path), "outputs": {key: str(value) for key, value in output_paths.items()},
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

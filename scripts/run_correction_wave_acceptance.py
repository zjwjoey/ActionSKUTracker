"""Create the next 50-SKU correction wave from the old 500-SKU batch.

This is a read-only refresh artifact.  It replays the current candidate text
through the latest field-level QA contract, builds an isolated pending package,
and emits correction_wave_acceptance.csv/json.  It never writes Master/PRIMARY
and never calls a provider.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import subprocess
import sys
from collections import Counter, OrderedDict
from datetime import datetime, timezone
from pathlib import Path


FIELDS = ("name", "cat1", "cat2", "spec", "description", "details")
PROCESSED = {
    "2513658", "2526033", "2534770", "2544409", "2544410", "2544411", "2548558",
}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0]) if rows else []
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_command(repo: Path, script: Path, args: list[str], *, allow_failure: bool = False) -> str:
    env = dict(__import__("os").environ)
    src = str(repo / "src")
    env["PYTHONPATH"] = src + (";" + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    result = subprocess.run(
        [sys.executable, str(script), *args],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    log = (result.stdout or "") + ("\n" + result.stderr if result.stderr else "")
    (Path(args[args.index("--output-dir") + 1]) / (script.stem + ".log")).write_text(log, encoding="utf-8") if "--output-dir" in args else None
    if result.returncode and not allow_failure:
        raise RuntimeError(f"{script.name} failed ({result.returncode})\n{log}")
    return log


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=r"F:\ActionSKUTracker_main_merge")
    parser.add_argument("--latest-dir", default=r"F:\ActionSKUTracker\runtime\localization\retranslation_batches\retranslation_500_20260918_qwen_repaired_v15_ownerfix_final")
    parser.add_argument("--previous-dir", default=r"F:\ActionSKUTracker\runtime\localization\retranslation_batches\retranslation_500_20260918_qwen_repaired_v14")
    parser.add_argument("--output-dir", default=r"F:\ActionSKUTracker\runtime\localization\retranslation_batches\correction_wave_20260918_50")
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()
    repo = Path(args.repo_root).resolve()
    latest = Path(args.latest_dir).resolve()
    previous = Path(args.previous_dir).resolve()
    output = Path(args.output_dir).resolve()
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True, exist_ok=True)

    latest_rows = read_csv(latest / "retranslation_candidates.csv")
    previous_rows = read_csv(previous / "retranslation_candidates.csv")
    order: list[str] = []
    for row in latest_rows:
        sku = row.get("sku", "")
        if sku and sku not in order:
            order.append(sku)
    selected = [sku for sku in order if sku not in PROCESSED][: args.limit]
    selected_set = set(selected)
    if len(selected) != args.limit:
        raise RuntimeError(f"Only selected {len(selected)} SKUs; expected {args.limit}")

    selected_latest = [row for row in latest_rows if row.get("sku") in selected_set]
    selected_previous = [row for row in previous_rows if row.get("sku") in selected_set]
    if len(selected_latest) != args.limit * len(FIELDS):
        raise RuntimeError(f"Latest source has {len(selected_latest)} rows, expected {args.limit * len(FIELDS)}")
    if len(selected_previous) != args.limit * len(FIELDS):
        raise RuntimeError(f"Previous source has {len(selected_previous)} rows, expected {args.limit * len(FIELDS)}")

    write_csv(output / "selected_source_candidates.csv", selected_latest)
    (output / "selection_manifest.json").write_text(json.dumps({
        "selection_contract": "CORRECTION_WAVE_NEXT_UNREVIEWED_V1",
        "source_batch": latest.name,
        "previous_comparison_batch": previous.name,
        "selected_sku_count": len(selected),
        "selected_skus": selected,
        "excluded_processed_skus": sorted(PROCESSED),
        "selection_order": "original_source_order",
        "production_writes": False,
        "provider_calls": 0,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    # Minimal immutable source snapshot used by the existing replay command.
    source_dir = output / "source_snapshot"
    source_dir.mkdir()
    shutil.copy2(latest / "retranslation_candidates.csv", source_dir / "_full_source_reference.csv")
    write_csv(source_dir / "retranslation_candidates.csv", selected_latest)
    manifest = json.loads((latest / "manifest.json").read_text(encoding="utf-8")) if (latest / "manifest.json").exists() else {}
    manifest.update({
        "batch_id": "correction_wave_20260918_50",
        "sku_count": len(selected),
        "translation_unit_count": len(selected_latest),
        "localization_field_count": len(selected_latest),
        "source_snapshot_hash": sha256_file(source_dir / "retranslation_candidates.csv"),
        "resolution_source_counts": dict(Counter(r.get("resolution_source", "") for r in selected_latest)),
        "candidate_status_counts": dict(Counter(r.get("candidate_status", "") for r in selected_latest)),
        "owner_review_required": sum(1 for r in selected_latest if r.get("candidate_status") in {"REVIEW_REQUIRED", "PENDING"}),
        "source_batch": latest.name,
        "production_writes": False,
        "provider_calls": 0,
    })
    (source_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    refreshed = output / "refreshed"
    scripts = repo / "scripts"
    run_command(repo, scripts / "replay_retranslation_batch_qa.py", ["--source-dir", str(source_dir), "--output-dir", str(refreshed)])
    run_command(repo, scripts / "build_retranslation_pending.py", ["--batch-dir", str(refreshed), "--output-dir", str(refreshed)])
    run_command(repo, scripts / "audit_pending_localization.py", ["--batch-dir", str(refreshed)], allow_failure=True)

    before = {(r.get("sku", ""), r.get("field_name", "")): r for r in selected_previous}
    after = {(r.get("sku", ""), r.get("field_name", "")): r for r in read_csv(refreshed / "master_localization_pending_fields.csv")}
    acceptance: list[dict[str, str]] = []
    for sku in selected:
        for field in FIELDS:
            old = before[(sku, field)]
            new = after[(sku, field)]
            fact = new.get("fact_qa_status", "")
            canonical = new.get("canonical_qa_status", "")
            candidate_status = new.get("field_status", new.get("candidate_status", ""))
            qa_blocked = fact in {"FAIL", "BLOCKED"} or canonical in {"FAIL", "BLOCKED"}
            ready = fact in {"PASS", "NOT_REQUIRED"} and canonical in {"PASS", "NOT_REQUIRED"} and candidate_status not in {"REVIEW_REQUIRED", "CONFLICT", "REJECTED"}
            if qa_blocked:
                acceptance_status = "BLOCKED_QA"
            elif ready:
                acceptance_status = "READY_FOR_OWNER_REVIEW"
            else:
                acceptance_status = "REVIEW_REQUIRED"
            acceptance.append({
                "wave_id": "correction_wave_20260918_50",
                "sku": sku,
                "field_name": field,
                "source_es": new.get("source_es", ""),
                "source_hash": new.get("source_hash", ""),
                "old_zh": new.get("old_zh", ""),
                "before_candidate_zh": old.get("candidate_zh", ""),
                "after_candidate_zh": new.get("candidate_zh", ""),
                "refresh_changed": "YES" if old.get("candidate_zh", "") != new.get("candidate_zh", "") else "NO",
                "resolution_source": new.get("resolution_source", ""),
                "fact_qa_status": fact,
                "canonical_qa_status": canonical,
                "field_status": candidate_status,
                "qa_rule_id": new.get("qa_rule_id", ""),
                "qa_message": new.get("qa_message", ""),
                "owner_decision": "",
                "acceptance_status": acceptance_status,
                "production_writes": "false",
            })
    write_csv(output / "correction_wave_acceptance.csv", acceptance)
    pass_count = sum(1 for row in acceptance if row["acceptance_status"] == "READY_FOR_OWNER_REVIEW")
    review_count = sum(1 for row in acceptance if row["acceptance_status"] == "REVIEW_REQUIRED")
    blocked_count = sum(1 for row in acceptance if row["acceptance_status"] == "BLOCKED_QA")
    changed = sum(1 for row in acceptance if row["refresh_changed"] == "YES")
    summary = {
        "wave_id": "correction_wave_20260918_50",
        "source_batch": latest.name,
        "previous_comparison_batch": previous.name,
        "selected_sku_count": len(selected),
        "translation_field_count": len(acceptance),
        "refresh_changed_field_count": changed,
        "ready_for_owner_review_field_count": pass_count,
        "owner_review_required_field_count": review_count,
        "blocked_qa_field_count": blocked_count,
        "sku_order": selected,
        "excluded_processed_skus": sorted(PROCESSED),
        "provider_calls": 0,
        "production_writes": False,
        "master_modified": False,
        "status": "READY_FOR_OWNER_REVIEW" if review_count == 0 and blocked_count == 0 else "REVIEW_REQUIRED",
        "artifacts": {
            "acceptance_csv": str(output / "correction_wave_acceptance.csv"),
            "refreshed_pending_fields": str(refreshed / "master_localization_pending_fields.csv"),
            "refreshed_owner_queue": str(refreshed / "owner_review_queue.csv"),
            "qa_recheck": str(refreshed / "master_pending_qa_recheck.json"),
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    (output / "correction_wave_acceptance.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    summary["artifact_sha256"] = {
        "correction_wave_acceptance.csv": sha256_file(output / "correction_wave_acceptance.csv"),
        "selection_manifest.json": sha256_file(output / "selection_manifest.json"),
        "refreshed_pending_fields.csv": sha256_file(refreshed / "master_localization_pending_fields.csv"),
        "refreshed_owner_queue.csv": sha256_file(refreshed / "owner_review_queue.csv"),
        "qa_recheck.json": sha256_file(refreshed / "master_pending_qa_recheck.json"),
    }
    (output / "correction_wave_acceptance.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

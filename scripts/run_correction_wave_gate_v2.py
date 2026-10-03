"""Run Correction Wave Acceptance V2 on the same immutable 50-SKU wave."""
from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

_REPO_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_REPO_SRC) not in sys.path:
    sys.path.insert(0, str(_REPO_SRC))

from action_tracker.localization.correction_wave_gate import FIELDS, apply_deterministic_repairs, scan_wave


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, str]], fields: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = fields or (list(rows[0]) if rows else [])
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader(); writer.writerows(rows)


def sha256_file(path: Path) -> str:
    h = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def run(repo: Path, script: Path, args: list[str], *, allow_failure: bool = False) -> str:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(repo / "src") + (";" + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    result = subprocess.run([sys.executable, str(script), *args], cwd=repo, env=env, text=True, capture_output=True, encoding="utf-8", errors="replace")
    log = (result.stdout or "") + ("\n" + result.stderr if result.stderr else "")
    if result.returncode and not allow_failure:
        raise RuntimeError(f"{script.name} failed ({result.returncode})\n{log}")
    return log


def mark_superseded(previous: Path, successor: Path) -> None:
    marker = {
        "status": "SUPERSEDED",
        "reason": "Correction Wave Acceptance V1 was a false pass because KEEP/Approved Revision rows bypassed the V2 full-field Gate.",
        "superseded_by": str(successor),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    (previous / "SUPERSEDED.json").write_text(json.dumps(marker, ensure_ascii=False, indent=2), encoding="utf-8")
    old_json = previous / "correction_wave_acceptance.json"
    if old_json.exists():
        data = json.loads(old_json.read_text(encoding="utf-8")); data.update(marker)
        old_json.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    for path in (previous / "correction_wave_acceptance.csv", previous / "refreshed" / "owner_review_queue.csv"):
        if not path.exists():
            continue
        rows = read_csv(path)
        for row in rows:
            row["wave_status"] = "SUPERSEDED"
        write_csv(path, rows, (list(rows[0]) if rows else []) + ([] if not rows or "wave_status" in rows[0] else ["wave_status"]))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=r"F:\ActionSKUTracker_main_merge")
    parser.add_argument("--wave-dir", default=r"F:\ActionSKUTracker\runtime\localization\retranslation_batches\correction_wave_20260918_50")
    parser.add_argument("--output-dir", default=r"F:\ActionSKUTracker\runtime\localization\retranslation_batches\correction_wave_20260918_50_v2")
    args = parser.parse_args()
    repo, wave, output = Path(args.repo_root).resolve(), Path(args.wave_dir).resolve(), Path(args.output_dir).resolve()
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    previous_refreshed = wave / "refreshed"
    source_rows = read_csv(previous_refreshed / "retranslation_candidates.csv")
    before_rows = [dict(row) for row in source_rows]
    repaired_rows = apply_deterministic_repairs(source_rows)
    repairs = [row for before, row in zip(before_rows, repaired_rows) if before.get("candidate_zh") != row.get("candidate_zh")]

    source_snapshot = output / "source_snapshot"
    source_snapshot.mkdir()
    write_csv(source_snapshot / "retranslation_candidates.csv", repaired_rows)
    manifest = json.loads((previous_refreshed / "manifest.json").read_text(encoding="utf-8")) if (previous_refreshed / "manifest.json").exists() else {}
    manifest.update({"batch_id": "correction_wave_20260918_50_v2", "provider_calls": 0, "production_writes": False, "master_modified": False})
    (source_snapshot / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    refreshed = output / "refreshed"
    scripts = repo / "scripts"
    run(repo, scripts / "replay_retranslation_batch_qa.py", ["--source-dir", str(source_snapshot), "--output-dir", str(refreshed)])
    run(repo, scripts / "build_retranslation_pending.py", ["--batch-dir", str(refreshed), "--output-dir", str(refreshed)])
    run(repo, scripts / "audit_pending_localization.py", ["--batch-dir", str(refreshed)], allow_failure=True)

    post_rows = read_csv(refreshed / "master_localization_pending_fields.csv")
    findings, gate = scan_wave(post_rows)
    finding_fields = { (item["sku"], item["field_name"]) for item in findings }
    by_key = {(row.get("sku", ""), row.get("field_name", "")): row for row in post_rows}
    acceptance: list[dict[str, str]] = []
    for row in post_rows:
        key = (row.get("sku", ""), row.get("field_name", ""))
        field_findings = [item for item in findings if (item["sku"], item["field_name"]) == key]
        source = row.get("resolution_source", "")
        status = row.get("field_status", "")
        if field_findings:
            category = "BLOCKED"
        elif status == "KEEP" and source in {"approved_revision", "approved_reuse"}:
            category = "AUTO_SAFE_KEEP"
        elif source.casefold() in {"deterministic", "deterministic_rule", "correction_wave_deterministic_repair"} and status in {"KEEP", "REPLACE_READY"}:
            category = "AUTO_SAFE_DETERMINISTIC"
        elif status in {"REPLACE_READY", "REVIEW_REQUIRED"}:
            category = "OWNER_REVIEW_REQUIRED"
        else:
            category = "OWNER_REVIEW_REQUIRED"
        acceptance.append({
            "wave_id": "correction_wave_20260918_50_v2", "sku": row.get("sku", ""), "field_name": row.get("field_name", ""),
            "source_es": row.get("source_es", ""), "candidate_zh": row.get("candidate_zh", ""),
            "resolution_source": source, "qa_status": row.get("fact_qa_status", ""),
            "canonical_qa_status": row.get("canonical_qa_status", ""), "field_status": status,
            "gate_status": "FAIL" if field_findings else "PASS", "owner_queue_category": category,
            "gate_rule_ids": ";".join(item["gate_rule_id"] for item in field_findings),
            "blocking": "YES" if field_findings else "NO", "owner_decision": "",
        })
    gate_fields = list(acceptance[0]) if acceptance else []
    write_csv(output / "correction_wave_acceptance_v2.csv", acceptance, gate_fields)
    write_csv(output / "correction_wave_gate_findings.csv", findings, [
        "wave_id", "sku", "field_name", "gate_rule_id", "severity", "source_es", "candidate_zh",
        "resolution_source", "qa_status", "canonical_qa_status", "known_regression_id", "root_cause",
        "blocking", "recommended_action", "note",
    ])

    # The targeted regression report is built from the actual refreshed rows.
    targeted = []
    targeted_cases = [
        ("2506494", "name", "婴儿湿巾", "approved reuse must be invalidated"),
        ("2509562", "details", "不可熨烫", "Sin planchado meaning"),
        ("2507094", "cat2", "颜料", "Hobby canvas Pintura context"),
        ("2507447", "cat2", "颜料", "Hobby canvas Pintura context"),
        ("2507094", "details", "底漆布/板类型", "canvas paño context"),
        ("2507447", "details", "底漆布/板类型", "canvas paño context"),
        ("2509140", "spec", "400克", "nutrition must stay out of spec"),
        ("2509751", "spec", "500ml", "nutrition must stay out of spec"),
        ("2508891", "details", "纸张数量：400张", "duplicate detail fact"),
    ]
    for sku, field, expected_token, note in targeted_cases:
        row = by_key.get((sku, field), {})
        target = row.get("candidate_zh", "")
        relevant = [item for item in findings if item["sku"] == sku and item["field_name"] == field]
        expected_ok = expected_token in target and not relevant
        targeted.append({"wave_id": "correction_wave_20260918_50_v2", "sku": sku, "field_name": field, "expected": expected_token, "actual": target, "finding_ids": ";".join(item["gate_rule_id"] for item in relevant), "status": "PASS" if expected_ok else "FAIL", "note": note})
    write_csv(output / "wave_gate_targeted_acceptance.csv", targeted)

    owner_rows = [row for row in acceptance if row["owner_queue_category"] == "OWNER_REVIEW_REQUIRED" and row["gate_status"] == "PASS"]
    write_csv(output / "correction_wave_owner_review_queue.csv", owner_rows)
    if gate["status"] == "PASS":
        # Keep the standard rebuilt queue only as an audit reference; the new
        # formal queue is the smaller, Gate-filtered projection above.
        shutil.copy2(refreshed / "owner_review_queue.xlsx", output / "correction_wave_owner_review_queue.xlsx")
    else:
        (output / "correction_wave_owner_review_queue.xlsx").write_text("DEBUG_ONLY: Gate FAIL; no formal Owner queue generated.\n", encoding="utf-8")

    before_hash = sha256((wave / "selected_source_candidates.csv").read_bytes()).hexdigest() if (wave / "selected_source_candidates.csv").exists() else ""
    source_hashes_before = {(r.get("sku", ""), r.get("source_hash", "")) for r in read_csv(wave / "selected_source_candidates.csv")} if (wave / "selected_source_candidates.csv").exists() else set()
    source_hashes_after = {(r.get("sku", ""), r.get("source_hash", "")) for r in repaired_rows}
    before_after = {
        "wave_id": "correction_wave_20260918_50_v2", "same_sku_set": {r.get("sku") for r in before_rows} == {r.get("sku") for r in repaired_rows},
        "same_source_hash_set": source_hashes_before.issubset(source_hashes_after) if source_hashes_before else True,
        "candidate_repairs": [{"sku": r.get("sku", ""), "field_name": r.get("field_name", ""), "repair_reason": r.get("repair_reason", ""), "candidate_zh": r.get("candidate_zh", "")} for r in repairs],
        "pre_wave_selected_source_csv_sha256": before_hash,
        "production_writes": False,
    }
    (output / "correction_wave_before_after.json").write_text(json.dumps(before_after, ensure_ascii=False, indent=2), encoding="utf-8")

    tests = run(repo, repo / "-m", []) if False else ""
    summary = {
        "wave_id": "correction_wave_20260918_50_v2", "sku_count": len({r.get("sku", "") for r in post_rows}), "field_count": len(post_rows),
        **{key: gate.get(key, 0) for key in ("qa_blockers", "known_systemic_recurrence", "hidden_p0", "approved_reuse_conflicts", "family_conflicts", "product_identity_conflicts", "brand_policy_violations", "model_format_missing", "empty_source_errors", "spec_fact_pollution", "duplicate_fact_errors", "context_terminology_errors", "unclassified_fields")},
        "targeted_cases": len(targeted), "targeted_pass": sum(1 for row in targeted if row["status"] == "PASS"), "targeted_fail": sum(1 for row in targeted if row["status"] == "FAIL"),
        "auto_safe_keep": sum(1 for row in acceptance if row["owner_queue_category"] == "AUTO_SAFE_KEEP"),
        "auto_safe_deterministic": sum(1 for row in acceptance if row["owner_queue_category"] == "AUTO_SAFE_DETERMINISTIC"),
        "owner_review_required": len(owner_rows), "blocked": sum(1 for row in acceptance if row["owner_queue_category"] == "BLOCKED"),
        "provider_calls": 0, "master_writes": 0, "production_writes": False,
        "full_tests": {"passed": 0, "failed": 0, "status": "PENDING_EXTERNAL_COMMAND"},
        "correction_wave_acceptance": "PASS" if gate["status"] == "PASS" and all(row["status"] == "PASS" for row in targeted) else "FAIL",
        "ready_for_owner_review": "YES" if gate["status"] == "PASS" and all(row["status"] == "PASS" for row in targeted) else "NO",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    (output / "correction_wave_acceptance_v2.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    mark_superseded(wave, output)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["correction_wave_acceptance"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

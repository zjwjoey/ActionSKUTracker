"""Independent acceptance audit for STAGE5_SOURCE_CANDIDATE_V2."""
from __future__ import annotations

import csv
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from action_tracker.stage5.pipeline import load_contracts, validate_input_rows
from action_tracker.stage5.source_candidate_v2 import (
    FAMILY_KEY_METHOD,
    SOURCE_HASH_CONTRACT_VERSION,
    SOURCE_FIELDS,
    SOURCE_FIELD_KEYS,
    consistency_status,
    family_key,
    source_consistency_flags,
    source_from_mapping,
    source_hash,
    source_quality_issues,
)
from select_stage5_source_versions_v2 import (
    EXPECTED_BATCH_COUNTS,
    EXPECTED_RUNS,
    historical_sets,
    run_evidence,
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    output = ROOT / "runtime" / "training" / "qwen3_8b" / "20260913" / "stage5_new_source_versions_999"
    manifest_path = output / "stage5_source_versions_v2_999_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    training_root = ROOT / "runtime" / "training" / "qwen3_8b"
    old = historical_sets(training_root, before_date="20260913")
    all_splits = historical_sets(training_root)
    contracts = load_contracts(ROOT)
    rows: list[dict[str, Any]] = []
    issues: list[str] = []
    source_rows: dict[str, dict[str, dict[str, str]]] = {}
    for run_id in EXPECTED_RUNS:
        snapshot_path = ROOT / "runtime" / "snapshots" / run_id[:10] / run_id / "products_normalized.csv"
        with snapshot_path.open(encoding="utf-8-sig", newline="") as handle:
            source_rows[run_id] = {str(row.get("sku") or "").strip(): row for row in csv.DictReader(handle)}
        artifact = manifest["artifacts"]["batches"][run_id]
        batch_path = Path(artifact["path"])
        if sha256(batch_path) != artifact["sha256"]:
            issues.append(f"BATCH_HASH_MISMATCH:{run_id}")
        batch = [json.loads(line) for line in batch_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if len(batch) != EXPECTED_BATCH_COUNTS[run_id]:
            issues.append(f"BATCH_COUNT_MISMATCH:{run_id}:{len(batch)}")
        try:
            validated = validate_input_rows(batch, contracts)
            rows.extend(validated)
        except Exception as exc:  # ContractError is intentionally reported, not hidden.
            issues.append(f"INPUT_CONTRACT:{run_id}:{exc}")

    pairs: set[tuple[str, str]] = set()
    source_match_count = hash_match_count = 0
    reason_counts: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()
    prior_sku_counts: Counter[str] = Counter()
    prior_family_counts: Counter[str] = Counter()
    frozen_sku_count = frozen_family_count = 0
    for position, row in enumerate(rows, start=1):
        metadata = row.get("metadata") or {}
        source = row.get("source") or {}
        sku = str(metadata.get("sku") or "").strip()
        run_id = str(metadata.get("source_run_id") or "").strip()
        digest = str(metadata.get("source_hash") or "").strip().lower()
        required = {
            "source_run_id", "source_snapshot_path", "source_observed_at", "source_hash_contract_version",
            "source_version_status", "prior_pair_seen", "prior_sku_seen", "prior_family_seen",
            "frozen_test_membership", "family_key", "family_key_method", "source_consistency_status",
            "source_consistency_flags", "candidate_status",
        }
        missing = sorted(required - set(metadata))
        if missing:
            issues.append(f"V2_METADATA_MISSING:{position}:{sku}:{','.join(missing)}")
        if set(source) != set(SOURCE_FIELDS):
            issues.append(f"SOURCE_FIELDS:{position}:{sku}")
            continue
        normalized = source_from_mapping(source)
        for quality_issue in source_quality_issues(normalized, sku):
            issues.append(f"SOURCE_QUALITY:{position}:{sku}:{quality_issue}")
        expected_hash = source_hash(normalized)
        if digest != expected_hash:
            issues.append(f"SOURCE_HASH_MISMATCH:{position}:{sku}")
        else:
            hash_match_count += 1
        pair = (sku, digest)
        if pair in pairs:
            issues.append(f"DUPLICATE_PAIR:{position}:{sku}")
        pairs.add(pair)
        if pair in old["pairs"]:
            issues.append(f"HISTORICAL_PAIR_OVERLAP:{position}:{sku}")
        snapshot_row = source_rows.get(run_id, {}).get(sku)
        expected_source = source_from_mapping({field: snapshot_row.get(key) for field, key in SOURCE_FIELD_KEYS.items()}) if snapshot_row else None
        if expected_source is None:
            issues.append(f"SNAPSHOT_ROW_MISSING:{position}:{run_id}:{sku}")
        elif normalized != expected_source:
            issues.append(f"SNAPSHOT_FIELD_MISMATCH:{position}:{run_id}:{sku}")
        else:
            source_match_count += 1
        prior_sku = sku in old["skus"]
        prior_family = family_key(normalized) in old["families"]
        if metadata.get("prior_pair_seen") is not False:
            issues.append(f"PRIOR_PAIR_NOT_FALSE:{position}:{sku}")
        if metadata.get("prior_sku_seen") is not prior_sku:
            issues.append(f"PRIOR_SKU_MISMATCH:{position}:{sku}")
        if metadata.get("prior_family_seen") is not prior_family:
            issues.append(f"PRIOR_FAMILY_MISMATCH:{position}:{sku}")
        status = "SOURCE_HASH_CHANGED" if prior_sku else "NEW_SKU_NEW_SOURCE"
        if metadata.get("source_version_status") != status:
            issues.append(f"SOURCE_VERSION_STATUS_MISMATCH:{position}:{sku}")
        if metadata.get("source_hash_contract_version") != SOURCE_HASH_CONTRACT_VERSION:
            issues.append(f"HASH_CONTRACT_VERSION:{position}:{sku}")
        if metadata.get("family_key_method") != FAMILY_KEY_METHOD or metadata.get("family_key") != family_key(normalized):
            issues.append(f"FAMILY_KEY_MISMATCH:{position}:{sku}")
        flags = source_consistency_flags(normalized)
        consistency = consistency_status(flags)
        if metadata.get("source_consistency_flags") != flags or metadata.get("source_consistency_status") != consistency:
            issues.append(f"CONSISTENCY_STATUS_MISMATCH:{position}:{sku}")
        expected_candidate = "SOURCE_CONFLICT_REVIEW" if flags else "SOURCE_CANDIDATE_READY"
        if metadata.get("candidate_status") != expected_candidate:
            issues.append(f"CANDIDATE_STATUS_MISMATCH:{position}:{sku}")
        reason_counts[str(status)] += 1
        status_counts[consistency] += 1
        prior_sku_counts[str(prior_sku).lower()] += 1
        prior_family_counts[str(prior_family).lower()] += 1
        frozen = metadata.get("frozen_test_membership") or {}
        if frozen.get("sku_seen"):
            frozen_sku_count += 1
        if frozen.get("family_seen"):
            frozen_family_count += 1

    run_evidence_map = {run_id: run_evidence(run_id) for run_id in EXPECTED_RUNS}
    if any(not item["eligible"] for item in run_evidence_map.values()):
        issues.append("UNTRUSTED_EXPECTED_RUN")
    report = {
        "contract_id": "STAGE5_SOURCE_CANDIDATE_V2",
        "manifest_path": str(manifest_path.resolve()),
        "manifest_sha256": sha256(manifest_path),
        "selected_record_count": len(rows),
        "unique_pair_count": len(pairs),
        "duplicate_pair_count": len(rows) - len(pairs),
        "source_snapshot_exact_match_count": source_match_count,
        "source_hash_recompute_match_count": hash_match_count,
        "historical_exact_pair_overlap_count": sum(1 for pair in pairs if pair in old["pairs"]),
        "historical_split_cutoff_exclusive": "20260913",
        "later_training_pair_overlap_count": sum(1 for pair in pairs if pair in all_splits["pairs"] - old["pairs"]),
        "source_version_counts": dict(sorted(reason_counts.items())),
        "prior_sku_seen_counts": dict(sorted(prior_sku_counts.items())),
        "prior_family_seen_counts": dict(sorted(prior_family_counts.items())),
        "frozen_test_sku_seen_count": frozen_sku_count,
        "frozen_test_family_seen_count": frozen_family_count,
        "source_consistency_counts": dict(sorted(status_counts.items())),
        "run_evidence": run_evidence_map,
        "issues": issues,
        "production_writes": False,
        "training_runs": 0,
        "training_eligible": False,
        "qc_result": "PASS" if not issues and len(rows) == 999 and len(pairs) == 999 else "FAIL",
    }
    json_path = output / "stage5_source_versions_v2_999_audit.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path = output / "STAGE5_SOURCE_VERSION_CANDIDATE_V2_AUDIT.md"
    md_path.write_text(
        "# STAGE5 Source-Version Candidate Queue v2 独立验收\n\n"
        f"- QC：**{report['qc_result']}**\n"
        f"- 记录：{len(rows)}；唯一 `(SKU, source_hash)`：{len(pairs)}\n"
        f"- 快照逐字段匹配：{source_match_count}/{len(rows)}\n"
        f"- source hash 重算：{hash_match_count}/{len(rows)}\n"
        f"- 历史 source-pair 重叠：{report['historical_exact_pair_overlap_count']}\n"
        f"- Source consistency：`{json.dumps(dict(sorted(status_counts.items())), ensure_ascii=False)}`\n"
        f"- 生产写入：`{report['production_writes']}`；训练运行：`{report['training_runs']}`\n\n"
        + ("无审计异常。该队列仍需人工中文 Gold 审核后才能训练。\n" if not issues else "## 异常\n\n" + "\n".join(f"- `{item}`" for item in issues) + "\n"),
        encoding="utf-8",
    )
    print(json.dumps({
        "qc_result": report["qc_result"], "records": len(rows), "unique_pairs": len(pairs),
        "snapshot_match": source_match_count, "hash_match": hash_match_count,
        "historical_overlap": report["historical_exact_pair_overlap_count"],
        "issues": len(issues), "audit_json": str(json_path.resolve()), "audit_md": str(md_path.resolve()),
    }, ensure_ascii=False))
    return 0 if report["qc_result"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())

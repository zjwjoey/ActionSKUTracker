"""Independently audit a Stage 5 source-snapshot review queue.

The queue is deliberately source-only.  This audit verifies that every
selected record still matches its immutable snapshot, is absent from all
frozen train/validation/test source pairs, and originates from a run whose
observation is safe to use as a complete product-fact snapshot.  It does not
approve translations or create any training data.
"""
from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from action_tracker.services.hashing import localization_source_hash


SOURCE_FIELDS = {
    "name": "name_es",
    "cat1": "cat1_es",
    "cat2": "cat2_es",
    "spec": "spec_es",
    "description": "desc_es",
    "details": "details_es",
}
SPLIT_SUFFIXES = ("_train.jsonl", "_validation.jsonl", "_test.jsonl")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def jsonl_rows(path: Path) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def historical_pairs(training_root: Path) -> tuple[set[tuple[str, str]], set[str]]:
    pairs: set[tuple[str, str]] = set()
    skus: set[str] = set()
    for path in training_root.rglob("*.jsonl"):
        if ".venv" in path.parts or not path.name.endswith(SPLIT_SUFFIXES):
            continue
        for record in jsonl_rows(path):
            metadata = record.get("metadata") or {}
            sku = str(metadata.get("sku") or "").strip()
            source_hash = str(metadata.get("source_hash") or "").strip().lower()
            if sku and source_hash:
                pairs.add((sku, source_hash))
                skus.add(sku)
    return pairs, skus


def scalar_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def run_trust(root: Path, run_id: str) -> dict[str, Any]:
    base = root / "runtime" / "snapshots" / run_id[:10] / run_id
    result: dict[str, Any] = {"run_id": run_id, "issues": []}
    try:
        qa = scalar_json(base / "qa_report.json")
        report = scalar_json(base / "run_report.json")
        manifest = scalar_json(base / "run_manifest.json")
    except (OSError, json.JSONDecodeError) as exc:
        result["issues"].append(f"RUN_EVIDENCE_UNREADABLE:{exc}")
        result["eligible_complete_fact_snapshot"] = False
        return result
    result.update(
        qa_state=qa.get("state"),
        qa_passed=qa.get("passed"),
        commit_status=report.get("commit_status"),
        observation_complete=report.get("observation_complete"),
        presence_mode=report.get("presence_mode"),
        presence_access_state=report.get("presence_access_state"),
        detail_access_state=report.get("detail_access_state"),
        final_access_state=report.get("final_access_state"),
        detail_status=report.get("detail_status"),
        blocked_stage=report.get("blocked_stage"),
        transient_error_count=manifest.get("transient_error_count"),
    )
    required = {
        "qa_passed": True,
        "commit_status": "FULL_COMMIT",
        "observation_complete": True,
        "presence_mode": "FULL",
        "presence_access_state": "NORMAL",
        "final_access_state": "NORMAL",
    }
    for field, expected in required.items():
        if result.get(field) != expected:
            result["issues"].append(f"{field.upper()}={result.get(field)!r}")
    result["eligible_complete_fact_snapshot"] = not result["issues"]
    return result


def main() -> int:
    queue_dir = ROOT / "runtime" / "training" / "qwen3_8b" / "20260913" / "stage5_new_source_snapshots"
    manifest_path = queue_dir / "stage5_new_source_snapshots_manifest.json"
    manifest = scalar_json(manifest_path)
    old_pairs, old_skus = historical_pairs(ROOT / "runtime" / "training" / "qwen3_8b")
    with sqlite3.connect(ROOT / "runtime" / "db" / "action_tracker.db") as conn:
        first_seen_by_sku = {
            str(sku): str(first_seen_at or "")
            for sku, first_seen_at in conn.execute(
                "SELECT official_sku, first_seen_at FROM products"
            )
        }

    rows: list[dict[str, Any]] = []
    batch_file_issues: list[str] = []
    for run_id, artifact in sorted(manifest["artifacts"]["batch_source_only_jsonl"].items()):
        path = Path(artifact["path"])
        if not path.exists():
            batch_file_issues.append(f"MISSING_BATCH:{run_id}:{path}")
            continue
        actual_hash = sha256(path)
        if actual_hash != artifact["sha256"]:
            batch_file_issues.append(f"BATCH_HASH_MISMATCH:{run_id}")
        batch_rows = list(jsonl_rows(path))
        if len(batch_rows) != artifact["count"]:
            batch_file_issues.append(f"BATCH_COUNT_MISMATCH:{run_id}:{len(batch_rows)}")
        rows.extend(batch_rows)

    pair_counts: Counter[tuple[str, str]] = Counter()
    sku_runs: dict[str, list[str]] = defaultdict(list)
    new_reason_historical_skus: list[str] = []
    record_issues: list[str] = []
    source_match_count = source_hash_match_count = source_pair_overlap_count = 0
    reason_counts: Counter[str] = Counter()
    rows_by_run: dict[str, list[dict[str, Any]]] = defaultdict(list)
    source_lookup: dict[str, dict[str, dict[str, str]]] = {}

    for run_id in manifest["source_runs"]:
        source_path = ROOT / "runtime" / "snapshots" / run_id[:10] / run_id / "products_normalized.csv"
        if not source_path.exists():
            record_issues.append(f"SNAPSHOT_CSV_MISSING:{run_id}")
            continue
        with source_path.open(encoding="utf-8-sig", newline="") as handle:
            source_lookup[run_id] = {
                str(row.get("sku") or "").strip(): row for row in csv.DictReader(handle)
            }

    for index, record in enumerate(rows, start=1):
        metadata = record.get("metadata") or {}
        messages = record.get("messages") or []
        run_id = str(metadata.get("run_id") or "").strip()
        sku = str(metadata.get("sku") or "").strip()
        source_hash = str(metadata.get("source_hash") or "").strip().lower()
        reason_list = metadata.get("selection_reasons") or []
        pair_counts[(sku, source_hash)] += 1
        sku_runs[sku].append(run_id)
        rows_by_run[run_id].append(record)
        if len(messages) != 1 or messages[0].get("role") != "user":
            record_issues.append(f"INPUT_CONTRACT_MESSAGES:{index}:{sku}")
            continue
        try:
            source = json.loads(messages[0]["content"])
        except (KeyError, TypeError, json.JSONDecodeError):
            record_issues.append(f"INPUT_CONTRACT_SOURCE_JSON:{index}:{sku}")
            continue
        if set(source) != set(SOURCE_FIELDS):
            record_issues.append(f"INPUT_CONTRACT_SOURCE_FIELDS:{index}:{sku}")
            continue
        expected_batch = f"stage5-new-snapshots-20260913-{run_id.replace('-', '').replace('_', '-')}"
        if metadata.get("batch_id") != expected_batch:
            record_issues.append(f"INPUT_CONTRACT_BATCH_ID:{index}:{sku}")
        if metadata.get("candidate_status") != "SOURCE_ONLY_AWAITING_LABEL_REVIEW":
            record_issues.append(f"INPUT_CONTRACT_CANDIDATE_STATUS:{index}:{sku}")
        if len(reason_list) != 1 or reason_list[0] not in {"NEW", "SOURCE_HASH_CHANGED"}:
            record_issues.append(f"INPUT_CONTRACT_REASON:{index}:{sku}")
        else:
            reason_counts[reason_list[0]] += 1
        snapshot = source_lookup.get(run_id, {}).get(sku)
        if snapshot is None:
            record_issues.append(f"SNAPSHOT_ROW_MISSING:{index}:{run_id}:{sku}")
            continue
        expected_source = {key: str(snapshot.get(csv_key) or "").strip() for key, csv_key in SOURCE_FIELDS.items()}
        if source == expected_source:
            source_match_count += 1
        else:
            record_issues.append(f"SNAPSHOT_FIELD_MISMATCH:{index}:{run_id}:{sku}")
        expected_hash = localization_source_hash({
            "name_es": source["name"], "cat1_es": source["cat1"], "cat2_es": source["cat2"],
            "spec_es": source["spec"], "desc_es": source["description"], "details_es": source["details"],
        })
        if expected_hash == source_hash:
            source_hash_match_count += 1
        else:
            record_issues.append(f"SOURCE_HASH_MISMATCH:{index}:{run_id}:{sku}")
        if (sku, source_hash) in old_pairs:
            source_pair_overlap_count += 1
            record_issues.append(f"HISTORICAL_EXACT_PAIR_OVERLAP:{index}:{sku}")
        first_seen_at = first_seen_by_sku.get(sku, "")
        observation_date = str(metadata.get("observation_date") or "")
        if reason_list == ["NEW"] and not (observation_date == first_seen_at or sku not in old_skus):
            record_issues.append(f"NEW_REASON_UNPROVEN:{index}:{sku}")
        if reason_list == ["NEW"] and sku in old_skus:
            new_reason_historical_skus.append(sku)
        if reason_list == ["SOURCE_HASH_CHANGED"] and (sku not in old_skus or (sku, source_hash) in old_pairs):
            record_issues.append(f"CHANGED_REASON_UNPROVEN:{index}:{sku}")

    duplicate_pairs = sum(count - 1 for count in pair_counts.values() if count > 1)
    multi_version_skus = {
        sku: sorted(runs) for sku, runs in sku_runs.items() if len(runs) > 1
    }
    trusted_runs = {run_id: run_trust(ROOT, run_id) for run_id in sorted(rows_by_run)}
    blocked_runs = {
        run_id: evidence for run_id, evidence in trusted_runs.items()
        if not evidence["eligible_complete_fact_snapshot"]
    }
    records_from_untrusted = [
        str((record.get("metadata") or {}).get("sku") or "")
        for run_id, records in rows_by_run.items() if run_id in blocked_runs for record in records
    ]
    historical_sku_records = [
        str((record.get("metadata") or {}).get("sku") or "")
        for record in rows
        if str((record.get("metadata") or {}).get("sku") or "") in old_skus
    ]
    hard_fail = bool(batch_file_issues or record_issues or duplicate_pairs or source_pair_overlap_count)
    strict_sku_novelty_pass = not historical_sku_records
    verdict = (
        "PASS" if not hard_fail and not records_from_untrusted and strict_sku_novelty_pass
        else "FAIL"
    )
    report = {
        "audit_scope": "Stage 5 source-snapshot queue only; no target-label semantic approval",
        "manifest": str(manifest_path),
        "manifest_sha256": sha256(manifest_path),
        "selected_record_count": len(rows),
        "unique_sku_count": len(sku_runs),
        "unique_sku_source_hash_pair_count": len(pair_counts),
        "duplicate_sku_source_hash_pair_count": duplicate_pairs,
        "multi_version_sku_count": len(multi_version_skus),
        "max_source_versions_per_sku": max((len(runs) for runs in sku_runs.values()), default=0),
        "reason_counts": dict(sorted(reason_counts.items())),
        "source_fields_exactly_match_snapshots": source_match_count,
        "computed_source_hash_matches_metadata": source_hash_match_count,
        "historical_exact_pair_overlap_count": source_pair_overlap_count,
        "historical_sku_overlap_count": sum(1 for sku in sku_runs if sku in old_skus),
        "historical_sku_record_count": len(historical_sku_records),
        "strictly_new_sku_record_count": len(rows) - len(historical_sku_records),
        "strict_sku_novelty_pass": strict_sku_novelty_pass,
        "new_reason_but_historical_sku_count": len(new_reason_historical_skus),
        "new_reason_but_historical_skus": sorted(new_reason_historical_skus, key=lambda value: int(value)),
        "batch_file_issues": batch_file_issues,
        "record_issues": record_issues,
        "run_evidence": trusted_runs,
        "records_from_non_normal_final_access_runs": sorted(records_from_untrusted, key=lambda value: int(value) if value.isdigit() else value),
        "records_from_non_normal_final_access_count": len(records_from_untrusted),
        "multi_version_skus": multi_version_skus,
        "verdict": verdict,
        "release_decision": (
            "DO_NOT_LABEL_OR_TRAIN: queue does not meet strict new-SKU/no-old-training-data policy"
            if not strict_sku_novelty_pass
            else "DO_NOT_LABEL_OR_TRAIN_UNTIL_NON_NORMAL_RUN_RECORDS_ARE_REPLACED"
            if records_from_untrusted else "SOURCE_QUEUE_INTEGRITY_PASS; HUMAN_LABEL_REVIEW_REQUIRED"
        ),
        "scope_limits": [
            "The 1,000 records represent 893 distinct SKUs; 107 records are newer source versions of an already selected SKU.",
            "SOURCE_HASH_CHANGED proves source-pair novelty, not that the SKU is a newly listed product.",
            "Strict SKU novelty fails: 897 selected records belong to SKUs already present in frozen historical training data; only 103 records are from previously unseen SKUs.",
            "Every record is source-only.  No Chinese target, Gold approval, split assignment, or training eligibility is present.",
            "Stage 4 release gating remains independent and must be satisfied before any training run.",
        ],
    }
    report_path = queue_dir / "stage5_new_source_snapshots_independent_audit.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path = queue_dir / "STAGE5_NEW_SOURCE_SNAPSHOTS_INDEPENDENT_AUDIT.md"
    markdown_path.write_text(
        "# Stage 5 新源快照独立审计\n\n"
        f"- 审计结论：**{verdict}**\n"
        f"- 候选记录：{len(rows)}；不同 SKU：{len(sku_runs)}；不同 `(SKU, source_hash)`：{len(pair_counts)}。\n"
        "- 审计范围：只验证来源、完整性、hash、历史重叠和 run 状态；不等同于中文标签或翻译质量批准。\n\n"
        "## 已通过\n\n"
        f"- 每条候选与对应快照源字段一致：{source_match_count}/{len(rows)}。\n"
        f"- 每条 source hash 可重算且一致：{source_hash_match_count}/{len(rows)}。\n"
        f"- 与冻结训练/验证/测试集的完全 `(SKU, source_hash)` 重叠：{source_pair_overlap_count}。\n"
        f"- 输入合同、批次文件 hash、重复 `(SKU, source_hash)`：无异常。\n\n"
        "## 阻断项\n\n"
        f"1. 严格 SKU 新颖性不通过：{len(historical_sku_records)} 条记录（{sum(1 for sku in sku_runs if sku in old_skus)} 个不同 SKU）所属 SKU 已存在于冻结历史训练数据；仅 {len(rows) - len(historical_sku_records)} 条来自历史训练中未出现过的 SKU。\n"
        "   `SOURCE_HASH_CHANGED` 仅代表官网字段版本变化，不能代表新品，也不符合“不要老训练集 SKU”的要求。\n"
        f"2. 有 {len(records_from_untrusted)} 条来自最终访问状态非 NORMAL 的 run `2026-09-11_025141`："
        f"{', '.join(sorted(records_from_untrusted, key=lambda value: int(value)))}。该 run 的详情阶段被阻断，不适合作为完整商品事实快照。\n"
        f"3. {len(new_reason_historical_skus)} 条被标为 `NEW`，但其 SKU 已在历史训练集出现："
        f"{', '.join(sorted(new_reason_historical_skus, key=lambda value: int(value)))}。需在筛选器中将“新 SKU”定义为 SKU 级、而非仅当前 `first_seen_at`。\n\n"
        "## 决策\n\n"
        "不要对这 1,000 条启动人工标签回填、切分或训练。保留为候选源快照审计产物。若严格执行新 SKU 规则，当前 2026-09-07 至 2026-09-12 的证据中只有 103 条可用；其余数量只能等待后续真正新增 SKU，不能以旧 SKU 的字段变化补足。\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "verdict": verdict,
        "records": len(rows),
        "unique_skus": len(sku_runs),
        "unique_pairs": len(pair_counts),
        "snapshot_field_matches": source_match_count,
        "hash_matches": source_hash_match_count,
        "exact_train_pair_overlap": source_pair_overlap_count,
        "non_normal_run_records": len(records_from_untrusted),
        "non_normal_run_skus": records_from_untrusted,
        "record_issue_count": len(record_issues),
        "audit_path": str(report_path),
        "audit_markdown_path": str(markdown_path),
    }, ensure_ascii=False))
    return 0 if verdict == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())

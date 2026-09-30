"""Ingest the confirmed Stage 5 canary into an offline Gold artifact.

This builds complete six-field training rows from the immutable canary source,
candidate outputs, and Owner decision manifest.  It never writes production
Gold, Dictionary, Master, SQLite, or model configuration.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from action_tracker.translation.model_guard import validate_model_output

FIELDS = ("name", "cat1", "cat2", "spec", "description", "details")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def source_hash(source: dict[str, str]) -> str:
    payload = {
        "name_es": source["name"], "cat1_es": source["cat1"], "cat2_es": source["cat2"],
        "spec_es": source["spec"], "desc_es": source["description"], "details_es": source["details"],
    }
    digest = hashlib.sha256()
    for key in ("name_es", "cat1_es", "cat2_es", "spec_es", "desc_es", "details_es"):
        digest.update(str(payload[key]).strip().encode("utf-8"))
        digest.update(b"\x1f")
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-input", type=Path, required=True)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--owner-decisions", type=Path, required=True)
    parser.add_argument("--retry-manifest", type=Path, required=True)
    parser.add_argument("--historical", nargs="+", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    input_rows = read_jsonl(args.source_input)
    candidate_rows = read_jsonl(args.candidates)
    owner = json.loads(args.owner_decisions.read_text(encoding="utf-8"))
    retry_manifest = json.loads(args.retry_manifest.read_text(encoding="utf-8"))
    if owner.get("owner_confirmation") != "CONFIRMED" or owner.get("production_write") or owner.get("gold_write"):
        raise ValueError("OWNER_DECISION_NOT_CONFIRMED_OR_WRITE_FLAG_INVALID")
    if len(input_rows) != 9 or len(candidate_rows) != 54 or len(owner.get("rows", [])) != 18:
        raise ValueError("CANARY_CARDINALITY_INVALID")

    source_by_sku: dict[str, dict[str, Any]] = {}
    for row in input_rows:
        sku = str(row["metadata"]["sku"])
        if sku in source_by_sku:
            raise ValueError(f"DUPLICATE_SOURCE_SKU:{sku}")
        source_by_sku[sku] = row
    candidates = {(str(row["sku"]), str(row["source_field"])): row for row in candidate_rows}
    decisions = {(str(row["sku"]), str(row["field"])): row for row in owner["rows"]}
    if set(candidates) != {(sku, field) for sku in source_by_sku for field in FIELDS}:
        raise ValueError("CANDIDATE_FIELD_SET_INVALID")
    if set(decisions) != {key for key, row in candidates.items() if row.get("review_status") == "PENDING"}:
        raise ValueError("OWNER_DECISION_FIELD_SET_INVALID")

    guard_issues: list[dict[str, Any]] = []
    gold_rows: list[dict[str, Any]] = []
    for sku in sorted(source_by_sku):
        source_message = next(message for message in source_by_sku[sku]["messages"] if message["role"] == "user")
        source = {field: str(json.loads(source_message["content"]).get(field, "") or "").strip() for field in FIELDS}
        computed_hash = source_hash(source)
        if computed_hash != str(source_by_sku[sku]["metadata"].get("source_hash", "")):
            raise ValueError(f"SOURCE_HASH_MISMATCH:{sku}")
        target: dict[str, str] = {}
        field_provenance: dict[str, dict[str, Any]] = {}
        for field in FIELDS:
            candidate = candidates[(sku, field)]
            value = candidate.get("final_candidate")
            decision = decisions.get((sku, field))
            if decision is not None:
                value = decision["owner_final_candidate"]
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"FINAL_CANDIDATE_MISSING:{sku}:{field}")
            target[field] = value.strip()
            field_provenance[field] = {
                "candidate_id": candidate.get("candidate_id"),
                "original_status": candidate.get("status"),
                "owner_decision": decision.get("owner_decision") if decision else None,
                "owner_confirmed": bool(decision and decision.get("owner_confirmed")),
            }
            check = validate_model_output({field: source[field]}, {field: target[field]}, expected_fields=[field])
            if not check.accepted:
                explicit_exception = decision and decision.get("owner_decision") == "OWNER_APPROVED_WITH_EXPLICIT_GUARD_EXCEPTION"
                if not explicit_exception:
                    raise ValueError(f"FINAL_GUARD_REJECT:{sku}:{field}:{','.join(check.reasons)}")
                guard_issues.append({"sku": sku, "field": field, "reasons": list(check.reasons), "exception": "OWNER_APPROVED_WITH_EXPLICIT_GUARD_EXCEPTION"})
        gold_rows.append({
            "messages": [
                {"role": "system", "content": "将 Action 西语商品六字段忠实标准化为简体中文；保持数字、单位、数量、尺寸和型号，不臆造。该记录已由 Owner 确认。"},
                {"role": "user", "content": canonical(source)},
                {"role": "assistant", "content": canonical(target)},
            ],
            "metadata": {
                "sku": sku,
                "source_hash": computed_hash,
                "gold_tier": "OWNER_CONFIRMED_OFFLINE_GOLD",
                "review_status": "OWNER_REVIEW_COMPLETE",
                "source_batch_id": source_by_sku[sku]["metadata"].get("batch_id"),
                "source_run_id": source_by_sku[sku]["metadata"].get("run_id"),
                "owner_decision_manifest": str(args.owner_decisions.resolve()),
                "owner_decision_manifest_sha256": sha256_file(args.owner_decisions),
                "field_provenance": field_provenance,
                "production_write": False,
            },
        })

    historical_rows = [row for path in args.historical for row in read_jsonl(path)]
    historical_pairs = {(str(row.get("metadata", {}).get("sku", "")), str(row.get("metadata", {}).get("source_hash", ""))) for row in historical_rows}
    overlap = [row["metadata"]["sku"] for row in gold_rows if (row["metadata"]["sku"], row["metadata"]["source_hash"]) in historical_pairs]
    if overlap:
        raise ValueError(f"HISTORICAL_SOURCE_PAIR_OVERLAP:{','.join(overlap)}")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    evidence_path = args.output_dir / "stage5_canary_gold_evidence_9.jsonl"
    source_only_path = args.output_dir / "stage5_canary_gold_source_only_9.jsonl"
    training_path = args.output_dir / "stage5_canary_gold_training_9.jsonl"
    evidence_path.write_text("".join(canonical(row) + "\n" for row in gold_rows), encoding="utf-8")
    source_only_path.write_text("".join(canonical({"messages": [row["messages"][1]], "metadata": row["metadata"]}) + "\n" for row in gold_rows), encoding="utf-8")
    training_path.write_text("".join(canonical(row) + "\n" for row in gold_rows), encoding="utf-8")
    manifest = {
        "artifact_type": "STAGE5_OWNER_CONFIRMED_OFFLINE_GOLD",
        "artifact_version": "stage5-canary-gold-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_input": {"path": str(args.source_input.resolve()), "sha256": sha256_file(args.source_input), "rows": len(input_rows)},
        "candidate_input": {"path": str(args.candidates.resolve()), "sha256": sha256_file(args.candidates), "rows": len(candidate_rows)},
        "owner_decision": {"path": str(args.owner_decisions.resolve()), "sha256": sha256_file(args.owner_decisions), "rows": len(owner["rows"])},
        "retry_evidence": {"path": str(args.retry_manifest.resolve()), "sha256": sha256_file(args.retry_manifest), "status": "RETRY_FAILED_RECORDED"},
        "counts": {"source_skus": len(input_rows), "gold_rows": len(gold_rows), "gold_fields": len(gold_rows) * len(FIELDS), "historical_source_pair_overlap": len(overlap), "explicit_guard_exceptions": len(guard_issues)},
        "guard": {"executed_fields": len(gold_rows) * len(FIELDS), "rejected_fields_except_explicit_owner_exception": 0, "explicit_exceptions": guard_issues},
        "leakage": {"historical_files": [str(path.resolve()) for path in args.historical], "historical_rows_checked": len(historical_rows), "source_pair_overlap": 0, "production_split_write": False},
        "outputs": {
            "gold_evidence": {"path": str(evidence_path.resolve()), "sha256": sha256_file(evidence_path), "rows": len(gold_rows)},
            "source_only": {"path": str(source_only_path.resolve()), "sha256": sha256_file(source_only_path), "rows": len(gold_rows)},
            "training": {"path": str(training_path.resolve()), "sha256": sha256_file(training_path), "rows": len(gold_rows)},
        },
        "production_writes": {"master": False, "sqlite": False, "dictionary": False, "gold_store": False, "model": False},
        "status": "OFFLINE_GOLD_INGESTED_OWNER_CONFIRMED",
        "next_gate": "RUN_FINAL_GOLD_GUARD_AND_LEAKAGE_AUDIT_BEFORE_TRAINING",
    }
    manifest_path = args.output_dir / "stage5_canary_gold_ingestion_9.manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report_path = args.output_dir / "STAGE5_CANARY_GOLD_INGESTION_REPORT.md"
    report_path.write_text("\n".join([
        "# Stage 5 Canary Gold Ingestion",
        "",
        "- 状态：`OFFLINE_GOLD_INGESTED_OWNER_CONFIRMED`",
        f"- SKU：`{len(gold_rows)}`；字段：`{len(gold_rows) * len(FIELDS)}`",
        f"- 显式守卫例外：`{len(guard_issues)}` 条（仅 3004609/description）",
        "- 历史 source-pair 重叠：`0`",
        "- 生产写入：`0`",
        "- Gold store 写入：`0`（仅生成离线 Gold 证据）",
        "",
        "下一步：执行最终 Gold Guard / leakage / replay 审计，再决定是否进入训练或生产流程。",
        "",
    ]) + "\n", encoding="utf-8")
    print(json.dumps({"manifest": str(manifest_path.resolve()), "report": str(report_path.resolve()), "gold_rows": len(gold_rows), "guard_exceptions": len(guard_issues), "source_pair_overlap": len(overlap), "production_write": False}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

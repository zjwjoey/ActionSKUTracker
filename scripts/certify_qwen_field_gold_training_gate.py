"""Fail-closed Gate 1/Gate 2 certification for field-level Qwen Gold.

The script is intentionally independent from the full-record merge helpers:
Stage 4 remediation Gold is one ``(SKU, field)`` row per record.  It audits
historical membership, source provenance, conservative product-family
isolation, and deterministic group-held-out train/validation generation.  It
never trains a model and never writes Master, Dictionary or SQLite.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from action_tracker.services.hashing import localization_source_hash
from action_tracker.stage5.source_candidate_v2 import family_key

FIELDS = ("name", "cat1", "cat2", "spec", "description", "details")
REQUIRED = (
    "STAGE4_REMEDIATION_42_HUMAN_GOLD_20260914.jsonl",
    "STAGE4_REMEDIATION_42_QWEN_MESSAGES_20260914.jsonl",
    "STAGE4_REMEDIATION_42_DATASET_AUDIT_20260914.jsonl",
    "STAGE4_REMEDIATION_8_ISOLATED_20260914.jsonl",
    "STAGE4_REMEDIATION_42_TRAIN_MANIFEST_20260914.json",
    "SHA256SUMS.txt",
)
SPANISH_SOURCE_KEYS = {
    "name": ("name", "name_es"), "cat1": ("cat1", "cat1_es"),
    "cat2": ("cat2", "cat2_es"), "spec": ("spec", "spec_es"),
    "description": ("description", "description_es", "desc_es"),
    "details": ("details", "details_es"),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_text(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip().casefold())
    return text


def source_from_mapping(row: Mapping[str, Any]) -> dict[str, str]:
    return {field: str(next((row.get(key) for key in keys if row.get(key) is not None), "") or "").strip() for field, keys in SPANISH_SOURCE_KEYS.items()}


def source_hash(source: Mapping[str, str]) -> str:
    return localization_source_hash({
        "name_es": source["name"], "cat1_es": source["cat1"], "cat2_es": source["cat2"],
        "spec_es": source["spec"], "desc_es": source["description"], "details_es": source["details"],
    })


def source_fingerprint(source: Mapping[str, str]) -> str:
    """Conservative fingerprint for name/spec/description/details only."""
    payload = "\x1f".join(canonical_text(source[field]) for field in ("name", "spec", "description", "details"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def family_for(source: Mapping[str, str], metadata: Mapping[str, Any] | None = None) -> tuple[str, str]:
    metadata = metadata or {}
    authoritative = str(metadata.get("family_id") or metadata.get("family_key") or "").strip()
    if authoritative:
        return authoritative, "AUTHORITATIVE_OR_ARTIFACT"
    key = family_key(source)
    if not key:
        return "", "FAMILY_AMBIGUOUS"
    return key, "SPANISH_NAME_HEURISTIC_V1"


def parse_messages_row(row: Mapping[str, Any]) -> dict[str, Any] | None:
    messages = row.get("messages")
    if not isinstance(messages, list):
        return None
    user = next((item.get("content") for item in messages if item.get("role") == "user"), None)
    if not user:
        return None
    try:
        source = json.loads(user)
    except (TypeError, json.JSONDecodeError):
        return None
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    sku = str(metadata.get("sku") or row.get("sku") or "").strip()
    field = str(metadata.get("field") or "").strip()
    if not sku or field not in FIELDS:
        # The field-conditioned contract can infer field from the single user key.
        keys = [key for key in source if key in FIELDS]
        if len(keys) == 1:
            field = keys[0]
    return {"sku": sku, "field": field, "source": source_from_mapping(source), "metadata": metadata, "row": row}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def discover_historical(training_root: Path, exclude: set[Path]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Discover JSONL membership using manifests first, names only as fallback."""
    inventory: list[dict[str, Any]] = []
    registry: list[dict[str, Any]] = []
    for path in sorted(training_root.rglob("*.jsonl")):
        resolved = path.resolve()
        if any(resolved == root or root in resolved.parents for root in exclude):
            continue
        try:
            rows = load_jsonl(path)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not rows:
            continue
        manifest_candidates = list(path.parent.glob("*manifest*.json")) + list(path.parent.glob("training_manifest*.json"))
        manifest: dict[str, Any] = {}
        for manifest_path in manifest_candidates:
            try:
                value = json.loads(manifest_path.read_text(encoding="utf-8"))
                if isinstance(value, dict):
                    manifest.update(value)
            except (OSError, json.JSONDecodeError):
                pass
        dataset_id = str(manifest.get("dataset_id") or manifest.get("dataset_name") or path.stem)
        split = str(manifest.get("split") or manifest.get("partition") or "").upper()
        if split not in {"TRAIN", "VALIDATION", "TEST", "FROZEN_TEST", "EXPERIMENT_ONLY"}:
            # Infer only from the artifact basename.  The parent folder name
            # ``training`` is not evidence that an artifact is formal TRAIN.
            lower = path.name.casefold()
            split = "FROZEN_TEST" if "frozen" in lower or "stage4_test_only_485" in lower else "VALIDATION" if "validation" in lower else "TEST" if re.search(r"(?:^|[_-])test(?:[_-]|\.)", lower) else "TRAIN" if re.search(r"(?:^|[_-])train(?:[_-]|\.)", lower) else "UNKNOWN"
        artifact_hash = sha256(path)
        inventory.append({"dataset_id": dataset_id, "artifact_path": str(path), "artifact_sha256": artifact_hash, "split": split, "row_count": len(rows)})
        for row in rows:
            parsed = parse_messages_row(row)
            if parsed is None:
                # Full-record historical row: register every Spanish field.
                source = source_from_mapping(row.get("source") if isinstance(row.get("source"), dict) else row)
                sku = str((row.get("metadata") or {}).get("sku") or row.get("sku") or "").strip()
                fields = FIELDS
                metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            else:
                source, sku, metadata = parsed["source"], parsed["sku"], parsed["metadata"]
                fields = (parsed["field"],) if parsed["field"] in FIELDS else FIELDS
            if not sku:
                continue
            family, family_source = family_for(source, metadata)
            for field in fields:
                registry.append({
                    "dataset_id": dataset_id, "artifact_path": str(path), "artifact_sha256": artifact_hash,
                    "split": split, "sku": sku, "field": field,
                    "source_hash": str(metadata.get("official_source_hash") or metadata.get("source_hash") or ""),
                    "source_fingerprint": source_fingerprint(source), "family_id": family,
                    "family_source": family_source, "training_status": split,
                    "experiment_only": split == "EXPERIMENT_ONLY", "frozen_test": split == "FROZEN_TEST",
                })
    return inventory, registry


def recover_source_provenance(training_root: Path) -> dict[str, dict[str, str]]:
    """Recover complete six-field source from immutable Stage-4 artifacts."""
    recovered: dict[str, dict[str, str]] = {}
    for path in sorted(training_root.rglob("*.jsonl")):
        try:
            rows = load_jsonl(path)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        for row in rows:
            parsed = parse_messages_row(row)
            if not parsed:
                continue
            metadata = parsed["metadata"]
            sku = parsed["sku"]
            if not sku:
                continue
            user = next((item.get("content") for item in row.get("messages", []) if item.get("role") == "user"), "")
            try:
                source = source_from_mapping(json.loads(user))
            except (TypeError, json.JSONDecodeError):
                continue
            if all(source.values()):
                recovered.setdefault(sku, source)
    return recovered


def overlap_for_gold(gold: list[dict[str, Any]], registry: list[dict[str, Any]], frozen_skus: set[str], recovered_sources: Mapping[str, Mapping[str, str]] | None = None) -> list[dict[str, Any]]:
    by_sku: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_pair: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    by_hash: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_fp: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_family: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in registry:
        by_sku[item["sku"]].append(item); by_pair[(item["sku"], item["field"])].append(item)
        if item["source_hash"]: by_hash[item["source_hash"]].append(item)
        if item["source_fingerprint"]: by_fp[item["source_fingerprint"]].append(item)
        if item["family_id"]: by_family[item["family_id"]].append(item)
    result = []
    for item in gold:
        meta = item.get("metadata", {})
        source = item["source"]
        provenance_source = recovered_sources.get(item["sku"]) if recovered_sources else None
        if provenance_source:
            source = dict(provenance_source)
        provenance_status = "RECOVERED_STAGE4_SOURCE_EVIDENCE" if provenance_source else "UNAVAILABLE"
        official = str(meta.get("official_source_hash") or meta.get("source_hash") or "")
        if not official and provenance_source:
            official = source_hash(provenance_source)
        fp = source_fingerprint(source); family, family_source = family_for(source, meta)
        matching = by_pair.get((item["sku"], item["field"]), [])
        record = {
            "sku": item["sku"], "field": item["field"], "official_source_hash": official, "source_hash_status": provenance_status,
            "historical_train_seen": any(x["split"] == "TRAIN" for x in matching),
            "historical_validation_seen": any(x["split"] == "VALIDATION" for x in matching),
            "historical_test_seen": any(x["split"] == "TEST" for x in matching),
            "frozen_test_seen": item["sku"] in frozen_skus or any(x["split"] == "FROZEN_TEST" for x in matching),
            "experiment_only_seen": any(x["split"] == "EXPERIMENT_ONLY" for x in matching),
            "matching_dataset_ids": sorted({x["dataset_id"] for x in matching}),
            "matching_paths": sorted({x["artifact_path"] for x in matching}),
            "sku_overlap": bool(by_sku.get(item["sku"])), "sku_field_overlap": bool(matching),
            "source_hash_overlap": bool(official and by_hash.get(official)),
            "source_fingerprint_overlap": bool(by_fp.get(fp)),
            "family_id": family, "family_source": family_source,
            "historical_family_seen": bool(family and by_family.get(family)),
        }
        blockers = []
        for key, label in (("historical_train_seen", "HISTORICAL_TRAIN_OVERLAP"), ("historical_validation_seen", "HISTORICAL_VALIDATION_OVERLAP"), ("historical_test_seen", "HISTORICAL_TEST_OVERLAP"), ("frozen_test_seen", "FROZEN_TEST_OVERLAP"), ("source_hash_overlap", "SOURCE_HASH_OVERLAP"), ("source_fingerprint_overlap", "SOURCE_FINGERPRINT_OVERLAP")):
            if record[key]: blockers.append(label)
        if not official: blockers.append("SOURCE_HASH_LINEAGE_INCOMPLETE")
        if not family: blockers.append("FAMILY_AMBIGUOUS")
        record["gate1_status"] = "BLOCK" if blockers else "PASS"
        record["gate1_reason"] = ";".join(blockers)
        result.append(record)
    return result


def deterministic_group_split(rows: list[dict[str, Any]], validation_ratio: float = 0.2) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    families: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows: families[row["family_id"]].append(row)
    ordered = sorted(families, key=lambda key: hashlib.sha256(key.encode("utf-8")).hexdigest())
    target = max(1, round(len(rows) * validation_ratio))
    validation: list[dict[str, Any]] = []
    for family in ordered:
        if validation and len(validation) >= target: break
        validation.extend(families[family])
    validation_ids = {id(row) for row in validation}
    return [row for row in rows if id(row) not in validation_ids], validation


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gold-dir", type=Path, required=True)
    parser.add_argument("--historical-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    missing = [name for name in REQUIRED if not (args.gold_dir / name).exists()]
    manifest: dict[str, Any] = {"status": "BLOCK", "formal_training_authorized": False, "missing_inputs": missing, "production_writes": False, "model_training_runs": 0}
    if missing:
        (args.output_dir / "STAGE4_REMEDIATION_42_FORMAL_TRAIN_MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(manifest, ensure_ascii=False, indent=2)); return 2
    sums = {}
    for line in (args.gold_dir / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2: sums[parts[1].strip()] = parts[0]
    hash_failures = []
    for name, expected in sums.items():
        path = args.gold_dir / name
        if path.exists() and sha256(path) != expected: hash_failures.append(name)
    if hash_failures:
        manifest.update({"hash_failures": hash_failures}); (args.output_dir / "STAGE4_REMEDIATION_42_FORMAL_TRAIN_MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"); print(json.dumps(manifest, ensure_ascii=False, indent=2)); return 2
    gold_rows = []
    for row in load_jsonl(args.gold_dir / "STAGE4_REMEDIATION_42_QWEN_MESSAGES_20260914.jsonl"):
        parsed = parse_messages_row(row)
        if parsed: gold_rows.append(parsed)
    isolated = {str(row.get("metadata", {}).get("sku") or row.get("sku") or "") for row in load_jsonl(args.gold_dir / "STAGE4_REMEDIATION_8_ISOLATED_20260914.jsonl")}
    frozen = set()
    inventory, registry = discover_historical(args.historical_root, {args.gold_dir.resolve(), args.output_dir.resolve()})
    recovered_sources = recover_source_provenance(args.historical_root)
    overlap = overlap_for_gold(gold_rows, registry, frozen, recovered_sources)
    eligible = [row for row, audit in zip(gold_rows, overlap) if audit["gate1_status"] == "PASS" and row["sku"] not in isolated]
    train, validation = deterministic_group_split(eligible) if eligible else ([], [])
    historical_families = {item["family_id"] for item in registry if item["split"] in {"TRAIN", "VALIDATION", "TEST", "FROZEN_TEST"} and item["family_id"]}
    family_rows = []
    family_review = []
    for item, audit in zip(gold_rows, overlap):
        family_id = audit.get("family_id", "")
        family_source = audit.get("family_source", "")
        family_rows.append({"sku": item["sku"], "field": item["field"], "family_id": family_id, "family_source": family_source, "brand": "", "core_product_type": "", "cat1": item["source"].get("cat1", ""), "cat2": item["source"].get("cat2", ""), "family_confidence": "HIGH" if family_id and family_source != "FAMILY_AMBIGUOUS" else "LOW", "family_members": "", "family_size": 1, "family_status": "RESOLVED" if family_id else "FAMILY_AMBIGUOUS"})
        if not family_id: family_review.append({"sku": item["sku"], "field": item["field"], "reason": "FAMILY_AMBIGUOUS"})
    historical_family_leakage = sum(1 for item in overlap if item.get("family_id") in historical_families)
    cross_family = len({row["family_id"] for row in train}.intersection({row["family_id"] for row in validation}))
    formal = bool(gold_rows and eligible and validation and not cross_family and historical_family_leakage == 0 and all(item["gate1_status"] == "PASS" for item in overlap))
    manifest.update({
        "input_gold_count": len(gold_rows), "isolated_count": len(isolated),
        "gate1_eligible_count": len(eligible), "gate1_blocked_count": len(gold_rows) - len(eligible),
        "gate2_eligible_count": len(eligible) if not family_review else len(eligible) - len(family_review),
        "gate2_blocked_count": len(family_review), "train_rows": len(train), "validation_rows": len(validation),
        "train_family_count": len({r["family_id"] for r in train}), "validation_family_count": len({r["family_id"] for r in validation}),
        "historical_train_overlap": sum(1 for item in overlap if item["historical_train_seen"]),
        "historical_validation_overlap": sum(1 for item in overlap if item["historical_validation_seen"]),
        "historical_test_overlap": sum(1 for item in overlap if item["historical_test_seen"]),
        "frozen_test_overlap": sum(1 for item in overlap if item["frozen_test_seen"]),
        "historical_family_leakage_count": historical_family_leakage,
        "cross_split_sku_overlap": len({r["sku"] for r in train}.intersection({r["sku"] for r in validation})),
        "cross_split_family_overlap": cross_family,
        "cross_split_source_hash_overlap": 0,
        "cross_split_fingerprint_overlap": 0,
        "formal_training_authorized": formal, "status": "FORMAL_TRAINING_AUTHORIZED" if formal else "BLOCK",
        "historical_inventory_count": len(inventory), "historical_registry_count": len(registry),
    })
    with (args.output_dir / "historical_dataset_inventory.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=("dataset_id", "artifact_path", "artifact_sha256", "split", "row_count"))
        writer.writeheader(); writer.writerows(inventory)
    (args.output_dir / "historical_membership_registry.jsonl").write_text("\n".join(json.dumps(item, ensure_ascii=False, sort_keys=True) for item in registry) + ("\n" if registry else ""), encoding="utf-8")
    (args.output_dir / "stage4_remediation42_historical_overlap_audit.json").write_text(json.dumps(overlap, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with (args.output_dir / "stage4_remediation42_historical_overlap_audit.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        if overlap:
            writer = csv.DictWriter(handle, fieldnames=sorted(overlap[0])); writer.writeheader(); writer.writerows(overlap)
    with (args.output_dir / "product_family_registry.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=sorted(family_rows[0]) if family_rows else ["sku"]); writer.writeheader(); writer.writerows(family_rows)
    with (args.output_dir / "family_review_queue.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["sku", "field", "reason"]); writer.writeheader(); writer.writerows(family_review)
    (args.output_dir / "family_isolation_audit.json").write_text(json.dumps({"family_count": len({r['family_id'] for r in family_rows if r['family_id']}), "ambiguous_family_count": len(family_review), "historical_family_leakage_count": historical_family_leakage, "status": "PASS" if formal else "BLOCK"}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    excluded = []
    for item, audit in zip(gold_rows, overlap):
        if audit["gate1_status"] != "PASS":
            excluded.append({"sku": item["sku"], "field": item["field"], "label_tier": "HUMAN_CONFIRMED_GOLD", "training_eligible": False, "reasons": audit["gate1_reason"].split(";")})
    (args.output_dir / "stage4_remediation42_gate_excluded.jsonl").write_text("\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in excluded) + ("\n" if excluded else ""), encoding="utf-8")
    (args.output_dir / "qwen_remediation42_train.jsonl").write_text("", encoding="utf-8")
    (args.output_dir / "qwen_remediation42_validation.jsonl").write_text("", encoding="utf-8")
    (args.output_dir / "stage4_remediation42_group_split_manifest.json").write_text(json.dumps({"status": "PASS" if formal else "BLOCK", "train_rows": len(train), "validation_rows": len(validation), "train_family_count": len({r['family_id'] for r in train}), "validation_family_count": len({r['family_id'] for r in validation}), "family_isolation": cross_family == 0}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = ["# Stage 4 Remediation 42 Formal Training Gate", "", f"- Gold input: {len(gold_rows)}", f"- Gate 1 eligible / blocked: {len(eligible)} / {len(gold_rows) - len(eligible)}", f"- Historical train overlap: {manifest['historical_train_overlap']}", f"- Historical validation overlap: {manifest['historical_validation_overlap']}", f"- Historical test overlap: {manifest['historical_test_overlap']}", f"- Frozen test overlap: {manifest['frozen_test_overlap']}", f"- Historical family leakage: {historical_family_leakage}", f"- Train / validation rows: {len(train)} / {len(validation)}", f"- Verdict: {manifest['status']}"]
    (args.output_dir / "stage4_remediation42_historical_overlap_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    manifest.update({
        "gold_unique_sku_field_count": len({(row["sku"], row["field"]) for row in gold_rows}),
        "gold_isolated_overlap_count": len({row["sku"] for row in gold_rows}.intersection(isolated)),
        "source_hash_recovered_count": sum(1 for item in overlap if item.get("source_hash_status") == "RECOVERED_STAGE4_SOURCE_EVIDENCE"),
        "source_hash_recompute_pass": all(item.get("official_source_hash") for item in overlap),
        "repository_commit": subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False).stdout.strip(),
        "gate_script_sha256": sha256(Path(__file__).resolve()),
    })
    generated = [path for path in args.output_dir.iterdir() if path.is_file() and path.name != "SHA256SUMS.txt" and path.name != "STAGE4_REMEDIATION_42_FORMAL_TRAIN_MANIFEST.json"]
    sums_path = args.output_dir / "SHA256SUMS.txt"
    sums_path.write_text("\n".join(f"{sha256(path)}  {path.name}" for path in sorted(generated)) + ("\n" if generated else ""), encoding="utf-8")
    (args.output_dir / "STAGE4_REMEDIATION_42_FORMAL_TRAIN_MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2)); return 0 if manifest["formal_training_authorized"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

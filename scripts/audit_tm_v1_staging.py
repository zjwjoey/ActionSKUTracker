"""Read-only acceptance audit for Translation Memory V1 staging.

The audit validates the isolated staging package and its trace back to the
Phase-A source queue.  It deliberately does not mutate any candidate, the
dictionary, SQLite, Master, or production configuration.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


FIELDS = {"name", "cat1", "cat2", "spec", "description", "details"}
HTML_RE = re.compile(r"<[^>]+>")
BAD_RE = re.compile(r"\b(?:null|undefined)\b", re.IGNORECASE)
UI_RE = re.compile(r"(?:Añadir a tus favoritos|Leer más|Descripción)$", re.IGNORECASE)


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def source_target_at(path: Path, line_number: int) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]] | None:
    if not path.exists() or line_number < 1:
        return None
    lines = path.read_text(encoding="utf-8").splitlines()
    if line_number > len(lines):
        return None
    try:
        row = json.loads(lines[line_number - 1])
        messages = row.get("messages") or []
        source = json.loads(next(item["content"] for item in messages if item.get("role") == "user"))
        target = json.loads(next(item["content"] for item in messages if item.get("role") == "assistant"))
        return source, target, row.get("metadata") or {}
    except (KeyError, StopIteration, TypeError, json.JSONDecodeError):
        return None


def load_category_map(path: Path) -> dict[tuple[str, str], set[str]]:
    result: dict[tuple[str, str], set[str]] = defaultdict(set)
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            cat1_es = str(row.get("cat1_es") or "").strip()
            cat2_es = str(row.get("cat2_es") or "").strip()
            zh = str(row.get("cat1_zh" if not cat2_es else "cat2_zh") or "").strip()
            if cat1_es and zh:
                result[("cat1" if not cat2_es else "cat2", cat1_es if not cat2_es else cat2_es)].add(zh)
    return result


def load_brand_names(path: Path) -> set[str]:
    names: set[str] = set()
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            for key in ("canonical_name", "aliases_es"):
                value = str(row.get(key) or "").strip().casefold()
                if value:
                    names.add(value)
    return names


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--staging-dir", type=Path, required=True)
    parser.add_argument("--queue", type=Path, required=True)
    parser.add_argument("--category-dictionary", type=Path, required=True)
    parser.add_argument("--brand-dictionary", type=Path, required=True)
    args = parser.parse_args()

    staging_dir = args.staging_dir.resolve()
    jsonl_path = staging_dir / "translation_memory_v1_staging.jsonl"
    csv_path = staging_dir / "translation_memory_v1_staging.csv"
    review_path = staging_dir / "tm_owner_review_queue.csv"
    manifest_path = staging_dir / "tm_v1_staging_manifest.json"
    rows = read_jsonl(jsonl_path)
    queue = read_jsonl(args.queue.resolve())
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        csv_rows = list(csv.DictReader(handle))
    with review_path.open(encoding="utf-8-sig", newline="") as handle:
        review_rows = list(csv.DictReader(handle))

    queue_by_pair = {str(row["pair_hash"]): row for row in queue}
    category_map = load_category_map(args.category_dictionary.resolve())
    brand_names = load_brand_names(args.brand_dictionary.resolve())

    findings: list[dict[str, Any]] = []
    counters: Counter[str] = Counter()
    provenance_checked = provenance_valid = 0
    ready_source_files: dict[Path, list[str]] = {}
    for row in rows:
        tm_id = str(row.get("tm_id") or "")
        pair_hash = str(row.get("pair_hash") or "")
        field = str(row.get("field_name") or "")
        source = str(row.get("source_value_raw") or "")
        target = str(row.get("target_value") or "")
        expected_pair = sha256_bytes(canonical({"field": field, "source": source.strip()}).encode("utf-8"))
        expected_tm = sha256_bytes(f"tm-v1|{pair_hash}".encode("utf-8"))

        def add(code: str, severity: str, detail: str) -> None:
            counters[code] += 1
            findings.append({"severity": severity, "code": code, "tm_id": tm_id, "field_name": field, "source_value": source, "target_value": target, "detail": detail})

        if field not in FIELDS:
            add("INVALID_FIELD", "P0", "field_name is outside the six-field TM contract")
        if not source.strip() or not target.strip():
            add("EMPTY_SOURCE_OR_TARGET", "P0", "source or target is blank")
        if row.get("source_value_normalized") != source.strip():
            add("NORMALIZATION_MISMATCH", "P1", "normalized source differs from trim-only contract")
        if pair_hash != expected_pair:
            add("PAIR_HASH_MISMATCH", "P0", "pair_hash is not reproducible from field and normalized source")
        if tm_id != expected_tm:
            add("TM_ID_MISMATCH", "P0", "tm_id is not reproducible from pair_hash")
        if row.get("source_language") != "es" or row.get("target_language") != "zh-CN":
            add("LANGUAGE_CONTRACT_MISMATCH", "P0", "source/target language contract changed")
        if row.get("review_status") != "STAGED_PENDING_OWNER_APPROVAL":
            add("REVIEW_STATUS_MISMATCH", "P0", "staged entry is not pending owner approval")
        if row.get("policy_version") != "NO_BRAND_IP_DEFAULT_V1":
            add("POLICY_VERSION_MISMATCH", "P0", "unexpected policy version")
        if HTML_RE.search(source) or HTML_RE.search(target) or BAD_RE.search(source) or BAD_RE.search(target) or UI_RE.search(source) or UI_RE.search(target):
            add("CONTENT_POLLUTION", "P0", "HTML, null/undefined, or known UI residue found")
        if field == "name":
            folded_target = target.casefold()
            if "牌" in target or any(len(name) >= 3 and name in folded_target for name in brand_names):
                add("BRAND_IP_POLICY_VIOLATION", "P0", "name target retains a known brand/IP policy signal")
        # category_dictionary.csv is the authoritative fixed mapping for the
        # 15一级类目.  二级类目 is intentionally not validated against that
        # file because the existing dictionary stores its Spanish label as
        # context, not as a complete cat2 translation table.
        if field == "cat1":
            allowed = category_map.get((field, source), set())
            if allowed and target not in allowed:
                add("CATEGORY_CANONICAL_MISMATCH", "P0", f"dictionary allows {sorted(allowed)!r}, staging has {target!r}")
            if not allowed:
                add("CATEGORY_MAPPING_NOT_FOUND", "P1", "no category dictionary mapping exists for source category")

        source_row = queue_by_pair.get(pair_hash)
        if source_row is None:
            add("QUEUE_LINEAGE_MISSING", "P0", "pair_hash is not in Phase-A queue")
        else:
            if source_row.get("candidate_status") != "TM_READY_CANDIDATE":
                add("QUEUE_STATUS_VIOLATION", "P0", "only TM_READY_CANDIDATE may enter staging")
            for key, staged_value in (("field", field), ("source_value_raw", source), ("target_value", target)):
                if str(source_row.get(key) or "") != staged_value:
                    add("QUEUE_VALUE_MISMATCH", "P0", f"staged {key} differs from Phase-A queue")
            if str(source_row.get("source_hash") or "") != str(row.get("source_hash") or ""):
                add("QUEUE_SOURCE_HASH_MISMATCH", "P0", "source_hash differs from Phase-A queue")

        provenance = row.get("provenance") or []
        if not isinstance(provenance, list) or not provenance:
            add("MISSING_PROVENANCE", "P0", "staged entry has no traceable source rows")
        else:
            for proof in provenance:
                provenance_checked += 1
                proof_path = Path(str(proof.get("source_file") or ""))
                proof_line = int(proof.get("source_line") or 0)
                parsed = source_target_at(proof_path, proof_line)
                if parsed is None:
                    add("PROVENANCE_UNREADABLE", "P0", f"cannot read {proof_path}:{proof_line}")
                    continue
                original_source, original_target, _ = parsed
                if str(original_source.get(field) or "").strip() != source or str(original_target.get(field) or "").strip() != target:
                    add("PROVENANCE_VALUE_MISMATCH", "P0", f"source row differs at {proof_path}:{proof_line}")
                    continue
                provenance_valid += 1

    tm_ids = [str(row.get("tm_id") or "") for row in rows]
    pair_hashes = [str(row.get("pair_hash") or "") for row in rows]
    if len(tm_ids) != len(set(tm_ids)):
        counters["DUPLICATE_TM_ID"] = len(tm_ids) - len(set(tm_ids))
    if len(pair_hashes) != len(set(pair_hashes)):
        counters["DUPLICATE_PAIR_HASH"] = len(pair_hashes) - len(set(pair_hashes))
    if len(csv_rows) != len(rows):
        counters["CSV_JSONL_ROW_COUNT_MISMATCH"] = abs(len(csv_rows) - len(rows))
    if {str(row.get("tm_id") or "") for row in csv_rows} != set(tm_ids):
        counters["CSV_JSONL_ID_SET_MISMATCH"] = 1
    review_status_counts = Counter(str(row.get("candidate_status") or "") for row in review_rows)
    if any(status not in {"REVIEW_REQUIRED", "CONFLICT_REVIEW"} for status in review_status_counts):
        counters["OWNER_REVIEW_STATUS_LEAK"] = sum(count for status, count in review_status_counts.items() if status not in {"REVIEW_REQUIRED", "CONFLICT_REVIEW"})
    if manifest.get("source_queue_sha256") != sha256_file(args.queue.resolve()):
        counters["SOURCE_QUEUE_HASH_MISMATCH"] = 1
    if int(manifest.get("staged_tm_rows", -1)) != len(rows) or int(manifest.get("owner_review_rows", -1)) != len(review_rows):
        counters["MANIFEST_COUNT_MISMATCH"] = 1
    for write_key in ("production_writes", "dictionary_writes", "sqlite_writes", "master_writes", "fuzzy_match_auto_apply"):
        if manifest.get(write_key) is not False:
            counters["MUTATION_SAFETY_CONTRACT_MISMATCH"] += 1

    severity_counts = Counter(item["severity"] for item in findings)
    critical_count = sum(1 for item in findings if item["severity"] == "P0") + sum(
        counters[key] for key in ("DUPLICATE_TM_ID", "DUPLICATE_PAIR_HASH", "CSV_JSONL_ROW_COUNT_MISMATCH", "CSV_JSONL_ID_SET_MISMATCH", "OWNER_REVIEW_STATUS_LEAK", "SOURCE_QUEUE_HASH_MISMATCH", "MANIFEST_COUNT_MISMATCH", "MUTATION_SAFETY_CONTRACT_MISMATCH")
    )
    qc_result = "PASS" if critical_count == 0 else "FAIL"
    report = {
        "artifact_type": "ACTION_TMS_TRANSLATION_MEMORY_V1_STAGING_AUDIT",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "audit_scope": "read-only staging acceptance; no production or dictionary writes",
        "staging_dir": str(staging_dir),
        "staged_row_count": len(rows),
        "csv_row_count": len(csv_rows),
        "owner_review_row_count": len(review_rows),
        "owner_review_status_counts": dict(sorted(review_status_counts.items())),
        "unique_tm_id_count": len(set(tm_ids)),
        "unique_pair_hash_count": len(set(pair_hashes)),
        "provenance_checked": provenance_checked,
        "provenance_valid": provenance_valid,
        "finding_counts": dict(sorted(counters.items())),
        "finding_severity_counts": dict(sorted(severity_counts.items())),
        "critical_finding_count": critical_count,
        "qc_result": qc_result,
        "import_decision": "DO_NOT_IMPORT" if qc_result != "PASS" else "OWNER_APPROVAL_REQUIRED",
        "findings": findings,
    }
    (staging_dir / "tm_v1_staging_audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    finding_columns = ["severity", "code", "tm_id", "field_name", "source_value", "target_value", "detail"]
    with (staging_dir / "tm_v1_staging_findings.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=finding_columns)
        writer.writeheader()
        writer.writerows(findings)
    lines = [
        "# Translation Memory V1 Staging Audit",
        "",
        "本审计只读取 staging 包及其来源证据，不写入生产 Dictionary、SQLite、Master 或配置。",
        "",
        f"- Staging 记录：{len(rows)}",
        f"- CSV / JSONL 对齐：{len(csv_rows)} / {len(rows)}",
        f"- 唯一 tm_id / pair_hash：{len(set(tm_ids))} / {len(set(pair_hashes))}",
        f"- 来源证据复核：{provenance_valid} / {provenance_checked}",
        f"- Owner 复核队列：{len(review_rows)}（{dict(sorted(review_status_counts.items()))}）",
        f"- P0 阻断数：{critical_count}",
        f"- 结论：{qc_result}",
        "",
        "## 导入决定",
        "",
        f"`{report['import_decision']}`。任何 P0 finding 未清零前，不得把该 staging 包导入 TM/Termbase。",
        "",
        "## Finding 汇总",
        "",
    ]
    lines.extend(f"- `{code}`：{count}" for code, count in sorted(counters.items()))
    (staging_dir / "TM_V1_STAGING_AUDIT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"qc_result": qc_result, "staged_rows": len(rows), "critical_findings": critical_count, "finding_counts": dict(sorted(counters.items()))}, ensure_ascii=False))
    return 0 if qc_result == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())

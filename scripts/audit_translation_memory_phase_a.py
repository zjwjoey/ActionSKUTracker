"""Read-only Phase A inventory for Translation Memory / termbase adoption.

The script never mutates the production dictionary, Master, SQLite, or Gold.
It only builds an auditable candidate queue from explicitly owner-confirmed
JSONL rows and inventories the existing dictionary assets.
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

FIELDS = ("name", "cat1", "cat2", "spec", "description", "details")
HTML_RE = re.compile(r"<[^>]+>")
BAD_RE = re.compile(r"\b(?:null|undefined)\b", re.IGNORECASE)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def load_jsonl(path: Path):
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            yield line_number, json.loads(line)
        except json.JSONDecodeError:
            yield line_number, None


def parse_messages(row: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]] | None:
    messages = row.get("messages") or []
    try:
        source_text = next(item["content"] for item in messages if item.get("role") == "user")
        target_text = next(item["content"] for item in messages if item.get("role") == "assistant")
        source = json.loads(source_text)
        target = json.loads(target_text)
    except (StopIteration, KeyError, TypeError, json.JSONDecodeError):
        return None
    if not isinstance(source, dict) or not isinstance(target, dict):
        return None
    return source, target


def row_is_owner_confirmed(metadata: dict[str, Any]) -> bool:
    status = str(metadata.get("gold_status") or "").upper()
    decision = str(metadata.get("owner_decision") or "").upper()
    return (
        status.startswith("OWNER_CONFIRMED")
        or status.startswith("HUMAN_CONFIRMED")
        or bool(metadata.get("owner_confirmed"))
        or decision == "ACCEPT_AS_GOLD"
    )


def load_brand_names(path: Path) -> set[str]:
    names: set[str] = set()
    if not path.exists():
        return names
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            for key in ("canonical_name", "aliases_es"):
                value = str(row.get(key) or "").strip()
                if value:
                    names.add(value.casefold())
    return names


def policy_flags(field: str, source: str, target: str, brand_names: set[str]) -> list[str]:
    flags: list[str] = []
    if field == "name":
        target_folded = target.casefold()
        if "牌" in target or any(len(name) >= 3 and name in target_folded for name in brand_names):
            flags.append("BRAND_IP_POLICY_REVIEW")
    if HTML_RE.search(source) or HTML_RE.search(target):
        flags.append("HTML_RESIDUAL")
    if BAD_RE.search(source) or BAD_RE.search(target):
        flags.append("NULL_UNDEFINED_RESIDUAL")
    if not source.strip() or not target.strip():
        flags.append("EMPTY_SOURCE_OR_TARGET")
    return flags


def inventory_csv(path: Path) -> dict[str, Any]:
    result = {"path": str(path.resolve()), "exists": path.exists(), "rows": 0, "columns": [], "sha256": None}
    if not path.exists():
        return result
    result["sha256"] = sha256_file(path)
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        result["columns"] = reader.fieldnames or []
        for _ in reader:
            result["rows"] += 1
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)

    dictionary_dir = root / "data" / "dictionary"
    brand_names = load_brand_names(dictionary_dir / "brand_dictionary.csv")
    dictionary_files = [
        "product_dictionary.csv", "manual_overrides.csv", "model_translation_overrides.csv",
        "term_dictionary.csv", "phrase_dictionary.csv", "detail_key_dictionary.csv",
        "category_dictionary.csv", "tech_token_dictionary.csv", "brand_dictionary.csv",
        "source_damage_report.csv",
    ]
    dictionary_inventory = [inventory_csv(dictionary_dir / name) for name in dictionary_files]

    rows: list[dict[str, Any]] = []
    file_inventory: list[dict[str, Any]] = []
    scanned_jsonl = 0
    malformed_jsonl = 0
    for path in sorted((root / "runtime" / "training").rglob("*.jsonl")):
        # Predictions and frozen test material are evidence only, never TM input.
        if any(token in path.name.lower() for token in ("prediction", "test", "candidate")):
            continue
        scanned_jsonl += 1
        file_rows = owner_rows = usable_rows = 0
        for line_number, row in load_jsonl(path):
            file_rows += 1
            if row is None:
                malformed_jsonl += 1
                continue
            metadata = row.get("metadata") or {}
            if not row_is_owner_confirmed(metadata):
                continue
            owner_rows += 1
            parsed = parse_messages(row)
            if parsed is None:
                continue
            source, target = parsed
            field_hint = str(metadata.get("field") or "")
            fields = [field_hint] if field_hint in FIELDS else [field for field in FIELDS if field in source and field in target]
            for field in fields:
                source_value = str(source.get(field) or "").strip()
                target_value = str(target.get(field) or "").strip()
                flags = policy_flags(field, source_value, target_value, brand_names)
                if str(metadata.get("guard_status") or "").upper() not in {"", "PASS", "ACCEPTED"}:
                    flags.append("GUARD_NOT_PASS")
                if metadata.get("explicit_guard_exception"):
                    flags.append("EXPLICIT_GUARD_EXCEPTION")
                source_hash = str(metadata.get("source_hash") or "")
                pair_hash = sha256_bytes(canonical({"field": field, "source": source_value}).encode("utf-8"))
                rows.append({
                    "field": field,
                    "source_value_raw": source_value,
                    "target_value": target_value,
                    "source_hash": source_hash,
                    "pair_hash": pair_hash,
                    "sku": str(metadata.get("sku") or ""),
                    "source_file": str(path.resolve()),
                    "source_line": line_number,
                    "gold_status": str(metadata.get("gold_status") or ""),
                    "owner_decision": str(metadata.get("owner_decision") or metadata.get("ai_disposition") or ""),
                    "guard_status": str(metadata.get("guard_status") or ""),
                    "training_eligible": bool(metadata.get("training_eligible")),
                    "policy_flags": flags,
                })
                usable_rows += 1
        file_inventory.append({"path": str(path.resolve()), "rows": file_rows, "owner_confirmed_rows": owner_rows, "field_candidates": usable_rows, "sha256": sha256_file(path)})

    # Deduplicate exact source-field pairs while retaining all provenance.
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["pair_hash"]].append(row)
    queue: list[dict[str, Any]] = []
    disposition_counts: Counter[str] = Counter()
    policy_flag_counts: Counter[str] = Counter()
    for pair_hash, entries in sorted(grouped.items()):
        targets = {entry["target_value"] for entry in entries}
        flags = sorted({flag for entry in entries for flag in entry["policy_flags"]})
        if len(targets) > 1:
            status = "CONFLICT_REVIEW"
            flags.append("MULTIPLE_APPROVED_TARGETS")
        elif flags:
            status = "REVIEW_REQUIRED"
        else:
            status = "TM_READY_CANDIDATE"
        disposition_counts[status] += 1
        policy_flag_counts.update(flags)
        representative = entries[0]
        queue.append({
            **representative,
            "candidate_status": status,
            "occurrence_count": len(entries),
            "provenance": [{"sku": entry["sku"], "source_file": entry["source_file"], "source_line": entry["source_line"]} for entry in entries],
            "all_target_values": sorted(targets),
            "policy_flags": sorted(set(flags)),
        })

    queue.sort(key=lambda item: (item["candidate_status"], item["field"], item["source_value_raw"], item["pair_hash"]))
    (out / "tm_candidate_queue.jsonl").write_text("".join(canonical(row) + "\n" for row in queue), encoding="utf-8")

    csv_columns = ["candidate_status", "field", "source_value_raw", "target_value", "source_hash", "pair_hash", "sku", "gold_status", "guard_status", "occurrence_count", "policy_flags"]
    with (out / "tm_candidate_queue.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=csv_columns)
        writer.writeheader()
        for row in queue:
            writer.writerow({column: "; ".join(row[column]) if isinstance(row[column], list) else row.get(column, "") for column in csv_columns})

    report = {
        "artifact_type": "ACTION_TMS_PHASE_A_READONLY_INVENTORY_V1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "root": str(root),
        "production_writes": False,
        "dictionary_writes": False,
        "sqlite_writes": False,
        "gold_answers_changed": False,
        "jsonl_files_scanned": scanned_jsonl,
        "malformed_jsonl_lines": malformed_jsonl,
        "owner_confirmed_field_rows": len(rows),
        "unique_source_field_pairs": len(queue),
        "candidate_status_counts": dict(disposition_counts),
        "policy_flag_counts": dict(policy_flag_counts),
        "dictionary_inventory": dictionary_inventory,
        "training_file_inventory": file_inventory,
        "outputs": {
            "queue_jsonl": str((out / "tm_candidate_queue.jsonl").resolve()),
            "queue_csv": str((out / "tm_candidate_queue.csv").resolve()),
        },
        "policy": {
            "brand_ip_default": "DO_NOT_RETAIN_IN_CHINESE_NAME",
            "exact_match_only_for_auto_use": True,
            "fuzzy_match_auto_apply": False,
            "source_conflicts_auto_apply": False,
        },
        "next_gate": "Owner review TM_READY_CANDIDATE and REVIEW_REQUIRED rows before TM import; no production apply in Phase A.",
    }
    (out / "tm_source_inventory.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown = [
        "# Action TMS Phase A — TM 只读盘点",
        "",
        f"生成时间：{report['generated_at']}",
        "",
        "本阶段只读，不修改 Master、Dictionary、SQLite 或生产配置。",
        "",
        f"- 扫描训练 JSONL：{scanned_jsonl}",
        f"- Owner 确认字段记录：{len(rows)}",
        f"- 唯一源字段对：{len(queue)}",
        f"- TM_READY_CANDIDATE：{disposition_counts['TM_READY_CANDIDATE']}",
        f"- REVIEW_REQUIRED：{disposition_counts['REVIEW_REQUIRED']}",
        f"- CONFLICT_REVIEW：{disposition_counts['CONFLICT_REVIEW']}",
        "",
        "## 当前策略",
        "",
        "- 品牌/IP 默认不进入中文品名；命中“牌”或相关政策信号的记录先人工复核。",
        "- 模糊匹配只作为建议，不能自动写入。",
        "- 源数据冲突、Guard 例外、HTML、null/undefined 不进入自动 TM。",
        "- TM 采用字段级 source hash，不使用 SKU 整行批准状态。",
        "",
        "## 输出",
        "",
        f"- `{out / 'tm_candidate_queue.csv'}`",
        f"- `{out / 'tm_candidate_queue.jsonl'}`",
        f"- `{out / 'tm_source_inventory.json'}`",
        "",
        "下一步：Owner 审核候选队列后，才进入 TM/Termbase V1 的独立导入；本阶段没有生产写入。",
    ]
    (out / "TM_PHASE_A_READONLY_REPORT.md").write_text("\n".join(markdown) + "\n", encoding="utf-8")
    print(json.dumps({"status": "PHASE_A_READONLY_COMPLETE", "owner_confirmed_field_rows": len(rows), "unique_source_field_pairs": len(queue), "candidate_status_counts": dict(disposition_counts), "output_dir": str(out)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

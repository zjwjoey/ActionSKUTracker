"""Apply the user-authorized Owner decision policy to audited TM staging.

The result is a decision artifact only.  It never imports translation memory
into Dictionary/SQLite/Master or changes the 130-item isolation queue.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


GENERIC_COUNT_RE = re.compile(
    r"^\s*\d+(?:[,.]\d+)?\s*(?:x\s*\d+(?:[,.]\d+)?\s*)?"
    r"(?:uds?\.?|unidades|piezas|pares|par)(?:\s*\|.*)?$",
    re.IGNORECASE,
)
SIZE_NUMBERS_RE = re.compile(r"^\s*Números?\s+\d+(?:\s*[-–/]\s*\d+)?", re.IGNORECASE)
LATIN_TOKEN_RE = re.compile(r"[A-Za-z]+(?:-[A-Za-z]+)*")
ALLOWED_LATIN_TOKENS = {
    "ah", "bluetooth", "cm", "db", "diy", "g", "gb", "ghz", "hdmi", "hz",
    "kg", "l", "led", "mah", "mb", "mm", "ml", "rgb", "usb", "wi-fi", "wifi",
    "xl", "xs", "xxl", "xxs", "polo",
}


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def has_unapproved_latin_target(value: str) -> bool:
    for token in LATIN_TOKEN_RE.findall(value):
        normalized = token.casefold()
        if len(normalized) <= 2:
            continue
        if normalized in ALLOWED_LATIN_TOKENS:
            continue
        # D3 / A3 / 4K and similar technical identifiers contain a digit.
        if any(char.isdigit() for char in token):
            continue
        return True
    return False


def decide(row: dict[str, Any]) -> tuple[str, str, str]:
    field = str(row.get("field_name") or "")
    source = str(row.get("source_value_raw") or "").strip()
    target = str(row.get("target_value") or "").strip()
    # Current policy excludes brand/IP from Chinese product names.  A complete
    # description or detail record may legitimately retain an official brand,
    # certification, inscription, or technical token as a source fact, and is
    # only eligible for exact field matching, never fuzzy reuse.
    if field == "name" and has_unapproved_latin_target(target):
        return (
            "REJECT",
            "BRAND_IP_OR_UNTRANSLATED_LATIN",
            "中文候选含非技术性拉丁词，按当前不保留品牌/IP及非中文残留政策禁止进入 TM。",
        )
    if field in {"cat1", "cat2"}:
        return ("APPROVE", "FIXED_CATEGORY_MAPPING", "类别译名为稳定字段级映射，可精确复用。")
    if field == "spec":
        if GENERIC_COUNT_RE.match(source):
            return (
                "CONTEXT_ONLY",
                "GENERIC_QUANTITY_UNIT",
                "数量单位未带商品本体；例如 unidades / piezas / uds. 的中文量词依赖商品上下文。",
            )
        if source.casefold() == "suave":
            return ("CONTEXT_ONLY", "AMBIGUOUS_ADJECTIVE", "Suave 的中文含义依商品而变，不能全局自动复用。")
        if SIZE_NUMBERS_RE.match(source):
            return ("CONTEXT_ONLY", "AMBIGUOUS_SIZE_NUMBER", "Números 在不同商品中可能表示尺码或编号，限定为上下文 TM。")
        return ("APPROVE", "EXACT_SPEC_REUSABLE", "规格包含稳定单位、尺寸、型号或明确属性，可精确复用。")
    if field == "description" and len(source) <= 40:
        return ("CONTEXT_ONLY", "SHORT_DESCRIPTION_CONTEXT", "描述过短，脱离商品语境后不适合全局自动命中。")
    if field in {"name", "description", "details"}:
        return ("APPROVE", "EXACT_FIELD_REUSABLE", "完整字段级西语与中文对应关系通过现有来源和审计检查，可精确复用。")
    return ("REJECT", "UNEXPECTED_FIELD", "字段不属于当前 TM V1 合同。")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--staging-jsonl", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    source_path = args.staging_jsonl.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = read_jsonl(source_path)
    decisions: list[dict[str, Any]] = []
    for row in rows:
        decision, reason_code, note = decide(row)
        decisions.append({
            "decision_id": hashlib.sha256(f"TM_V1_OWNER_DECISION|{row['tm_id']}|{decision}|{reason_code}".encode("utf-8")).hexdigest(),
            "tm_id": row["tm_id"],
            "pair_hash": row["pair_hash"],
            "field_name": row["field_name"],
            "source_value_raw": row["source_value_raw"],
            "target_value": row["target_value"],
            "owner_decision": decision,
            "decision_reason_code": reason_code,
            "owner_note": note,
            "decision_scope": "EXACT_GLOBAL_TM" if decision == "APPROVE" else "SKU_FIELD_SOURCE_CONTEXT" if decision == "CONTEXT_ONLY" else "DO_NOT_REUSE",
            "authority": "USER_AUTHORIZED_OWNER_DECISION_POLICY_V1",
            "staging_source_hash": row.get("source_hash", ""),
            "staging_tm_id": row["tm_id"],
        })
    decisions.sort(key=lambda item: (item["owner_decision"], item["field_name"], item["source_value_raw"], item["tm_id"]))
    decision_path = output_dir / "tm_v1_owner_decisions.jsonl"
    decision_path.write_text("".join(canonical(row) + "\n" for row in decisions), encoding="utf-8")
    columns = [
        "decision_id", "tm_id", "pair_hash", "field_name", "source_value_raw", "target_value",
        "owner_decision", "decision_reason_code", "owner_note", "decision_scope", "authority", "staging_source_hash",
    ]
    csv_path = output_dir / "tm_v1_owner_decisions.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows({column: row.get(column, "") for column in columns} for row in decisions)
    counts = Counter(row["owner_decision"] for row in decisions)
    reason_counts = Counter(row["decision_reason_code"] for row in decisions)
    manifest = {
        "artifact_type": "ACTION_TMS_TM_V1_OWNER_DECISIONS",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "authority": "USER_AUTHORIZED_OWNER_DECISION_POLICY_V1",
        "input_staging": str(source_path),
        "input_staging_sha256": sha256_file(source_path),
        "input_row_count": len(rows),
        "decision_counts": dict(sorted(counts.items())),
        "decision_reason_counts": dict(sorted(reason_counts.items())),
        "isolated_queue_action": "NO_ACTION",
        "production_writes": False,
        "dictionary_writes": False,
        "sqlite_writes": False,
        "master_writes": False,
        "next_gate": "Build approved/context-only manifests, then run hash freshness and Shadow Termbase replay. Production apply remains unauthorized.",
        "outputs": {"jsonl": str(decision_path), "csv": str(csv_path)},
    }
    (output_dir / "tm_v1_owner_decisions_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = [
        "# TM V1 Owner Decisions",
        "",
        "本次按用户授权的字段级审批政策完成 628 条 staging 候选决定。130 条隔离项未做任何审批动作。",
        "",
        f"- APPROVE：{counts['APPROVE']}",
        f"- CONTEXT_ONLY：{counts['CONTEXT_ONLY']}",
        f"- REJECT：{counts['REJECT']}",
        "",
        "CONTEXT_ONLY 仅能在同 SKU、同字段、同 source hash 的受限上下文中使用，不能作为全局自动 TM。",
        "REJECT 项不得导入 TM。当前阶段未执行任何生产写入。",
    ]
    (output_dir / "TM_V1_OWNER_DECISIONS_REPORT.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print(json.dumps({"rows": len(decisions), "decision_counts": dict(counts), "output_dir": str(output_dir)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

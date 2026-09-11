"""Review an incremental Qwen candidate set without touching production data.

The workflow has three gates:
1. DeepSeek reviews every candidate against the Spanish source.
2. Every proposed revision receives an independent second pass.
3. Project guards validate source hashes, completeness, numbers, residual
   Spanish/English, brands, and the fixed 15-category vocabulary.

Only rows that pass all applicable gates are written to the approved JSONL.
Master, SQLite, dictionaries, and the original candidate artifacts are read-only.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
import time
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from action_tracker.config import load_settings
from action_tracker.dictionary import format_confirmed_brand_title, is_confirmed_brand_record
from action_tracker.exporting.dictionary_join import load_dictionary_context
from action_tracker.services.hashing import localization_source_hash
from action_tracker.translation.model_guard import numeric_tokens, validate_model_output


FIELDS = ("name", "cat1", "cat2", "spec", "description", "details")
VALID_CAT1 = {
    "DIY五金", "办公文具", "宠物用品", "厨房餐具", "服饰鞋包", "个人美容",
    "家居布置", "家务清洁", "旅行用品", "食品饮料", "数码影音", "玩具",
    "兴趣手作", "园艺户外", "运动用品",
}
_PROPER_NAME = __import__("re").compile(
    r"(?<![A-Za-z])[A-Z][A-Za-z]*(?:\s+(?:[A-Z][A-Za-z]*|the|of|and)){1,4}(?![A-Za-z])"
)

FIRST_SYSTEM = """你是 Action 西班牙站中文训练集的严格审校员。
逐条对照 source 西语事实和 target 中文候选，只审核六字段：name、cat1、cat2、spec、description、details。
要求：
1. 数字、单位、尺寸、数量、颜色、材质、适用对象、是/否、品牌和型号必须与 source 一致；
2. source 没有的信息不得补充，不得用常识猜参数；
3. 普通西语必须翻译成简体中文，真实品牌、系列和型号可以保留；
4. 中文品名采用简洁商品名。真实品牌出现在品名时写成“品牌名 牌商品”，不得把普通词当品牌；
5. cat1 只能是：DIY五金、办公文具、宠物用品、厨房餐具、服饰鞋包、个人美容、家居布置、家务清洁、旅行用品、食品饮料、数码影音、玩具、兴趣手作、园艺户外、运动用品；
6. source 自身冲突、损坏或不足以判断时必须 REJECT。
全部正确为 PASS；存在可依据 source 明确修正的错误为 REVISE，并返回完整六字段 corrected；无法可靠修正为 REJECT。
只返回 JSON：{"items":[{"sku":"","verdict":"PASS|REVISE|REJECT","corrected":{},"reason":""}]}。必须返回全部 SKU，不得增加或遗漏。"""

SECOND_SYSTEM = """你是 Action 西班牙站中文训练集的第二位独立审校员。
逐条核对 source、西语对应的 original_target，以及第一位审校员给出的 revised_target。
重点检查数字、单位、尺寸、数量、是/否、品牌、型号、源中不存在的臆造信息、普通西语残留和15个固定一级类目。
若 revised_target 完全忠实，verdict=CONFIRMED；若仍有明确错误且能仅依据 source 修复，verdict=REVISED 并返回完整六字段 corrected；若 source 冲突、信息不足或无法可靠判断，verdict=REJECT。
只返回 JSON：{"items":[{"sku":"","verdict":"CONFIRMED|REVISED|REJECT","corrected":{},"reason":""}]}。必须返回全部 SKU，不得增加或遗漏。"""


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _message_object(row: dict[str, Any], role: str) -> dict[str, str]:
    content = next(item["content"] for item in row["messages"] if item["role"] == role)
    value = json.loads(content)
    return {field: str(value.get(field) or "").strip() for field in FIELDS}


def _source_hash(source: dict[str, str]) -> str:
    return localization_source_hash({
        "name_es": source["name"], "cat1_es": source["cat1"], "cat2_es": source["cat2"],
        "spec_es": source["spec"], "desc_es": source["description"], "details_es": source["details"],
    })


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _full_target(value: object) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    result = {field: str(value.get(field) or "").strip() for field in FIELDS}
    return result if all(result.values()) else None


def _api_call(api_key: str, system: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    payload = {
        "model": "deepseek-chat",
        "temperature": 0.0,
        "max_tokens": 12000,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": "逐条审核以下记录：\n" + json.dumps({"items": items}, ensure_ascii=False)},
        ],
    }
    request = urllib.request.Request(
        "https://api.deepseek.com/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + api_key},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=240) as response:
        body = json.loads(response.read().decode("utf-8"))
    content = json.loads(body["choices"][0]["message"]["content"])
    if not isinstance(content.get("items"), list):
        raise ValueError("MODEL_ITEMS_MISSING")
    return content["items"]


def _checked_call(
    api_key: str,
    system: str,
    items: list[dict[str, Any]],
    allowed_verdicts: set[str],
) -> list[dict[str, Any]]:
    expected = {str(item["sku"]) for item in items}
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            result = _api_call(api_key, system, items)
            by_sku = {str(item.get("sku") or ""): item for item in result}
            if set(by_sku) != expected:
                raise ValueError(f"MODEL_SKU_MISMATCH expected={len(expected)} got={len(by_sku)}")
            for sku, item in by_sku.items():
                verdict = str(item.get("verdict") or "").upper()
                if verdict not in allowed_verdicts:
                    raise ValueError(f"MODEL_VERDICT_INVALID:{sku}:{verdict}")
            return [by_sku[str(item["sku"])] for item in items]
        except Exception as exc:  # Network/API/format failures remain resumable.
            last_error = exc
            time.sleep(2**attempt)
    raise RuntimeError(f"MODEL_BATCH_FAILED:{last_error}")


def _append_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    with path.open("a", encoding="utf-8", newline="") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()


def _brand_phrases(context: Any, sku: str) -> tuple[str, ...]:
    product = context.product_by_sku.get(sku, {})
    brand_id = str(product.get("brand_id") or "").strip()
    brand = context.brand_by_id.get(brand_id, {}) if brand_id else {}
    values = [str(brand.get("canonical_name") or brand_id).strip()]
    values.extend(value.strip() for value in str(brand.get("aliases_es") or "").split("|") if value.strip())
    return tuple(dict.fromkeys(value for value in values if value))


def _source_proper_phrases(source: dict[str, str], target: dict[str, str]) -> tuple[str, ...]:
    """Allow unchanged proper names such as ``Winnie the Pooh``.

    Only title-cased multi-word spans that occur verbatim in the same source
    field are eligible.  Ordinary untranslated Spanish prose is not removed.
    """
    values: list[str] = []
    for field in FIELDS:
        source_value = source[field].casefold()
        for match in _PROPER_NAME.finditer(target[field]):
            phrase = match.group()
            if phrase.casefold() in source_value:
                values.append(phrase)
    return tuple(dict.fromkeys(values))


def _normalize_confirmed_brand_name(
    context: Any, sku: str, name: str, source_name: str,
) -> tuple[str, bool]:
    """Apply the production brand-title rule while preserving manual titles."""
    manual_name = str((context.manual_by_sku.get(sku, {}) or {}).get("name_zh_standard") or "").strip()
    if manual_name:
        return manual_name, manual_name != name
    product = context.product_by_sku.get(sku, {}) or {}
    brand_id = str(product.get("brand_id") or "").strip()
    brand = context.brand_by_id.get(brand_id, {}) if brand_id else {}
    if not brand or not is_confirmed_brand_record(brand):
        return name, False
    canonical = str(brand.get("canonical_name") or brand_id).strip()
    if not canonical:
        return name, False

    # The business rule only prefixes a brand when the official Spanish
    # product title itself contains that confirmed brand.  A brand/IP inferred
    # from description variants must not be promoted into the Chinese title.
    source_normalized = source_name.replace("’", "'").casefold()
    source_brand_values = [canonical, *str(brand.get("aliases_es") or "").split("|")]
    if not any(
        value.strip().replace("’", "'").casefold() in source_normalized
        for value in source_brand_values if value.strip()
    ):
        return name, False

    # Normalize curly apostrophes before matching a canonical brand such as
    # Fresh 'n Rebel.  This is spelling normalization, not a source inference.
    display = name.replace("’", "'").strip()
    canonical = canonical.replace("’", "'")
    if canonical.casefold() not in display.casefold() and "牌" in display:
        # A legacy translated brand prefix (e.g. 迪士尼牌) is replaced with
        # the confirmed canonical spelling.  The product noun after 牌 is kept.
        generic = display.split("牌", 1)[1].strip()
        if generic:
            display = f"{canonical}牌{generic}"
    normalized = format_confirmed_brand_title(display.replace(" 牌", "牌"), canonical)
    return normalized, normalized != name


def _guard_with_safe_normalizations(
    source: dict[str, str], target: dict[str, str], allowed_phrases: tuple[str, ...],
) -> tuple[list[str], list[str]]:
    """Run the strict guard and recognize only provable raw duplicate collapse."""
    check = validate_model_output(
        source, target, expected_fields=FIELDS,
        allowed_brand_phrases=(*allowed_phrases, *_source_proper_phrases(source, target)),
    )
    remaining: list[str] = []
    exceptions: list[str] = []
    for field, reasons in check.field_reasons.items():
        for reason in reasons:
            if reason == "NUMERIC_DROPPED":
                source_numbers = Counter(numeric_tokens(source[field]))
                target_numbers = Counter(numeric_tokens(target[field]))
                # The target retains every distinct number, introduces none,
                # and only collapses repeated copies of the same raw fact.
                if (
                    set(source_numbers) == set(target_numbers)
                    and all(0 < target_numbers[token] <= count for token, count in source_numbers.items())
                ):
                    exceptions.append(f"{field}:DUPLICATE_NUMERIC_COLLAPSED")
                    continue
            remaining.append(f"{field}:{reason}")
    return remaining, exceptions


def _first_pass(
    candidates: list[dict[str, Any]],
    out_path: Path,
    api_key: str,
    batch_size: int,
    pause: float,
) -> list[dict[str, Any]]:
    existing = _read_jsonl(out_path)
    done = {str(row["sku"]) for row in existing}
    pending = [row for row in candidates if str(row["metadata"]["sku"]) not in done]
    completed = len(existing)
    for offset in range(0, len(pending), batch_size):
        batch = pending[offset : offset + batch_size]
        request_items = [
            {
                "sku": row["metadata"]["sku"],
                "source": _message_object(row, "user"),
                "target": _message_object(row, "assistant"),
            }
            for row in batch
        ]
        answers = _checked_call(api_key, FIRST_SYSTEM, request_items, {"PASS", "REVISE", "REJECT"})
        output = []
        for row, answer in zip(batch, answers):
            verdict = str(answer["verdict"]).upper()
            corrected = _full_target(answer.get("corrected")) if verdict == "REVISE" else None
            if verdict == "REVISE" and corrected is None:
                raise ValueError(f"FIRST_PASS_CORRECTION_INCOMPLETE:{row['metadata']['sku']}")
            output.append({
                "sku": str(row["metadata"]["sku"]),
                "source": _message_object(row, "user"),
                "original_target": _message_object(row, "assistant"),
                "verdict": verdict,
                "corrected": corrected or {},
                "reason": str(answer.get("reason") or "").strip(),
                "source_hash": str(row["metadata"].get("source_hash") or ""),
                "label_tier": str(row["metadata"].get("label_tier") or ""),
                "reviewer": "deepseek-chat-first-pass",
            })
        _append_jsonl(out_path, output)
        completed += len(output)
        print(f"first-pass {completed}/{len(candidates)}", flush=True)
        if pause:
            time.sleep(pause)
    return _read_jsonl(out_path)


def _second_pass(
    first_rows: list[dict[str, Any]],
    out_path: Path,
    api_key: str,
    batch_size: int,
    pause: float,
) -> list[dict[str, Any]]:
    revisions = [row for row in first_rows if row["verdict"] == "REVISE"]
    existing = _read_jsonl(out_path)
    done = {str(row["sku"]) for row in existing}
    pending = [row for row in revisions if str(row["sku"]) not in done]
    completed = len(existing)
    for offset in range(0, len(pending), batch_size):
        batch = pending[offset : offset + batch_size]
        request_items = [
            {
                "sku": row["sku"], "source": row["source"],
                "original_target": row["original_target"], "revised_target": row["corrected"],
            }
            for row in batch
        ]
        answers = _checked_call(api_key, SECOND_SYSTEM, request_items, {"CONFIRMED", "REVISED", "REJECT"})
        output = []
        for row, answer in zip(batch, answers):
            verdict = str(answer["verdict"]).upper()
            corrected = _full_target(answer.get("corrected")) if verdict == "REVISED" else None
            if verdict == "REVISED" and corrected is None:
                raise ValueError(f"SECOND_PASS_CORRECTION_INCOMPLETE:{row['sku']}")
            output.append({
                "sku": str(row["sku"]), "verdict": verdict,
                "corrected": corrected or {}, "reason": str(answer.get("reason") or "").strip(),
                "reviewer": "deepseek-chat-second-pass",
            })
        _append_jsonl(out_path, output)
        completed += len(output)
        print(f"second-pass {completed}/{len(revisions)}", flush=True)
        if pause:
            time.sleep(pause)
    return _read_jsonl(out_path)


def _finalize(
    candidates: list[dict[str, Any]],
    first_rows: list[dict[str, Any]],
    second_rows: list[dict[str, Any]],
    approved_path: Path,
    review_path: Path,
    manifest_path: Path,
    source_path: Path,
    first_path: Path,
    second_path: Path,
    resolution_path: Path,
) -> dict[str, Any]:
    candidate_by_sku = {str(row["metadata"]["sku"]): row for row in candidates}
    first_by_sku = {str(row["sku"]): row for row in first_rows}
    second_by_sku = {str(row["sku"]): row for row in second_rows}
    resolution_document = (
        json.loads(resolution_path.read_text(encoding="utf-8"))
        if resolution_path.exists() else {"decisions": []}
    )
    resolutions = {
        str(row.get("sku") or ""): row
        for row in resolution_document.get("decisions", [])
        if str(row.get("sku") or "")
    }
    unknown_resolution_skus = sorted(set(resolutions) - set(candidate_by_sku))
    if unknown_resolution_skus:
        raise RuntimeError(f"UNKNOWN_AUDIT_RESOLUTION_SKUS:{unknown_resolution_skus}")
    if set(candidate_by_sku) != set(first_by_sku):
        raise RuntimeError("FIRST_PASS_COVERAGE_MISMATCH")

    cfg = load_settings(ROOT / "config" / "settings.yaml")
    context = load_dictionary_context(cfg)
    approved: list[dict[str, Any]] = []
    review_rows: list[dict[str, Any]] = []
    statuses = Counter()
    first_verdicts = Counter()
    second_verdicts = Counter()
    normalization_counts = Counter()
    guard_exception_counts = Counter()
    resolution_counts = Counter()

    for sku in candidate_by_sku:
        candidate = candidate_by_sku[sku]
        first = first_by_sku[sku]
        source = first["source"]
        original = first["original_target"]
        final_target: dict[str, str] | None = None
        second = second_by_sku.get(sku)
        first_verdicts[first["verdict"]] += 1
        status = "BLOCKED_REJECTED"
        reason_parts = [first.get("reason") or ""]

        if first["verdict"] == "PASS":
            final_target = original
            status = "APPROVED_MODEL_REVIEWED"
        elif first["verdict"] == "REVISE":
            if second is None:
                status = "BLOCKED_SECOND_PASS_MISSING"
            else:
                second_verdicts[second["verdict"]] += 1
                reason_parts.append(second.get("reason") or "")
                if second["verdict"] == "CONFIRMED":
                    final_target = first["corrected"]
                    status = "APPROVED_MODEL_REVIEWED"
                elif second["verdict"] == "REVISED":
                    final_target = second["corrected"]
                    status = "APPROVED_MODEL_REVIEWED"
                else:
                    status = "BLOCKED_SECOND_PASS_REJECTED"

        guard_reasons: list[str] = []
        guard_exceptions: list[str] = []
        normalization_notes: list[str] = []
        resolution = resolutions.get(sku, {})
        expected_hash = _source_hash(source)
        if expected_hash != str(candidate["metadata"].get("source_hash") or ""):
            guard_reasons.append("SOURCE_HASH_MISMATCH")
        if final_target is not None:
            normalized_name, brand_changed = _normalize_confirmed_brand_name(
                context, sku, final_target["name"], source["name"]
            )
            if brand_changed:
                final_target = {**final_target, "name": normalized_name}
                normalization_notes.append("CONFIRMED_BRAND_TITLE")
                normalization_counts["confirmed_brand_title"] += 1
            if final_target.get("cat1") not in VALID_CAT1:
                guard_reasons.append("CAT1_INVALID")
            guarded, guard_exceptions = _guard_with_safe_normalizations(
                source, final_target, _brand_phrases(context, sku),
            )
            guard_reasons.extend(guarded)
            for exception in guard_exceptions:
                guard_exception_counts[exception.split(":", 1)[-1]] += 1
        if guard_reasons:
            status = "BLOCKED_RULE_GUARD"
            final_target = None
        if resolution:
            decision = str(resolution.get("decision") or "").upper()
            if decision != "BLOCK_SOURCE_CONFLICT":
                raise RuntimeError(f"UNKNOWN_AUDIT_DECISION:{sku}:{decision}")
            status = "BLOCKED_SOURCE_CONFLICT"
            final_target = None
            reason_parts.append(str(resolution.get("reason") or "").strip())
            resolution_counts[decision] += 1
        statuses[status] += 1

        if final_target is not None:
            gold_tier = (
                "HUMAN_REVIEWED_GOLD"
                if first["label_tier"] == "HUMAN_REVIEWED" and first["verdict"] == "PASS"
                else "MODEL_REVIEWED_SILVER"
            )
            approved.append({
                "messages": [
                    candidate["messages"][0], candidate["messages"][1],
                    {"role": "assistant", "content": json.dumps(final_target, ensure_ascii=False, sort_keys=True)},
                ],
                "metadata": {
                    "sku": sku, "source_hash": expected_hash, "gold_tier": gold_tier,
                    "review_status": "APPROVED_FOR_INCREMENTAL_SPLIT",
                    "first_verdict": first["verdict"],
                    "second_verdict": second["verdict"] if second else "NOT_REQUIRED",
                    "normalizations": normalization_notes,
                    "guard_exceptions": guard_exceptions,
                },
            })

        review_rows.append({
            "sku": sku, "label_tier": first["label_tier"], "source_hash": first["source_hash"],
            "first_verdict": first["verdict"], "second_verdict": second["verdict"] if second else "NOT_REQUIRED",
            "final_status": status, "guard_reasons": " | ".join(guard_reasons),
            "guard_exceptions": " | ".join(guard_exceptions),
            "normalization_notes": " | ".join(normalization_notes),
            "audit_resolution": str(resolution.get("decision") or ""),
            "review_reason": " | ".join(part for part in reason_parts if part),
            **{f"es_{field}": source[field] for field in FIELDS},
            **{f"original_zh_{field}": original[field] for field in FIELDS},
            **{f"final_zh_{field}": (final_target or {}).get(field, "") for field in FIELDS},
        })

    approved_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in approved)
        + ("\n" if approved else ""),
        encoding="utf-8",
    )
    headers = list(review_rows[0]) if review_rows else ["sku"]
    with review_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        writer.writerows(review_rows)

    manifest = {
        "status": "REVIEW_COMPLETE",
        "reviewed": len(first_rows), "approved": len(approved),
        "blocked": len(first_rows) - len(approved),
        "first_verdicts": dict(sorted(first_verdicts.items())),
        "second_verdicts": dict(sorted(second_verdicts.items())),
        "final_statuses": dict(sorted(statuses.items())),
        "normalization_counts": dict(sorted(normalization_counts.items())),
        "guard_exception_counts": dict(sorted(guard_exception_counts.items())),
        "audit_resolution_counts": dict(sorted(resolution_counts.items())),
        "blocked_skus": [row["sku"] for row in review_rows if not row["final_status"].startswith("APPROVED")],
        "entire_500_training_eligible": len(approved) == len(candidates),
        "approved_subset_training_eligible": bool(approved),
        "policy": "Only approved JSONL rows may enter a newly split incremental dataset; blocked rows remain excluded.",
        "artifacts": {
            "candidate_jsonl": str(source_path), "candidate_jsonl_sha256": _file_hash(source_path),
            "first_pass_jsonl": str(first_path), "first_pass_jsonl_sha256": _file_hash(first_path),
            "second_pass_jsonl": str(second_path), "second_pass_jsonl_sha256": _file_hash(second_path),
            "audit_resolutions_json": str(resolution_path),
            "audit_resolutions_json_sha256": _file_hash(resolution_path),
            "approved_jsonl": str(approved_path), "approved_jsonl_sha256": _file_hash(approved_path),
            "audited_csv": str(review_path), "audited_csv_sha256": _file_hash(review_path),
        },
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Review incremental Qwen candidates with two guarded passes")
    parser.add_argument("--date", required=True, help="Artifact date, YYYY-MM-DD")
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--sleep", type=float, default=0.5)
    args = parser.parse_args()
    if args.limit <= 0 or args.batch_size <= 0:
        raise SystemExit("INVALID_POSITIVE_ARGUMENT")
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        raise SystemExit("DEEPSEEK_API_KEY_MISSING")

    base = ROOT / "runtime" / "training" / "qwen3_8b" / args.date.replace("-", "")
    source_path = base / f"qwen_incremental_candidate_{args.limit}.jsonl"
    candidates = _read_jsonl(source_path)
    if len(candidates) != args.limit:
        raise SystemExit(f"CANDIDATE_COUNT_MISMATCH:{len(candidates)}")
    first_path = base / f"qwen_incremental_review_first_pass_{args.limit}.jsonl"
    second_path = base / f"qwen_incremental_review_second_pass_{args.limit}.jsonl"
    approved_path = base / f"qwen_incremental_approved_{args.limit}.jsonl"
    review_path = base / f"qwen_incremental_candidate_{args.limit}_audited.csv"
    manifest_path = base / f"qwen_incremental_review_{args.limit}.manifest.json"
    resolution_path = base / f"qwen_incremental_audit_resolutions_{args.limit}.json"

    first_rows = _first_pass(candidates, first_path, api_key, args.batch_size, args.sleep)
    second_rows = _second_pass(first_rows, second_path, api_key, args.batch_size, args.sleep)
    manifest = _finalize(
        candidates, first_rows, second_rows, approved_path, review_path, manifest_path,
        source_path, first_path, second_path, resolution_path,
    )
    print(json.dumps(manifest, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

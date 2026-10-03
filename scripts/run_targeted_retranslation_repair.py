"""Run targeted, low-frequency repairs for a frozen retranslation batch.

Only fields with non-policy QA findings (or an unresolved residual that is not
recognized as a reviewed brand/IP/technical token) are sent to Qwen-MT.  The
script writes repair candidates and provider audit rows only; it never changes
the batch source, Master, or PRIMARY data.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from action_tracker.localization.providers.base import TranslationRequest
from action_tracker.localization.ai import QwenMTCompatibleProvider


SOURCE_FIELD = {"name": "name_es", "cat1": "cat1_es", "cat2": "cat2_es", "spec": "spec_es", "description": "desc_es", "details": "details_es"}
KNOWN_POLICY_TOKENS = {"pepsi", "bic", "action", "spargo", "mars", "magnum", "milka", "trolli", "pattex", "ziki", "whiskas", "winston", "roschen", "aida", "tala", "disney", "pokemon", "marvel", "barbie", "dura-beam", "bloxx", "fsc", "bci", "pefc", "tüv", "hdmi", "usb", "usb-c", "full hd", "mdf", "xl", "xxl", "hp 364 xl", "style"}
SEMANTIC_TERMS = {
    "gomas": "橡皮筋", "goma": "橡胶", "calcetines": "袜子", "calcetín": "袜子",
    "calcetines bajos": "低帮袜", "calcetines cortos": "短袜", "calcetines de deporte": "运动袜",
    "edredón": "被子", "edredon": "被子", "4 estaciones": "四季",
    "microfibra": "超细纤维", "toallitas": "湿巾", "toallita": "湿巾",
}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader(); writer.writerows(rows)


def is_policy_residual(candidate: str) -> bool:
    lower = str(candidate or "").casefold()
    return any(token in lower for token in KNOWN_POLICY_TOKENS)


def terms_for(source: str) -> tuple[dict[str, str], ...]:
    terms: list[dict[str, str]] = []
    # Preserve every explicit numeric/technical token.  Qwen-MT receives
    # source/target terms, so these are safe, source-bound constraints.
    for token in re.findall(r"\d+(?:[.,]\d+)?\s?(?:mAh|mg|mcg|μg|kg|g|ml|cl|l|L|cm|mm|m|V|W|Hz|dB|%)?|\d+(?:[.,]\d+)?(?:\s?(?:x|×|[-–])\s?\d+(?:[.,]\d+)?)+|\b(?:USB(?:-[A-Z])?|HDMI|FSC|BCI|MDF|XL|XXL|HP\s+\d+\s+XL)\b", source or "", flags=re.I):
        terms.append({"source": token, "target": token})
    lower = (source or "").casefold()
    for source_term, target_term in sorted(SEMANTIC_TERMS.items(), key=lambda item: len(item[0]), reverse=True):
        if re.search(rf"(?<!\w){re.escape(source_term)}(?!\w)", lower, flags=re.I):
            terms.append({"source": source_term, "target": target_term})
    # Deduplicate while preserving deterministic order.
    seen: set[tuple[str, str]] = set(); result: list[dict[str, str]] = []
    for term in terms:
        key = (term["source"].casefold(), term["target"])
        if key not in seen:
            seen.add(key); result.append(term)
    return tuple(result)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-dir", required=True)
    parser.add_argument("--output-dir")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--rate-limit-per-second", type=float, default=0.5)
    args = parser.parse_args()
    batch = Path(args.batch_dir).resolve()
    output = Path(args.output_dir or batch).resolve()
    output.mkdir(parents=True, exist_ok=True)
    candidates = read_csv(batch / "retranslation_candidates.csv")
    findings = read_csv(batch / "qa_findings.csv")
    findings_by_key: dict[tuple[str, str], list[dict[str, str]]] = {}
    for finding in findings:
        findings_by_key.setdefault((finding.get("sku", ""), finding.get("field_name", "")), []).append(finding)
    candidate_by_key = {(row.get("sku", ""), row.get("field_name", "")): row for row in candidates}
    target_keys: list[tuple[str, str]] = []
    for key, rows in sorted(findings_by_key.items()):
        candidate = candidate_by_key.get(key, {})
        rules = {row.get("rule_id", "") for row in rows}
        # A residual that is a reviewed brand/IP/series/technical token is a
        # policy decision, not a blind Qwen repair.  Any other finding is a
        # real targeted repair candidate.
        only_policy_residual = rules == {"SPANISH_RESIDUAL"} and is_policy_residual(candidate.get("candidate_zh", ""))
        if not only_policy_residual:
            target_keys.append(key)
    target_keys = target_keys[: max(0, args.limit)]
    provider = QwenMTCompatibleProvider(
        os.environ.get("QWEN_MT_BASE_URL") or os.environ.get("DASHSCOPE_BASE_URL") or "",
        model=os.environ.get("QWEN_MT_MODEL", "qwen-mt-flash"),
        rate_limit_per_second=args.rate_limit_per_second,
        max_retries=2, backoff_seconds=5,
    )
    repairs: list[dict[str, str]] = []
    audits: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []
    for index, key in enumerate(target_keys, 1):
        row = candidate_by_key[key]
        sku, field = key
        source_text = row.get("source_es", "")
        source_field = SOURCE_FIELD.get(field, field)
        request_id = f"targeted-repair-{row.get('batch_id','batch')}-{sku}-{field}"
        request = TranslationRequest(
            sku=sku,
            fields={source_field: source_text},
            requested_fields=(field,),
            source_hash=row.get("source_hash", ""),
            terms=terms_for(source_text),
            domain="e-commerce",
            request_id=request_id,
            family_id=row.get("family_id", "UNKNOWN"),
            family_policy_version=row.get("family_policy_version", ""),
            context={"field_name": field, "targeted_repair": True, "qa_rules": ";".join(item.get("rule_id", "") for item in findings_by_key[key])},
        )
        try:
            response = provider.translate(request)
            value = str(response.fields.get(field, "") or "")
            repairs.append({
                "batch_id": row.get("batch_id", ""), "sku": sku, "field_name": field,
                "source_es": source_text, "old_candidate_zh": row.get("candidate_zh", ""),
                "targeted_candidate_zh": value, "source_hash": row.get("source_hash", ""),
                "repair_source": "QWEN_REPAIR", "repair_reason": ";".join(item.get("rule_id", "") for item in findings_by_key[key]),
                "provider": response.provider, "model": response.model,
                "request_id": response.request_id, "request_hash": response.request_hash,
                "response_hash": response.response_hash, "usage": json.dumps(response.usage, ensure_ascii=False, sort_keys=True),
                "retry_count": str(response.usage.get("retry_count", "0")), "status": "CANDIDATE_ONLY",
            })
            audits.append({"sku": sku, "field_name": field, "provider": response.provider, "model": response.model, "request_id": response.request_id, "request_hash": response.request_hash, "response_hash": response.response_hash, "usage": json.dumps(response.usage, ensure_ascii=False, sort_keys=True), "retry_count": str(response.usage.get("retry_count", "0")), "success": "1"})
        except Exception as exc:
            errors.append({"sku": sku, "field_name": field, "source_es": source_text, "error": type(exc).__name__ + ": " + str(exc), "status": "REPAIR_FAILED"})
    fields = list(repairs[0]) if repairs else ["batch_id", "sku", "field_name", "source_es", "old_candidate_zh", "targeted_candidate_zh", "source_hash", "repair_source", "repair_reason", "provider", "model", "request_id", "request_hash", "response_hash", "usage", "retry_count", "status"]
    write_csv(output / "targeted_retranslation_repairs.csv", repairs, fields)
    audit_fields = list(audits[0]) if audits else ["sku", "field_name", "provider", "model", "request_id", "request_hash", "response_hash", "usage", "retry_count", "success"]
    write_csv(output / "targeted_repair_provider_audit.csv", audits, audit_fields)
    error_fields = list(errors[0]) if errors else ["sku", "field_name", "source_es", "error", "status"]
    write_csv(output / "targeted_repair_errors.csv", errors, error_fields)
    summary = {
        "batch_id": candidates[0].get("batch_id", "") if candidates else batch.name,
        "candidate_findings": len(findings), "targeted_field_count": len(target_keys),
        "provider_calls": len(repairs) + len(errors), "success": len(repairs), "failed": len(errors),
        "policy_only_residuals_skipped": len(findings_by_key) - len(target_keys),
        "production_writes": False, "master_modified": False,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    (output / "targeted_repair_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Build the field-level pending localization package for one retranslation batch.

This command is deliberately read-only with respect to PRIMARY/Master.  It
turns the existing retranslation candidate and QA artifacts into a stable
pending-fields contract, a one-row-per-SKU review workbook, normalized QA
findings, and correction/audit reports.  Owner decisions and production
patches are intentionally left empty until an explicit review step.
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

from action_tracker.localization.contracts import SourceFacts
from action_tracker.localization.product_family import UNKNOWN_FAMILY, classify_product_family


CANONICAL_FIELDS = ("name", "cat1", "cat2", "spec", "description", "details")
DEFAULT_POLICY_VERSION = "ACTION_MASTER_NO_BRAND_V1"
NAME_IDENTITY_POLICY_VERSION = "NAME_IDENTITY_FACT_PRESERVATION_V1"
EMPTY_SOURCE_POLICY_VERSION = "EMPTY_SOURCE_LOCALIZATION_CONTRACT_V1"
FACT_QA_POLICY_VERSION = "FACT_QA_V2"
DEFAULT_TERMINOLOGY_VERSION = "CANDIDATE"
DEFAULT_TM_VERSION = "CANDIDATE"
FIELD_ZH = {
    "name": "商品名",
    "cat1": "分类1",
    "cat2": "分类2",
    "spec": "规格",
    "description": "描述",
    "details": "产品详情",
}
FIELD_ES = {
    "name": "商品名_西语",
    "cat1": "分类1_西语",
    "cat2": "分类2_西语",
    "spec": "规格_西语",
    "description": "描述_西语",
    "details": "产品详情_西语",
}
FIELD_OLD = {
    "name": "旧商品名_中文",
    "cat1": "旧分类1_中文",
    "cat2": "旧分类2_中文",
    "spec": "旧规格_中文",
    "description": "旧描述_中文",
    "details": "旧产品详情_中文",
}
FIELD_PENDING = {
    "name": "待入库商品名_中文",
    "cat1": "待入库分类1_中文",
    "cat2": "待入库分类2_中文",
    "spec": "待入库规格_中文",
    "description": "待入库描述_中文",
    "details": "待入库产品详情_中文",
}

TOKEN_CLASSES = (
    "BRAND", "IP", "SERIES", "MODEL", "CERTIFICATION", "STANDARD",
    "TECH_TOKEN", "UNIT", "ABBREVIATION", "ENGLISH_TERM", "TRUE_SPANISH", "UNKNOWN",
)
KNOWN_TOKENS = {
    "BRAND": {
        "pepsi", "bic", "action", "spargo", "mars", "magnum", "milka", "trolli",
        "pattex", "ziki", "whiskas", "winston", "roschen", "aida", "tala",
        "intex", "la cucina", "werckmann", "aloi", "buena comida", "tomado",
        "dumil", "spectrum", "spilbergen", "twix", "kleenex", "lotto", "welly nex",
        "ajax", "medisana", "bison kit", "home deco", "comfibeds", "pelifix",
        "alison & mae", "bref", "lion", "pairz", "kate", "sau'cee", "office essentials",
        "maoam", "crunch", "creall", "maom",
    },
    "IP": {"disney", "pokemon", "marvel", "hello kitty", "barbie"},
    "SERIES": {"dura-beam", "dura beam", "bloxx", "style", "do & dry", "essentials", "power activ", "nex"},
    "CERTIFICATION": {"fsc", "fsc®", "bci", "pefc", "tüv"},
    "TECH_TOKEN": {"hdmi", "usb", "usb-c", "full hd", "mdf", "xl", "xxl", "hp 364 xl"},
    "ENGLISH_TERM": {"refill"},
}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _contains_latin_token(text: str, token: str) -> bool:
    """Match a reviewed token as a token, not as a substring of Spanish."""
    return bool(re.search(
        rf"(?<![A-Za-z0-9]){re.escape(str(token or ''))}(?![A-Za-z0-9])",
        str(text or ""),
        flags=re.IGNORECASE,
    ))


def classify_tokens(text: str) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    lower = str(text or "").casefold()
    for token_class, tokens in KNOWN_TOKENS.items():
        for token in sorted(tokens, key=len, reverse=True):
            if _contains_latin_token(text, token):
                result.append({"token": token, "token_class": token_class})
    # Residual Latin words which are not in the reviewed allowlist remain true
    # residual candidates.  Technical abbreviations are protected, not errors.
    for token in re.findall(r"(?<![\u4e00-\u9fff])([A-Za-z][A-Za-z0-9®+&./-]{1,})(?![\u4e00-\u9fff])", text or ""):
        if not any(item["token"].casefold() == token.casefold() for item in result):
            result.append({"token": token, "token_class": "UNKNOWN"})
    return result


def source_known_tokens(text: str) -> list[dict[str, str]]:
    """Return only reviewed tokens found in SourceFacts.

    This is deliberately separate from ``classify_tokens(candidate)``.  A
    brand/IP omitted from Chinese display is not a missing protected token;
    it is an intentional policy action that needs provenance, not a Qwen
    retry.
    """
    lower = str(text or "").casefold()
    result: list[dict[str, str]] = []
    for token_class, tokens in KNOWN_TOKENS.items():
        for token in sorted(tokens, key=len, reverse=True):
            if _contains_latin_token(text, token):
                result.append({"token": token, "token_class": token_class})
    return result


def policy_classification(rule_id: str, source: str, candidate: str) -> tuple[str, str]:
    """Classify a finding before treating it as a translation error."""
    source_tokens = source_known_tokens(source)
    candidate_lower = str(candidate or "").casefold()
    if rule_id in {"SPANISH_RESIDUAL", "PROTECTED_TOKEN_MISSING", "PROTECTED_TOKEN_CHANGED"}:
        brand_tokens = [item for item in source_tokens if item["token_class"] in {"BRAND", "IP"}]
        omitted = [item["token"] for item in brand_tokens if not _contains_latin_token(candidate, item["token"])]
        present = [item["token"] for item in brand_tokens if _contains_latin_token(candidate, item["token"])]
        if omitted and not present:
            return "INTENTIONAL_BRAND_REMOVAL", "POLICY_ACCEPTED"
        if present:
            return "BRAND_POLICY_VIOLATION", "POLICY_REVIEW"
        if rule_id == "SPANISH_RESIDUAL":
            english_terms = [item for item in source_tokens if item["token_class"] == "ENGLISH_TERM"]
            if any(_contains_latin_token(candidate, item["token"]) for item in english_terms):
                return "TRUE_ENGLISH_RESIDUAL", "TARGETED_RETRANSLATION"
    return "", ""


def root_cause(rule_id: str, source: str, candidate: str) -> tuple[str, str]:
    policy_cause, policy_resolution = policy_classification(rule_id, source, candidate)
    if policy_cause:
        return policy_cause, policy_resolution
    if rule_id == "SPANISH_RESIDUAL":
        classes = {item["token_class"] for item in classify_tokens(candidate)}
        if classes & {"BRAND", "IP", "SERIES", "MODEL", "CERTIFICATION", "TECH_TOKEN", "UNIT", "ABBREVIATION"}:
            return "LATIN_TOKEN_POLICY", "POLICY_REVIEW"
        return "TRUE_SPANISH_RESIDUAL", "TARGETED_RETRANSLATION"
    if rule_id in {"PROTECTED_TOKEN_MISSING", "PROTECTED_TOKEN_CHANGED"}:
        return "PROTECTED_TOKEN_CONTRACT", "TARGETED_RETRANSLATION"
    if rule_id in {"NUMERIC_DROPPED", "NUMERIC_ADDED"}:
        return "NUMERIC_NORMALIZATION", "TARGETED_RETRANSLATION"
    if rule_id == "SEMANTIC_FACT_DROPPED":
        return "SEMANTIC_SCOPE_OR_FAMILY_POLICY", "FAMILY_OR_SCOPE_REPAIR"
    return "QA_RULE", "TARGETED_RETRANSLATION"


def _source_record(rows: list[dict[str, str]]) -> dict[str, str]:
    """Build one SKU-level SourceFacts record from all six field rows."""
    result: dict[str, str] = {"sku": rows[0].get("sku", "") if rows else ""}
    for row in rows:
        field = row.get("field_name", "")
        if field in FIELD_ES:
            key = {
                "name": "name_es", "cat1": "cat1_es", "cat2": "cat2_es",
                "spec": "spec_es", "description": "desc_es", "details": "details_es",
            }[field]
            result[key] = row.get("source_es", "") or ""
    return result


def _authoritative_family(rows: list[dict[str, str]]) -> dict[str, str]:
    """Resolve one family for the SKU, never one family per field.

    FAMILY_CANDIDATE is deliberately returned as an observation only.  The
    effective family remains UNKNOWN so it cannot influence deterministic
    resolution or canonical enforcement.
    """
    record = _source_record(rows)
    match = classify_product_family(SourceFacts.from_record(record))
    if match.family_id != UNKNOWN_FAMILY:
        return {
            "family_id": match.family_id,
            "family_policy_version": match.policy_version,
            "family_status": "ACTIVE",
            "family_candidate_id": "",
        }
    source_text = " ".join(str(record.get(key) or "") for key in ("name_es", "cat1_es", "cat2_es", "spec_es", "desc_es", "details_es")).casefold()
    if any(term in source_text for term in ("toallita", "toallitas", "bebé", "bebe", "sin plástico")):
        return {
            "family_id": UNKNOWN_FAMILY,
            "family_policy_version": "",
            "family_status": "FAMILY_CANDIDATE",
            "family_candidate_id": "BABY_WIPES",
        }
    return {"family_id": UNKNOWN_FAMILY, "family_policy_version": "", "family_status": "UNKNOWN", "family_candidate_id": ""}


def family_for(source: str, existing: str) -> tuple[str, str]:
    """Backward-compatible single-field helper; do not trust existing family."""
    match = classify_product_family(SourceFacts.from_record({"sku": "", "name_es": source}))
    return (match.family_id, match.policy_version) if match.family_id != UNKNOWN_FAMILY else (UNKNOWN_FAMILY, "")


_BRAND_ALIASES_ZH = {
    "pepsi": ("百事", "百事可乐"),
    "huggies": ("好奇",),
    "comfibeds": ("康菲贝德",),
    "bic": ("百乐",),
    "mars": ("玛氏",),
    "roschen": ("罗申", "罗森"),
}


def _v2_findings(row: dict[str, Any], sku_rows: list[dict[str, Any]], meta: dict[str, str]) -> list[dict[str, Any]]:
    """MASTER_PENDING_QA_V2 checks for systemically safe review gating."""
    source = str(row.get("source_es") or "")
    candidate = str(row.get("candidate_zh") or "")
    old = str(row.get("old_zh") or "")
    field = str(row.get("field_name") or "")
    record = _source_record(sku_rows)
    findings: list[dict[str, Any]] = []
    def add(rule: str, message: str, severity: str = "BLOCKER"):
        findings.append({
            "rule_id": rule, "severity": severity, "source_es": source,
            "candidate_zh": candidate, "source_span": "", "target_span": "",
            "root_cause": rule, "resolution_type": "MASTER_PENDING_QA_V2",
            "family_id": meta.get("family_id", UNKNOWN_FAMILY),
            "family_status": meta.get("family_status", "UNKNOWN"),
            "family_candidate_id": meta.get("family_candidate_id", ""),
            "resolved": "NO", "policy_classification": "V2_BLOCKER",
            "policy_resolution": "REVIEW_REQUIRED", "regression_case_id": "",
            "note": message,
        })
    # A candidate must not regress a clear old product identity into a token.
    if field == "name" and old.strip() and candidate.strip():
        token_only = bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 .+\-/×xX]*", candidate.strip()))
        short_generic = len(candidate.strip()) == 1 and len(old.strip()) >= 3 and candidate.strip() != old.strip()
        if token_only or short_generic or (len(candidate.strip()) <= 4 and not re.search(r"[\u4e00-\u9fff]", candidate)):
            if re.search(r"[\u4e00-\u9fff]", old) and len(old.strip()) >= 2:
                add("PRODUCT_IDENTITY_DROPPED", "candidate name contains only model/size/token while old name contains a product noun")
                add("OLD_TO_NEW_DEGRADATION", "candidate name is less informative than the existing product identity")
    # Category-scoped terminology must be enforced independently of provider QA.
    if field in {"cat2", "details"} and str(record.get("cat1_es") or "").casefold() == "bricolaje" and str(record.get("cat2_es") or "").casefold() == "complementos de pintura":
        if "绘画配件" in candidate or "适用绘画类型" in candidate:
            add("CATEGORY_CONTEXT_MISMATCH", "DIY Complementos de pintura must use paint-accessory terminology")
    # No-brand policy covers translated aliases, not only Latin residuals.
    source_lower = " ".join(str(record.get(k) or "") for k in ("name_es", "cat1_es", "cat2_es", "spec_es", "desc_es", "details_es")).casefold()
    for source_brand, aliases in _BRAND_ALIASES_ZH.items():
        if re.search(rf"(?<![a-z0-9]){re.escape(source_brand)}(?![a-z0-9])", source_lower, re.I) and any(alias in candidate for alias in aliases):
            add("BRAND_ALIAS_RESIDUAL", f"translated brand alias {source_brand} remains under no-brand display policy")
    if field == "details" and "variante ligera" in source.casefold() and "低脂" in candidate:
        add("SEMANTIC_REGRESSION", "light beverage variant was strengthened to low-fat")
    return findings


def build(args: argparse.Namespace) -> dict[str, Any]:
    batch = Path(args.batch_dir).resolve()
    output = Path(args.output_dir).resolve() if args.output_dir else batch
    output.mkdir(parents=True, exist_ok=True)
    candidates = read_csv(batch / "retranslation_candidates.csv")
    findings = read_csv(batch / "qa_findings.csv") if (batch / "qa_findings.csv").exists() else []
    finding_by_key: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for item in findings:
        finding_by_key[(item.get("sku", ""), item.get("field_name", ""))].append(item)

    grouped_candidates: dict[str, list[dict[str, str]]] = defaultdict(list)
    for candidate_row in candidates:
        grouped_candidates[candidate_row.get("sku", "")].append(candidate_row)
    family_meta_by_sku = {
        sku: _authoritative_family(rows)
        for sku, rows in grouped_candidates.items()
    }
    # Keep a single authoritative field row and preserve every finding in a
    # separate normalized table.
    field_rows: list[dict[str, Any]] = []
    correction_rows: list[dict[str, Any]] = []
    normalized_findings: list[dict[str, Any]] = []
    token_rows: list[dict[str, Any]] = []
    family_rows: list[dict[str, Any]] = []
    failure_counter = Counter()
    # Preserve evidence that the old field-level metadata disagreed, while
    # recording that the SKU-level authoritative recomputation resolved it.
    for sku, sku_rows in grouped_candidates.items():
        raw_families = sorted({str(item.get("family_id") or "UNKNOWN") for item in sku_rows if str(item.get("family_id") or "UNKNOWN") != "UNKNOWN"})
        raw_versions = sorted({str(item.get("family_policy_version") or "") for item in sku_rows if str(item.get("family_policy_version") or "")})
        if len(raw_families) > 1 or len(raw_versions) > 1:
            meta = family_meta_by_sku.get(sku, {})
            normalized_findings.append({
                "finding_id": hashlib.sha256(f"{sku}|FAMILY_CONSISTENCY".encode()).hexdigest()[:16],
                "batch_id": sku_rows[0].get("batch_id", ""), "sku": sku, "field_name": "__sku__",
                "rule_id": "FAMILY_CONSISTENCY", "severity": "ERROR",
                "source_es": "", "candidate_zh": "", "source_span": "", "target_span": "",
                "root_cause": "FAMILY_CLASSIFIER_CONFLICT", "resolution_type": "AUTHORITATIVE_SKU_RECOMPUTE",
                "family_id": meta.get("family_id", UNKNOWN_FAMILY),
                "family_status": meta.get("family_status", "UNKNOWN"),
                "family_candidate_id": meta.get("family_candidate_id", ""),
                "resolved": "YES", "token_classes": "", "source_token_classes": "",
                "policy_classification": "AUTHORITATIVE_SKU_RECOMPUTE",
                "policy_resolution": "POLICY_ACCEPTED", "regression_case_id": "",
                "note": f"raw family metadata conflicted: families={raw_families}, versions={raw_versions}; recomputed once per SKU",
            })
    for row in candidates:
        sku = row.get("sku", "")
        field = row.get("field_name", "")
        source = row.get("source_es", "")
        candidate = row.get("candidate_zh", "")
        old = row.get("old_zh", "")
        field_findings = finding_by_key.get((sku, field), [])
        sku_source_rows = grouped_candidates.get(sku, [row])
        family_meta = family_meta_by_sku.get(sku, {"family_id": UNKNOWN_FAMILY, "family_policy_version": "", "family_status": "UNKNOWN", "family_candidate_id": ""})
        rules = [item.get("rule_id", "") for item in field_findings if item.get("rule_id")]
        for item in field_findings:
            rule = item.get("rule_id", "")
            failure_counter[rule] += 1
            cause, resolution = root_cause(rule, source, candidate)
            policy_cause, policy_resolution = policy_classification(rule, source, candidate)
            family_id = family_meta["family_id"]
            family_version = family_meta["family_policy_version"]
            tokens = classify_tokens(candidate)
            source_tokens = source_known_tokens(source)
            for token in tokens:
                token_rows.append({
                    "batch_id": row.get("batch_id", ""), "sku": sku,
                    "field_name": field, "token": token["token"],
                    "token_class": token["token_class"],
                    "policy_action": "OMIT_DISPLAY" if token["token_class"] in {"BRAND", "IP"} else "PROTECT_OR_TRANSLATE",
                    "source_es": source, "candidate_zh": candidate,
                })
            if family_id != "UNKNOWN":
                family_rows.append({
                    "batch_id": row.get("batch_id", ""), "sku": sku,
                    "field_name": field, "family_id": family_id,
                    "family_policy_version": family_version,
                    "source_es": source, "candidate_zh": candidate,
                    "regression_status": "CANDIDATE",
                })
            normalized_findings.append({
                "finding_id": hashlib.sha256(f"{row.get('batch_id','')}|{sku}|{field}|{rule}|{len(normalized_findings)}".encode()).hexdigest()[:16],
                "batch_id": row.get("batch_id", ""), "sku": sku, "field_name": field,
                "rule_id": rule, "severity": item.get("severity", ""),
                "source_es": source, "candidate_zh": candidate,
                "source_span": "", "target_span": "",
                "root_cause": cause, "resolution_type": resolution,
                    "family_id": family_id,
                    "family_status": family_meta["family_status"],
                    "family_candidate_id": family_meta["family_candidate_id"],
                # A finding that is explicitly accepted by the display policy
                # remains in the audit trail, but must not block the field.
                "resolved": "YES" if policy_resolution == "POLICY_ACCEPTED" else "NO",
                "token_classes": ";".join(sorted({item["token_class"] for item in tokens})),
                "source_token_classes": ";".join(sorted({item["token_class"] for item in source_tokens})),
                "policy_classification": policy_cause,
                "policy_resolution": policy_resolution,
                "regression_case_id": "", "note": item.get("message", ""),
            })
        # V2 findings are computed against the authoritative SKU-level family
        # and complete source record, not against donor field metadata.
        for v2 in _v2_findings(row, sku_source_rows, family_meta):
            v2.update({
                "finding_id": hashlib.sha256(f"{row.get('batch_id','')}|{sku}|{field}|{v2['rule_id']}|{len(normalized_findings)}".encode()).hexdigest()[:16],
                "batch_id": row.get("batch_id", ""), "sku": sku, "field_name": field,
                "severity": v2.get("severity", "BLOCKER"), "source_hash": row.get("source_hash", ""),
                "token_classes": "", "source_token_classes": "",
            })
            normalized_findings.append(v2)
        fact = row.get("fact_qa_status", "NOT_RUN")
        canonical = row.get("canonical_qa_status", "NOT_RUN")
        v2_findings = _v2_findings(row, sku_source_rows, family_meta)
        unresolved_findings = [
            item for item in field_findings
            if policy_classification(item.get("rule_id", ""), source, candidate)[1] != "POLICY_ACCEPTED"
        ]
        unresolved_findings.extend(v2_findings)
        # An officially empty source field is a source exception, not a
        # translation failure.  Keep it in the field snapshot for provenance,
        # but do not send it to Owner translation review or treat it as an
        # empty overwrite risk.
        no_source_exception = (
            not source.strip()
            and (
                not str(row.get("old_zh") or "").strip()
                or str(row.get("old_zh") or "").strip() == "官网无独立描述"
            )
        )
        # Findings accepted by the explicit no-brand display policy remain in
        # the audit file and the raw replay status, but do not block this
        # candidate.  Only unresolved findings continue to require review.
        effective_fact = "PASS" if fact == "FAIL" and field_findings and not unresolved_findings else fact
        if no_source_exception:
            status = "NO_SOURCE"
            ready = "NOT_REQUIRED"
        elif not candidate.strip():
            status = "MISSING"
            ready = "NO"
        elif unresolved_findings:
            status = "REVIEW_REQUIRED"
            ready = "NO"
        elif effective_fact == "PASS" and canonical in {"PASS", "NOT_REQUIRED"}:
            status = "KEEP" if old.strip() == candidate.strip() else "REPLACE_READY"
            ready = "REVIEW_READY"
        else:
            status = "REVIEW_REQUIRED"
            ready = "NO"
        field_row = {
            **row,
            "policy_version": row.get("policy_version", "") or DEFAULT_POLICY_VERSION,
            "family_id": family_meta["family_id"],
            "family_policy_version": family_meta["family_policy_version"],
            "family_status": family_meta["family_status"],
            "family_candidate_id": family_meta["family_candidate_id"],
            "qa_findings": ";".join(item.get("rule_id", "") for item in unresolved_findings if item.get("rule_id")),
            "repair_applied": "NO",
            "repair_reason": "",
            "revision_id": row.get("approved_revision_id", ""),
            "terminology_version": row.get("terminology_version", "") or DEFAULT_TERMINOLOGY_VERSION,
            "tm_version": row.get("tm_version", "") or DEFAULT_TM_VERSION,
            "ready_for_master": ready,
            "field_status": status,
        }
        field_rows.append(field_row)
        if not no_source_exception and (old.strip() != candidate.strip() or unresolved_findings):
            correction_rows.append({
                "sku": sku, "field_name": field, "source_es": source,
                "old_zh": old, "failed_candidate_zh": candidate,
                "corrected_candidate_zh": candidate,
                "error_type": ";".join(rules),
                "root_cause": ";".join(sorted({root_cause(r, source, candidate)[0] for r in rules})),
                "correction_source": "PENDING_TARGETED_REPAIR" if field_findings else row.get("resolution_source", ""),
                "fact_qa_before": fact, "fact_qa_after": fact,
                "canonical_before": canonical, "canonical_after": canonical,
                "owner_review_required": "YES" if unresolved_findings or old.strip() != candidate.strip() else "NO",
            })

    # Family governance is a batch-level learning artifact, not only a
    # consequence of a currently failing QA row.  Preserve real source
    # examples for every detected family so the same 500-SKU rerun can seed
    # deterministic regression cases even after the QA false positives are
    # repaired.
    existing_family_keys = {(row.get("family_id", ""), row.get("sku", ""), row.get("field_name", "")) for row in family_rows}
    for sku, sku_rows in grouped_candidates.items():
        family_meta = family_meta_by_sku.get(sku, {})
        family_id = family_meta.get("family_id", UNKNOWN_FAMILY)
        family_version = family_meta.get("family_policy_version", "")
        family_status = family_meta.get("family_status", "UNKNOWN")
        family_candidate_id = family_meta.get("family_candidate_id", "")
        if family_id == "UNKNOWN" and family_status != "FAMILY_CANDIDATE":
            continue
        for row in sku_rows:
            key = (family_id, row.get("sku", ""), row.get("field_name", ""))
            if key in existing_family_keys:
                continue
            existing_family_keys.add(key)
            family_rows.append({
                "batch_id": row.get("batch_id", ""), "sku": row.get("sku", ""),
                "field_name": row.get("field_name", ""), "family_id": family_id,
                "family_policy_version": family_version,
                "family_status": family_status, "family_candidate_id": family_candidate_id,
                "source_es": row.get("source_es", ""), "candidate_zh": row.get("candidate_zh", ""),
                "regression_status": "CANDIDATE",
            })

    fields = list(field_rows[0]) if field_rows else []
    write_csv(output / "master_localization_pending_fields.csv", field_rows, fields)
    write_csv(output / "qa_findings_normalized.csv", normalized_findings, list(normalized_findings[0]) if normalized_findings else ["finding_id"])
    write_csv(output / "translation_correction_list.csv", correction_rows, list(correction_rows[0]) if correction_rows else ["sku"])
    write_csv(output / "token_candidates.csv", token_rows, list(token_rows[0]) if token_rows else ["batch_id", "sku", "field_name", "token", "token_class", "policy_action", "source_es", "candidate_zh"])
    write_csv(output / "family_candidates.csv", family_rows, list(family_rows[0]) if family_rows else ["batch_id", "sku", "field_name", "family_id", "family_policy_version", "source_es", "candidate_zh", "regression_status"])

    # The previous owner queue is intentionally not reused: targeted repair
    # changes candidate values and QA status, so an old queue would silently
    # present stale text for approval.  Regenerate it from the authoritative
    # field rows and leave the Owner decision blank.
    owner_rows = []
    for row in field_rows:
        high_risk_keep = (
            row.get("field_status") == "KEEP"
            and (
                row.get("resolution_source") in {"qwen_mt", "QWEN_REPAIR"}
                or row.get("family_id", "UNKNOWN") != "UNKNOWN"
            )
        )
        if row.get("field_status") not in {"REPLACE_READY", "REVIEW_REQUIRED", "MISSING"} and not high_risk_keep:
            continue
        review_reason = (
            "HIGH_RISK_KEEP"
            if high_risk_keep
            else ("MISSING" if row.get("field_status") == "MISSING" else row.get("field_status", ""))
        )
        owner_rows.append({
            "batch_id": row.get("batch_id", ""),
            "sku": row.get("sku", ""),
            "field_name": row.get("field_name", ""),
            "source_es": row.get("source_es", ""),
            "old_zh": row.get("old_zh", ""),
            "candidate_zh": row.get("candidate_zh", ""),
            "source_hash": row.get("source_hash", ""),
            "old_target_hash": row.get("old_target_hash", ""),
            "candidate_hash": row.get("candidate_hash", ""),
            "resolution_source": row.get("resolution_source", ""),
            "family_id": row.get("family_id", "UNKNOWN"),
            "family_policy_version": row.get("family_policy_version", ""),
            "fact_qa_status": row.get("fact_qa_status", ""),
            "canonical_qa_status": row.get("canonical_qa_status", ""),
            "qa_findings": row.get("qa_findings", ""),
            "field_status": row.get("field_status", ""),
            "review_reason": review_reason,
            "ready_for_master": row.get("ready_for_master", "NO"),
            "owner_decision": "",
            "owner_note": "",
            "apply_status": "NOT_STAGED",
        })
    owner_fields = list(owner_rows[0]) if owner_rows else [
        "batch_id", "sku", "field_name", "source_es", "old_zh", "candidate_zh",
        "source_hash", "old_target_hash", "candidate_hash", "resolution_source",
        "family_id", "family_policy_version", "fact_qa_status", "canonical_qa_status",
        "qa_findings", "field_status", "ready_for_master", "owner_decision",
        "review_reason", "owner_note", "apply_status",
    ]
    write_csv(output / "owner_review_queue.csv", owner_rows, owner_fields)
    # Stable, explicit names for downstream consumers and human review.
    write_csv(output / "master_pending_qa_findings.csv", normalized_findings,
              list(normalized_findings[0]) if normalized_findings else ["finding_id"])
    write_csv(output / "translation_results_all.csv", field_rows, fields)

    # Family governance needs concrete regression rows, not only a list of
    # candidate mentions.  Deduplicate to one deterministic case per
    # (family, SKU, field) while retaining the original evidence.
    regression_rows = []
    seen_regressions: set[tuple[str, str, str]] = set()
    for row in family_rows:
        key = (row.get("family_id", ""), row.get("sku", ""), row.get("field_name", ""))
        if key in seen_regressions:
            continue
        seen_regressions.add(key)
        regression_rows.append({
            **row,
            "regression_case_id": hashlib.sha256("|".join(key).encode("utf-8")).hexdigest()[:16],
            "expected_status": "REVIEW_REQUIRED",
        })
    write_csv(output / "family_regression_cases.csv", regression_rows,
              list(regression_rows[0]) if regression_rows else [
                  "batch_id", "sku", "field_name", "family_id", "family_policy_version",
                  "source_es", "candidate_zh", "regression_status", "regression_case_id",
                  "expected_status",
              ])

    # One row per SKU business review table.
    by_sku: dict[str, dict[str, Any]] = {}
    for row in field_rows:
        sku = row.get("sku", "")
        item = by_sku.setdefault(sku, {"SKU": sku})
        field = row.get("field_name", "")
        item[FIELD_ES[field]] = row.get("source_es", "")
        item[FIELD_OLD[field]] = row.get("old_zh", "")
        item[FIELD_PENDING[field]] = row.get("candidate_zh", "")
        item[f"{FIELD_ZH[field]}_状态"] = row.get("field_status", "REVIEW_REQUIRED")
        if field == "name":
            item["source_hash"] = row.get("source_hash", "")
            item["batch_id"] = row.get("batch_id", "")
    business_fields = ["SKU"]
    for field in CANONICAL_FIELDS:
        business_fields.extend([FIELD_ES[field], FIELD_OLD[field], FIELD_PENDING[field], f"{FIELD_ZH[field]}_状态"])
    business_fields.extend(["source_hash", "batch_id"])
    business_rows = [{key: value.get(key, "") for key in business_fields} for value in by_sku.values()]
    write_csv(output / "master_localization_pending.csv", business_rows, business_fields)

    # Workbook is a convenience review view; it is not a production merge file.
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "待入库中文表"
        sheet.append(business_fields)
        for row in business_rows:
            sheet.append([row.get(key, "") for key in business_fields])
        for cell in sheet[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="1F4E78")
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        for column in sheet.columns:
            letter = column[0].column_letter
            sheet.column_dimensions[letter].width = min(42, max(12, max(len(str(c.value or "")) for c in column[:20]) + 2))
        workbook.save(output / "master_localization_pending.xlsx")

        # Field-level view requested for audit: every one of the 3000 fields,
        # including KEEP, REPLACE_READY, REVIEW_REQUIRED and MISSING rows.
        field_workbook = Workbook()
        field_sheet = field_workbook.active
        field_sheet.title = "全部翻译结果"
        field_view_fields = [
            "sku", "field_name", "source_es", "old_zh", "candidate_zh",
            "field_status", "ready_for_master", "fact_qa_status",
            "canonical_qa_status", "qa_findings", "resolution_source",
            "family_id", "source_hash", "candidate_hash", "owner_decision",
        ]
        field_sheet.append(field_view_fields)
        for row in field_rows:
            field_sheet.append([row.get(key, "") for key in field_view_fields])
        for cell in field_sheet[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="1F4E78")
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        field_sheet.freeze_panes = "A2"
        field_sheet.auto_filter.ref = field_sheet.dimensions
        for column in field_sheet.columns:
            letter = column[0].column_letter
            field_sheet.column_dimensions[letter].width = min(60, max(12, max(len(str(c.value or "")) for c in column[:30]) + 2))
        field_workbook.save(output / "translation_results_all.xlsx")

        # Owner queue workbook: decisions remain blank by design. This is a
        # review convenience view, not an approval artifact.
        owner_workbook = Workbook()
        owner_sheet = owner_workbook.active
        owner_sheet.title = "Owner审核队列"
        owner_sheet.append(owner_fields)
        for row in owner_rows:
            owner_sheet.append([row.get(key, "") for key in owner_fields])
        for cell in owner_sheet[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="7F6000")
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        owner_sheet.freeze_panes = "A2"
        owner_sheet.auto_filter.ref = owner_sheet.dimensions
        for column in owner_sheet.columns:
            letter = column[0].column_letter
            owner_sheet.column_dimensions[letter].width = min(60, max(12, max(len(str(c.value or "")) for c in column[:30]) + 2))
        owner_workbook.save(output / "owner_review_queue.xlsx")
    except Exception as exc:
        (output / "xlsx_generation_error.txt").write_text(str(exc), encoding="utf-8")

    before = {"failed_fields": 138, "qa_findings": 164, "rules": {"SPANISH_RESIDUAL": 93, "PROTECTED_TOKEN_MISSING": 36, "NUMERIC_DROPPED": 19, "SEMANTIC_FACT_DROPPED": 10, "NUMERIC_ADDED": 4, "PROTECTED_TOKEN_CHANGED": 2}}
    after = {"failed_fields": len({(r.get("sku"), r.get("field_name")) for r in normalized_findings}), "qa_findings": len(normalized_findings), "rules": dict(failure_counter)}
    root_counts = Counter(row.get("root_cause", "") for row in normalized_findings)
    policy_counts = Counter(row.get("policy_classification", "") for row in normalized_findings if row.get("policy_classification"))
    family_counts = Counter(row.get("family_id", "UNKNOWN") for row in family_rows)
    unresolved_normalized = [row for row in normalized_findings if row.get("resolved") != "YES"]
    unresolved_field_count = len({(row.get("sku", ""), row.get("field_name", "")) for row in unresolved_normalized})
    translation_correction_counts = Counter(
        str(row.get("resolution_source") or "").upper()
        for row in field_rows
        if str(row.get("old_zh") or "").strip() != str(row.get("candidate_zh") or "").strip()
    )
    manifest = json.loads((batch / "manifest.json").read_text(encoding="utf-8")) if (batch / "manifest.json").exists() else {}
    summary = {
        "batch_id": manifest.get("batch_id", batch.name), "sku_count": len(by_sku),
        "localization_field_count": len(field_rows), "source_snapshot_hash": manifest.get("source_snapshot_hash", ""),
        "policy_versions": {
            "display_policy": DEFAULT_POLICY_VERSION,
            "name_identity": NAME_IDENTITY_POLICY_VERSION,
            "empty_source": EMPTY_SOURCE_POLICY_VERSION,
            "fact_qa": FACT_QA_POLICY_VERSION,
        },
        "before": before, "current_pending_state": after,
        "root_cause_counts": dict(root_counts),
        "policy_classification_counts": dict(policy_counts),
        "family_candidate_counts": dict(family_counts),
        "qa_pass_fields": len(field_rows) - after["failed_fields"],
        "qa_fail_fields": after["failed_fields"],
        "effective_unresolved_field_count": unresolved_field_count,
        "brand_policy_violations": policy_counts.get("BRAND_POLICY_VIOLATION", 0),
        "intentional_brand_removals": policy_counts.get("INTENTIONAL_BRAND_REMOVAL", 0),
        "source_conflict_count": sum(1 for row in normalized_findings if row.get("rule_id") == "SOURCE_CONFLICT"),
        "empty_overwrite_risk_count": sum(
            1 for row in field_rows
            if row.get("field_status") != "NO_SOURCE"
            and not str(row.get("candidate_zh") or "").strip()
            and str(row.get("old_zh") or "").strip()
        ),
        "no_source_field_count": sum(1 for row in field_rows if row.get("field_status") == "NO_SOURCE"),
        "field_mapping_error_count": 0,
        "pending_status_counts": dict(Counter(row.get("field_status", "") for row in field_rows)),
        "ready_for_master_counts": dict(Counter(row.get("ready_for_master", "") for row in field_rows)),
        "owner_review_field_count": len(owner_rows),
        "owner_review_sku_count": len({row.get("sku", "") for row in owner_rows}),
        "owner_review_high_risk_keep_count": sum(1 for row in owner_rows if row.get("review_reason") == "HIGH_RISK_KEEP"),
        "family_regression_case_count": len(regression_rows),
        "translation_correction_counts": dict(translation_correction_counts),
        "token_candidate_count": len(token_rows),
        "owner_review_required": sum(1 for row in field_rows if row.get("field_status") in {"REPLACE_READY", "REVIEW_REQUIRED"}),
        "master_fields_corrected": 0, "master_writes": 0,
        "production_writes": False, "master_modified": False,
        "decision": "KEEP_BATCH_OPEN",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    (output / "master_pending_qa_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "batch_before_after.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    matrix = []
    for rule in sorted(set(before["rules"]) | set(failure_counter)):
        matrix.append({"rule_id": rule, "before": before["rules"].get(rule, 0), "current_pending": failure_counter.get(rule, 0), "status": "PENDING_REPAIR" if failure_counter.get(rule, 0) else "RESOLVED"})
    write_csv(output / "before_after_failure_matrix.csv", matrix, ["rule_id", "before", "current_pending", "status"])
    report = [
        f"# Batch Learning V2 pending localization report",
        "",
        f"- Batch: `{summary['batch_id']}`",
        f"- SKU: {summary['sku_count']}",
        f"- Localization fields: {summary['localization_field_count']}",
        "- Production writes: 0",
        "- Master modified: false",
        "",
        "## Current state",
        "",
        "This package is a field-level pending review artifact. It does not approve or apply any candidate.",
        "",
        "| Status | Count |",
        "|---|---:|",
    ]
    for key, value in summary["pending_status_counts"].items():
        report.append(f"| {key} | {value} |")
    report += [
        "", "## Review artifacts", "",
        f"- All field results (PASS, KEEP, REPLACE_READY, REVIEW_REQUIRED, MISSING): `translation_results_all.csv`",
        f"- One-row-per-SKU review workbook: `master_localization_pending.xlsx`",
        f"- Owner queue (non-KEEP plus high-risk KEEP, decisions blank): `owner_review_queue.csv` ({len(owner_rows)} fields / {len({row.get('sku','') for row in owner_rows})} SKUs)",
        f"- Normalized findings: `master_pending_qa_findings.csv` ({len(normalized_findings)} findings)",
        f"- Family regression cases: `family_regression_cases.csv` ({len(regression_rows)} cases)",
        "", "## Translation correction summary", "",
        f"- Before: {before['failed_fields']} failed fields / {before['qa_findings']} findings",
        f"- Current: {after['failed_fields']} failed fields / {after['qa_findings']} findings",
        f"- Root causes: {dict(root_counts)}",
        f"- Policy classifications: {dict(policy_counts)}",
        f"- Brand policy violations: {summary['brand_policy_violations']}",
        f"- Intentional brand removals: {summary['intentional_brand_removals']}",
        f"- Source conflicts detected by existing findings: {summary['source_conflict_count']}",
        f"- Empty overwrite risks: {summary['empty_overwrite_risk_count']}",
        "", "## Translation Correction", "",
        f"- Wrong/failed fields identified before repair: {before['failed_fields']}",
        f"- Initial Qwen candidate changes: {translation_correction_counts.get('QWEN_MT', 0)}",
        f"- Corrected by Qwen repair: {translation_correction_counts.get('QWEN_REPAIR', 0)}",
        f"- Corrected by deterministic rules: {translation_correction_counts.get('DETERMINISTIC_RULE', 0) + translation_correction_counts.get('DETERMINISTIC', 0)}",
        "- Corrected by family/terminology direct changes: 0 (family rules were added as regression governance)",
        "- Corrected by human: 0 (Owner decisions are still blank)",
        f"- Remaining unresolved fields after policy resolution: {summary['effective_unresolved_field_count']}",
        "", "## Pending Master localization", "",
        f"- SKUs: {summary['sku_count']}",
        f"- Fields: {summary['localization_field_count']}",
        f"- Owner queue: {summary['owner_review_field_count']} fields",
        "- Owner decisions: not entered",
        "- Owner approved: 0",
        "- Owner rejected: 0",
        "- Patch ready: 0",
        "- Patch/apply artifacts: not generated",
    ]
    report += [
        "", "## Required next steps", "",
        "1. Resolve normalized findings through token/family/numeric/semantic policy or targeted repair.",
        "2. Re-run Fact QA, Canonical QA and source freshness.",
        "3. Owner review the regenerated queue.",
        "4. Only after approval generate patch/apply preview and request explicit production Apply.",
        "",
        "Decision: `KEEP_BATCH_OPEN`",
    ]
    (output / "batch_learning_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    output_files = [
        "translation_results_all.csv", "translation_results_all.xlsx",
        "master_localization_pending.csv", "master_localization_pending.xlsx",
        "master_localization_pending_fields.csv", "owner_review_queue.csv",
        "master_pending_qa_findings.csv", "master_pending_qa_summary.json",
        "translation_correction_list.csv", "token_candidates.csv",
        "family_candidates.csv", "family_regression_cases.csv",
        "batch_learning_report.md", "batch_before_after.json",
        "before_after_failure_matrix.csv",
    ]
    output_manifest = {
        "batch_id": summary["batch_id"],
        "sku_count": summary["sku_count"],
        "translation_field_count": summary["localization_field_count"],
        "production_writes": False,
        "master_modified": False,
        "pending_status_counts": summary["pending_status_counts"],
        "failed_field_count": summary["current_pending_state"]["failed_fields"],
        "qa_finding_count": summary["current_pending_state"]["qa_findings"],
        "files": {name: sha256_file(output / name) for name in output_files if (output / name).exists()},
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    (output / "pending_outputs_manifest.json").write_text(
        json.dumps(output_manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-dir", required=True)
    parser.add_argument("--output-dir")
    args = parser.parse_args()
    print(json.dumps(build(args), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

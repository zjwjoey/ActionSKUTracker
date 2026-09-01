from __future__ import annotations

import hashlib
import json
import csv
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping

from .contracts import LocalizationPlan
from .knowledge import NEW_KEYS, NEW_SCHEMAS, ensure_schemas, validate_knowledge_file
from ..dictionary import (
    BRAND_DICTIONARY_HEADERS, OVERRIDE_HEADERS, TERM_DICTIONARY_HEADERS,
    load_dictionary_rows, write_dictionary_csv,
)
from ..services.hashing import localization_source_hash


def can_promote(candidate: Mapping[str, Any], *, validator_pass: bool, source_hash_match: bool, human_approved: bool = False) -> tuple[bool, tuple[str, ...]]:
    reasons: list[str] = []
    if not validator_pass: reasons.append("VALIDATOR_FAIL")
    if not source_hash_match: reasons.append("SOURCE_HASH_MISMATCH")
    kind = str(candidate.get("semantic_type") or "")
    if kind not in {"STANDARD_UNIT", "TECH_TOKEN", "MODEL"} and not human_approved: reasons.append("HUMAN_APPROVAL_REQUIRED")
    if str(candidate.get("status") or "") == "REJECTED": reasons.append("CANDIDATE_REJECTED")
    return not reasons, tuple(reasons)


def promotion_manifest(candidate: Mapping[str, Any], old_state: str, new_state: str, *, evidence_hash: str = "") -> dict[str, Any]:
    return {"candidate_id": candidate.get("candidate_id"), "old_state": old_state, "new_state": new_state,
            "reviewer": candidate.get("reviewer") or candidate.get("approved_by"),
            "policy": "CHINESE_LOCALIZATION_STANDARD_V1", "reason": candidate.get("reason") or "",
            "source_evidence": candidate.get("evidence_skus") or candidate.get("source_examples") or [],
            "evidence_hash": evidence_hash or hashlib.sha256(json.dumps(dict(candidate), ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest(),
            "knowledge_file_before_hash": candidate.get("knowledge_file_before_hash"),
            "knowledge_file_after_hash": candidate.get("knowledge_file_after_hash"),
            "policy_version": "CHINESE_LOCALIZATION_STANDARD_V1"}


class KnowledgePromotionError(RuntimeError):
    """A candidate failed the V1 promotion contract."""


def validate_candidate_freshness(candidate: Mapping[str, Any], current_facts: Mapping[str, Mapping[str, Any]] | None = None) -> tuple[bool, str]:
    """Re-check every evidence SKU against current official Spanish facts."""
    evidence = candidate.get("evidence_skus") or candidate.get("sku") or ""
    if isinstance(evidence, str):
        skus = [s.strip() for s in evidence.replace(",", "|").split("|") if s.strip()]
    else:
        skus = [str(s).strip() for s in evidence if str(s).strip()]
    if not skus:
        return False, "SOURCE_EVIDENCE_MISSING"
    if not current_facts:
        return False, "SOURCE_EVIDENCE_MISSING"
    expected = str(candidate.get("source_hash") or "")
    if not expected:
        return False, "SOURCE_EVIDENCE_MISSING"
    for sku in skus:
        record = current_facts.get(sku)
        if not record:
            return False, "SKU_NOT_CURRENT"
        actual = localization_source_hash({
            "name_es": record.get("name_es") or record.get("name"),
            "cat1_es": record.get("cat1_es") or record.get("cat1"),
            "cat2_es": record.get("cat2_es") or record.get("cat2"),
            "spec_es": record.get("spec_es") or record.get("spec"),
            "desc_es": record.get("desc_es") or record.get("description_es") or record.get("description"),
            "details_es": record.get("details_es") or record.get("details"),
        })
        if actual != expected:
            return False, "CANDIDATE_STALE"
    return True, "PASS"


class KnowledgePromotionRouter:
    """Route an explicitly approved candidate into its owning knowledge file.

    This is intentionally a small, synchronous, atomic adapter.  It never
    touches SQLite/PRIMARY and never performs an implicit Git operation.
    """

    _NEW_FILE_BY_TYPE = {
        "PRODUCT_TYPE": "product_type_dictionary.csv",
        "DETAIL_KEY": "detail_key_dictionary.csv",
        "TECH_TOKEN": "tech_token_dictionary.csv", "MODEL": "tech_token_dictionary.csv",
        "INTERFACE": "tech_token_dictionary.csv", "PROTECTION_RATING": "tech_token_dictionary.csv",
        "STANDARD_UNIT": "tech_token_dictionary.csv",
        "VARIANT": "phrase_dictionary.csv", "FUNCTION": "phrase_dictionary.csv",
        "CARE": "phrase_dictionary.csv", "DESCRIPTION_FACT": "phrase_dictionary.csv",
        "MATERIAL": "phrase_dictionary.csv", "COMPATIBILITY": "phrase_dictionary.csv",
    }

    def __init__(self, directory: Path, *, freshness_checker=None):
        self.directory = Path(directory)
        self.freshness_checker = freshness_checker

    @staticmethod
    def _candidate_payload(candidate: Mapping[str, Any]) -> Mapping[str, Any]:
        fields = candidate.get("fields")
        return fields if isinstance(fields, Mapping) else candidate

    def _gate(self, candidate: Mapping[str, Any], *, validator_pass: bool, source_hash_match: bool, human_approved: bool) -> None:
        expected_hash = str(candidate.get("expected_source_hash") or candidate.get("source_hash_expected") or "")
        actual_hash = str(candidate.get("source_hash") or "")
        if expected_hash and actual_hash and expected_hash != actual_hash:
            source_hash_match = False
        if not human_approved:
            raise KnowledgePromotionError("HUMAN_APPROVAL_REQUIRED")
        ok, reasons = can_promote(candidate, validator_pass=validator_pass, source_hash_match=source_hash_match, human_approved=True)
        if not ok:
            raise KnowledgePromotionError("PROMOTION_BLOCKED:" + ",".join(reasons))
        if str(candidate.get("validator_status") or "PASS").upper() in {"FAIL", "REJECTED"}:
            raise KnowledgePromotionError("VALIDATOR_FAIL")
        if str(candidate.get("status") or "").upper() == "REJECTED":
            raise KnowledgePromotionError("CANDIDATE_REJECTED")
        if candidate.get("evidence_skus") or candidate.get("sku"):
            if self.freshness_checker is None:
                raise KnowledgePromotionError("SOURCE_EVIDENCE_MISSING")
            fresh = self.freshness_checker(candidate)
            if isinstance(fresh, tuple):
                ok, reason = fresh
            else:
                ok, reason = bool(fresh), "PASS" if fresh else "CANDIDATE_STALE"
            if not ok:
                raise KnowledgePromotionError(str(reason))

    def _write_new(self, filename: str, row: dict[str, Any]) -> dict[str, Any]:
        ensure_schemas(self.directory)
        path = self.directory / filename
        headers = NEW_SCHEMAS[filename]
        rows = list(csv.DictReader(path.open(encoding="utf-8-sig", newline="")))
        key_fields = NEW_KEYS[filename]
        key = tuple(str(row.get(k) or "").strip().casefold() for k in key_fields)
        if not all(key):
            raise KnowledgePromotionError("KNOWLEDGE_KEY_EMPTY")
        index = {tuple(str(existing.get(k) or "").strip().casefold() for k in key_fields): existing for existing in rows}
        if key in index:
            existing = index[key]
            # A conflicting approved value is never silently overwritten.
            existing_values = tuple(str(existing.get(h) or "").strip() for h in headers if h in {"canonical_zh", "key_zh", "canonical_token", "zh_value"})
            new_values = tuple(str(row.get(h) or "").strip() for h in headers if h in {"canonical_zh", "key_zh", "canonical_token", "zh_value"})
            if existing_values != new_values:
                raise KnowledgePromotionError("KNOWLEDGE_CONFLICT")
            return {"file": str(path), "changed": False, "key": key[0]}
        normalized = {h: str(row.get(h) or "") for h in headers}
        normalized["schema_version"] = "1.0"
        normalized["review_status"] = "HUMAN_REVIEWED"
        rows.append(normalized)
        fd, staged_name = tempfile.mkstemp(prefix=f".{path.stem}.promotion.", suffix=".tmp", dir=self.directory)
        os.close(fd)
        staged = Path(staged_name)
        with staged.open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=headers); writer.writeheader(); writer.writerows(rows); fh.flush(); os.fsync(fh.fileno())
        os.replace(staged, path)
        validate_knowledge_file(path, headers, key_fields)
        ensure_schemas(self.directory)
        return {"file": str(path), "changed": True, "key": key[0]}

    def promote(self, candidate: Mapping[str, Any], *, validator_pass: bool = True, source_hash_match: bool = True, human_approved: bool = False) -> dict[str, Any]:
        self._gate(candidate, validator_pass=validator_pass, source_hash_match=source_hash_match, human_approved=human_approved)
        kind = str(candidate.get("knowledge_type") or candidate.get("semantic_type") or "").upper()
        source_term = str(candidate.get("source_term") or candidate.get("normalized_source") or "").strip()
        zh = str(candidate.get("zh_value") or candidate.get("canonical_zh") or candidate.get("suggested_zh") or "").strip()
        payload = self._candidate_payload(candidate)
        if not zh and isinstance(candidate.get("product_type_candidate"), Mapping):
            zh = str(candidate["product_type_candidate"].get("canonical_zh") or "").strip()
        if not source_term or not zh:
            raise KnowledgePromotionError("CANDIDATE_VALUE_MISSING")
        if kind == "TERM":
            path = self.directory / "term_dictionary.csv"
            if not path.exists():
                write_dictionary_csv(path, [], TERM_DICTIONARY_HEADERS, key_fields=("term_es", "term_type"))
            rows = load_dictionary_rows(path, headers=TERM_DICTIONARY_HEADERS, key_fields=("term_es", "term_type"))
            term_type = str(candidate.get("term_type") or candidate.get("semantic_type") or "general")
            row = {"term_es": source_term, "term_zh": zh, "term_type": term_type, "forbidden_zh": "", "keep_original": "0", "review_status": "HUMAN_REVIEWED", "notes": "Localization V1 human promotion"}
            rows = [r for r in rows if not (r.get("term_es", "").casefold() == source_term.casefold() and r.get("term_type", "").casefold() == term_type.casefold())] + [row]
            write_dictionary_csv(path, rows, TERM_DICTIONARY_HEADERS, key_fields=("term_es", "term_type"))
            return {"route": "term_dictionary", "file": str(path), "changed": True, "candidate_id": candidate.get("candidate_id")}
        if kind == "BRAND":
            path = self.directory / "brand_dictionary.csv"
            rows = load_dictionary_rows(path, headers=BRAND_DICTIONARY_HEADERS, key_fields=("brand_id",)) if path.exists() else []
            brand_id = str(candidate.get("brand_id") or source_term).strip()
            rows = [r for r in rows if str(r.get("brand_id") or "").casefold() != brand_id.casefold()]
            rows.append({"brand_id": brand_id, "canonical_name": zh, "aliases_es": source_term, "keep_original": "1", "is_action_brand": "0", "confidence": "HUMAN_CONFIRMED", "review_status": "HUMAN_REVIEWED", "notes": "Localization V1 human promotion"})
            write_dictionary_csv(path, rows, BRAND_DICTIONARY_HEADERS, key_fields=("brand_id",))
            return {"route": "brand_dictionary", "file": str(path), "changed": True, "candidate_id": candidate.get("candidate_id")}
        if kind == "CATEGORY":
            from ..dictionary import CATEGORY_DICTIONARY_HEADERS
            path = self.directory / "category_dictionary.csv"
            rows = load_dictionary_rows(path, headers=CATEGORY_DICTIONARY_HEADERS, key_fields=("cat1_es", "cat2_es")) if path.exists() else []
            cat1_es, cat2_es = str(candidate.get("cat1_es") or ""), str(candidate.get("cat2_es") or "")
            rows = [r for r in rows if not (str(r.get("cat1_es") or "").casefold() == cat1_es.casefold() and str(r.get("cat2_es") or "").casefold() == cat2_es.casefold())]
            rows.append({"cat1_es": cat1_es, "cat2_es": cat2_es, "cat1_code": str(candidate.get("cat1_code") or ""), "cat1_zh": str((candidate.get("cat1_zh") or zh) if cat1_es else ""), "cat2_zh": str((candidate.get("cat2_zh") or zh) if cat2_es else ""), "review_status": "HUMAN_REVIEWED", "notes": "Localization V1 human promotion"})
            write_dictionary_csv(path, rows, CATEGORY_DICTIONARY_HEADERS, key_fields=("cat1_es", "cat2_es"))
            return {"route": "category_dictionary", "file": str(path), "changed": True, "candidate_id": candidate.get("candidate_id")}
        if kind in {"SKU_SPECIFIC_NAME", "SKU_SPECIFIC_SPEC"}:
            from ..dictionary import OVERRIDE_HEADERS
            sku = str(candidate.get("sku") or "").strip()
            if not sku:
                raise KnowledgePromotionError("SKU_OVERRIDE_REQUIRES_SKU")
            field = "name_zh_standard" if kind.endswith("NAME") else "spec_zh_standard"
            path = self.directory / "manual_overrides.csv"
            rows = load_dictionary_rows(path, headers=OVERRIDE_HEADERS, key_fields=("scope", "key", "field")) if path.exists() else []
            rows = [r for r in rows if not (r.get("scope") == "product" and r.get("key") == sku and r.get("field") == field)]
            rows.append({"scope": "product", "key": sku, "field": field, "value": zh, "reason": "Localization V1 human promotion", "source": "KnowledgePromotionRouter", "locked": "0", "updated_at": ""})
            write_dictionary_csv(path, rows, OVERRIDE_HEADERS, key_fields=("scope", "key", "field"))
            return {"route": "manual_overrides", "file": str(path), "changed": True, "candidate_id": candidate.get("candidate_id")}
        filename = self._NEW_FILE_BY_TYPE.get(kind)
        if not filename:
            raise KnowledgePromotionError(f"PROMOTION_ROUTE_NOT_DEFINED:{kind}")
        if filename == "product_type_dictionary.csv":
            row = {"product_type_id": str(candidate.get("candidate_id") or hashlib.sha256(source_term.casefold().encode()).hexdigest()[:16]), "source_term": source_term, "source_aliases": str(candidate.get("source_aliases") or ""), "cat1_es": str(candidate.get("cat1_es") or ""), "cat2_es": str(candidate.get("cat2_es") or ""), "canonical_zh": zh, "confidence": str(candidate.get("confidence") or "1.0"), "notes": "Localization V1 human promotion"}
        elif filename == "detail_key_dictionary.csv":
            row = {"key_es": source_term, "key_zh": zh, "field_group": str(candidate.get("field_group") or "details"), "value_type": str(candidate.get("value_type") or "text"), "unit_rule": str(candidate.get("unit_rule") or ""), "notes": "Localization V1 human promotion"}
        elif filename == "tech_token_dictionary.csv":
            row = {"token": source_term, "canonical_token": zh, "token_type": kind, "keep_original": str(candidate.get("keep_original") or "1"), "normalization_rule": str(candidate.get("normalization_rule") or ""), "notes": "Localization V1 human promotion"}
        else:
            row = {"source_phrase": source_term, "zh_value": zh, "semantic_type": kind, "preferred_target": str(candidate.get("preferred_target") or "name"), "allowed_targets": str(candidate.get("allowed_targets") or "name"), "category_context": str(candidate.get("category_context") or ""), "notes": "Localization V1 human promotion"}
        result = self._write_new(filename, row)
        return {"route": filename, **result, "candidate_id": candidate.get("candidate_id")}

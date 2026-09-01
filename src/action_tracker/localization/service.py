from __future__ import annotations

import csv
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from ..database.integration import database_path
from ..database.repository import ProductionRepository
from ..database.production import apply_localization_correction
from .contracts import LOCALIZATION_FIELDS
from .engine import LocalizationEngine
from .ai import provider_from_config, resolve_unknown
from .knowledge import KnowledgeLoader
from .learning import aggregate_candidates


def _report_root(cfg: Mapping[str, Any]) -> Path:
    return Path(cfg["paths"]["temp"]).parent / "localization" / "reports"


def _existing_zh(db_path: Path) -> dict[str, dict[str, Any]]:
    from ..database.connection import connect
    with connect(db_path) as db:
        rows = db.execute("SELECT official_sku,name,cat1,cat2,spec,unit_price,description,details,source_hash,freshness_status,review_status,resolution_status,name_source,cat1_source,cat2_source,spec_source,unit_price_source,description_source,details_source,approved_by,approved_at,last_commit_id,applied_commit_id FROM product_localizations WHERE language='zh'").fetchall()
    return {str(row[0]): {"name_zh": row[1], "cat1_zh": row[2], "cat2_zh": row[3], "spec_zh": row[4], "unit_price_zh": row[5], "desc_zh": row[6], "details_zh": row[7], "source_hash": row[8], "freshness_status": row[9], "review_status": row[10], "resolution_status": row[11], "zh_name_source": row[12], "zh_cat1_source": row[13], "zh_cat2_source": row[14], "zh_spec_source": row[15], "zh_unit_price_source": row[16], "zh_description_source": row[17], "zh_details_source": row[18], "approved_by": row[19], "approved_at": row[20], "last_commit_id": row[21], "applied_commit_id": row[22]} for row in rows}


def audit_current(cfg: Mapping[str, Any], *, run_id: str | None = None, records: list[dict[str, Any]] | None = None, persist_reviews: bool = False) -> dict[str, Any]:
    """Resolve/validate every PRIMARY CURRENT SKU without writing production data."""
    db_path = database_path(dict(cfg))
    records = records if records is not None else ProductionRepository(db_path).load_current_export_records()
    directory = Path(cfg["paths"].get("dictionary_baseline") or Path(cfg["project_root"]) / "data" / "dictionary")
    knowledge = KnowledgeLoader(directory).load()
    engine = LocalizationEngine(knowledge=knowledge)
    existing = _existing_zh(db_path) if db_path.exists() else {}
    rows: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    unknown_plans: list[tuple[dict[str, Any], Any, LocalizationEngine]] = []
    for record in records:
        sku = str(record.get("sku") or record.get("official_sku") or "")
        product = knowledge.get("product_by_sku", {}).get(sku, {})
        per_record_knowledge = dict(knowledge)
        # Product dictionary values are field-level knowledge and are never
        # allowed to overwrite official Spanish facts.
        per_record_knowledge.update({
            "name_zh": product.get("name_zh_standard") or product.get("name_zh") or "",
            "cat1_zh": product.get("cat1_zh_standard") or product.get("cat1_zh") or "",
            "cat2_zh": product.get("cat2_zh_standard") or product.get("cat2_zh") or "",
            "spec_zh": product.get("spec_zh_standard") or product.get("spec_zh") or "",
            "desc_zh": product.get("desc_zh") or product.get("description_zh") or "",
            "details_zh": product.get("details_zh") or product.get("details") or "",
        })
        plan = LocalizationEngine(knowledge=per_record_knowledge).resolve(record, existing=existing.get(sku))
        engine_for_record = LocalizationEngine(knowledge=per_record_knowledge)
        validation = engine_for_record.validate(record, plan)
        if not validation.ok:
            unknown_plans.append((record, plan, engine_for_record))
        old = existing.get(sku, {})
        row = {"sku": sku, "source_hash": plan.source_hash,
               "old_name_zh": old.get("name_zh", ""), "new_name_zh": plan.fields["name_zh"].value,
               "old_spec_zh": old.get("spec_zh", ""), "new_spec_zh": plan.fields["spec_zh"].value,
               "old_cat1_zh": old.get("cat1_zh", ""), "new_cat1_zh": plan.fields["cat1_zh"].value,
               "old_cat2_zh": old.get("cat2_zh", ""), "new_cat2_zh": plan.fields["cat2_zh"].value,
               "old_desc_zh": old.get("desc_zh", ""), "new_desc_zh": plan.fields["desc_zh"].value,
               "old_details_zh": old.get("details_zh", ""), "new_details_zh": plan.fields["details_zh"].value,
               "old_unit_price_zh": old.get("unit_price_zh", record.get("unit_price", "")), "new_unit_price_zh": plan.fields["unit_price_zh"].value,
               "old_freshness_status": old.get("freshness_status", ""), "old_review_status": old.get("review_status", ""),
               # Validation is the final gate; a planner AUTO_READY result is
               # never allowed to mask residual Spanish or numeric failures.
               "readiness": "READY" if validation.ok else "REVIEW_REQUIRED",
               "review_reasons": "|".join(validation.reasons),
               "spanish_residue_tokens": "|".join(validation.spanish_residue_tokens),
               "numeric_validation": "PASS" if not validation.numeric_mismatches else "FAIL",
               "knowledge_hits": "|".join(plan.knowledge_hits), "ai_used": plan.ai_used,
               "source_run_id": str(record.get("source_run_id") or ""),
               "source_commit_id": str(record.get("source_commit_id") or "")}
        rows.append(row)
        for fact in plan.semantic_facts:
            if fact.semantic_type in {"PRODUCT_TYPE", "TECH_TOKEN", "STANDARD_UNIT", "DETAIL_KEY"}:
                candidates.append({"sku": sku, "semantic_type": fact.semantic_type, "knowledge_type": fact.semantic_type, "source_term": fact.source_text, "zh_value": fact.value, "source_hash": plan.source_hash, "source_run_id": record.get("source_run_id", ""), "source_commit_id": record.get("source_commit_id", ""), "provider": "deterministic", "validator_status": "PASS", "review_status": "PENDING"})
    date_key = run_id or datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    out = _report_root(cfg) / date_key
    out.mkdir(parents=True, exist_ok=True)
    audit_path = out / "localization_audit.csv"
    headers = list(rows[0].keys()) if rows else ["sku", "source_hash"]
    with audit_path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=headers); writer.writeheader(); writer.writerows(rows)
    review_rows = _review_rows(rows, run_id or date_key)
    review_path = out / "review_queue.csv"
    review_headers = ["review_id", "issue_type", "sku", "field", "current_value", "suggested_value", "evidence", "reason", "created_at", "status", "source", "updated_at", "resolution"]
    with review_path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=review_headers); writer.writeheader(); writer.writerows(review_rows)
    if persist_reviews and cfg.get("paths", {}).get("review_queue"):
        from ..review_queue import _write_queue, load_queue
        queue = load_queue(dict(cfg))
        queue.update({row["review_id"]: row for row in review_rows})
        _write_queue(dict(cfg), [queue[key] for key in sorted(queue)])
    # Deterministic facts and AI-validated unknowns share one learning pool.
    # AI candidates are still review-only; this merge only creates evidence.
    learning = aggregate_candidates(candidates, out)
    ai_candidates: list[dict[str, Any]] = []
    ai_config = ((cfg.get("localization") or {}).get("ai") or {})
    provider = provider_from_config(ai_config)
    if bool(ai_config.get("enabled")):
        for record, plan, record_engine in unknown_plans:
            # Do not spend an AI call on a purely stale/metadata-only row.
            # AI is reserved for explicit unknown semantic or translation
            # work; the ordinary daily path keeps the old value stale.
            eligible_reasons = {"PRODUCT_TYPE_REVIEW", "SERIES_REVIEW", "TECH_TOKEN_REVIEW", "DETAIL_KEY_REVIEW", "DETAIL_VALUE_REVIEW", "NAME_REVIEW", "CATEGORY_REVIEW", "DESCRIPTION_REVIEW", "SPANISH_RESIDUAL"}
            if not (set(plan.review_reasons) & eligible_reasons):
                continue
            try:
                candidate = resolve_unknown(record_engine, record, plan, provider)
            except Exception as exc:
                candidate = {"sku": str(record.get("sku") or ""), "status": "FAILED", "failure_reason": type(exc).__name__}
            if candidate:
                ai_candidates.append(candidate)
    ai_learning_rows: list[dict[str, Any]] = []
    for candidate in ai_candidates:
        if str(candidate.get("schema_status") or "FAIL") != "PASS":
            continue
        base = {k: candidate.get(k, "") for k in ("provider", "model", "prompt_version", "policy_version", "source_hash", "source_run_id", "source_commit_id", "request_hash", "response_hash", "confidence")}
        for item in candidate.get("semantic_items") or ():
            if isinstance(item, Mapping):
                ai_learning_rows.append({**base, **item, "validator_status": "PASS", "review_status": "PENDING", "status": "AI_CANDIDATE"})
        pt = candidate.get("product_type_candidate")
        if isinstance(pt, Mapping):
            ai_learning_rows.append({**base, "semantic_type": "PRODUCT_TYPE", "knowledge_type": "PRODUCT_TYPE", "source_term": pt.get("source_term"), "zh_value": pt.get("canonical_zh") or pt.get("zh_value"), "validator_status": "PASS", "review_status": "PENDING", "status": "AI_CANDIDATE"})
        for key, semantic_type in (("detail_key_candidates", "DETAIL_KEY"), ("tech_token_candidates", "TECH_TOKEN")):
            for item in candidate.get(key) or ():
                if isinstance(item, Mapping):
                    ai_learning_rows.append({**base, **item, "semantic_type": semantic_type, "knowledge_type": semantic_type, "source_term": item.get("source_term") or item.get("key_es") or item.get("token"), "zh_value": item.get("zh_value") or item.get("canonical_zh") or item.get("canonical_token"), "validator_status": "PASS", "review_status": "PENDING", "status": "AI_CANDIDATE"})
    if ai_learning_rows:
        learning = aggregate_candidates([*candidates, *ai_learning_rows], out)
    ai_path = out / "ai_candidates.json"
    ai_path.write_text(json.dumps(ai_candidates, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    # Keep the durable learning pool separate from per-run reports while
    # retaining the report-local copy required for reproducibility.
    learning_pool = Path(cfg["paths"]["temp"]).parent / "localization" / "learning_candidates" / date_key
    learning_pool.mkdir(parents=True, exist_ok=True)
    shutil.copy2(learning["path"], learning_pool / "learning_candidates.csv")
    ready = sum(row["readiness"] in {"READY", "AUTO_READY"} for row in rows)
    coverage = {"run_id": run_id, "total_current_skus": len(rows), "ready_count": ready, "review_required_count": len(rows)-ready,
                "ordinary_spanish_residue_count": sum(bool(row["spanish_residue_tokens"]) for row in rows),
                "numeric_mismatch_count": sum(row["numeric_validation"] == "FAIL" for row in rows),
                "fact_not_covered_count": sum("FACT_NOT_COVERED" in str(row.get("review_reasons") or "").split("|") for row in rows),
                "knowledge_hit_count": sum(bool(row["knowledge_hits"]) for row in rows), "ai_call_count": getattr(provider, "calls", 0),
                "ai_candidate_count": len(ai_candidates), "ai_avoidance_rate": 1.0 - (getattr(provider, "calls", 0) / max(1, len(rows))), "generated_at": datetime.now(timezone.utc).isoformat()}
    (out / "coverage.json").write_text(json.dumps(coverage, ensure_ascii=False, indent=2), encoding="utf-8")
    source_commit_id = None
    if db_path.exists():
        try:
            source_commit_id = ProductionRepository(db_path).current_head()
        except Exception:
            source_commit_id = None
    manifest = {"run_id": run_id, "report_dir": str(out), "audit": str(audit_path), "review_queue": str(review_path), "learning_candidates": learning["path"], "ai_candidates": str(ai_path), "coverage": str(out / "coverage.json"), "source_commit_id": source_commit_id, "generated_at": datetime.now(timezone.utc).isoformat()}
    manifest["manifest_hash"] = hashlib.sha256(json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {**manifest, **coverage}


def _review_rows(audit_rows: list[dict[str, Any]], run_id: str) -> list[dict[str, str]]:
    """Turn validator reasons into the existing unified review schema."""
    import hashlib
    mapping = {
        "NAME_REVIEW": ("NAME_REVIEW", "name_zh"), "CATEGORY_REVIEW": ("CATEGORY_REVIEW", "cat1_zh"),
        "SPEC_FORMAT_REVIEW": ("SPEC_FORMAT_REVIEW", "spec_zh"), "DESCRIPTION_REVIEW": ("DESCRIPTION_REVIEW", "desc_zh"),
        "DETAIL_VALUE_REVIEW": ("DETAIL_VALUE_REVIEW", "details_zh"), "SPANISH_RESIDUAL": ("SPANISH_RESIDUAL", ""),
        "NUMERIC_FACT_MISMATCH": ("NUMERIC_FACT_MISMATCH", ""), "SOURCE_HASH_CHANGED": ("SOURCE_HASH_CHANGED", "source_hash"),
        "SOURCE_HASH_MISMATCH": ("SOURCE_HASH_MISMATCH", "source_hash"), "DETAIL_SKU_MISMATCH": ("DETAIL_SKU_MISMATCH", "details_zh"),
        "STALE_LOCALIZATION": ("STALE_LOCALIZATION", ""), "PRODUCT_TYPE_REVIEW": ("PRODUCT_TYPE_REVIEW", "name_zh"),
        "DETAIL_KEY_REVIEW": ("DETAIL_KEY_REVIEW", "details_zh"), "TECH_TOKEN_REVIEW": ("TECH_TOKEN_REVIEW", "spec_zh"),
        "PRICE_FACT_MISMATCH": ("PRICE_FACT_MISMATCH", "unit_price_zh"),
    }
    rows = []
    for row in audit_rows:
        for reason in filter(None, str(row.get("review_reasons") or "").split("|")):
            issue_type, field = mapping.get(reason, (reason if reason.endswith("_REVIEW") else "DATA_INCONSISTENCY", ""))
            seed = "|".join((issue_type, str(row.get("sku") or ""), field, str(row.get("source_hash") or "")))
            rid = hashlib.sha256(seed.encode()).hexdigest()[:24]
            rows.append({"review_id": rid, "issue_type": issue_type, "sku": str(row.get("sku") or ""), "field": field,
                         "current_value": str(row.get("old_" + field) or "") if field else "", "suggested_value": str(row.get("new_" + field) or "") if field else "",
                         "evidence": str(row.get("source_hash") or ""), "reason": reason, "created_at": datetime.now(timezone.utc).isoformat(),
                         "status": "PENDING", "source": "LOCALIZATION_ENGINE", "updated_at": datetime.now(timezone.utc).isoformat(), "resolution": ""})
    return rows


def apply_from_audit(cfg: Mapping[str, Any], *, run_id: str, commit: bool = False) -> dict[str, Any]:
    result = audit_current(cfg, run_id=run_id)
    if not commit:
        result["formal_apply"] = False
        return result
    enabled = bool((cfg.get("knowledge") or {}).get("production_apply_enabled"))
    if not enabled:
        raise PermissionError("LOCALIZATION_PRODUCTION_APPLY_DISABLED")
    db_path = database_path(dict(cfg))
    candidates: dict[str, dict[str, Any]] = {}
    source_hashes: dict[str, str] = {}
    for row in csv.DictReader(Path(result["audit"]).open(encoding="utf-8-sig")):
        # Human/LOCKED localizations are immutable to automatic enrichment.
        if row.get("readiness") in {"READY", "AUTO_READY"} and str(row.get("old_review_status") or "").upper() not in {"LOCKED", "HUMAN_APPROVED", "APPROVED"}:
            candidates[row["sku"]] = {"name": row["new_name_zh"], "cat1": row["new_cat1_zh"], "cat2": row["new_cat2_zh"], "spec": row["new_spec_zh"], "unit_price": row["new_unit_price_zh"], "description": row["new_desc_zh"], "details": row["new_details_zh"]}
            source_hashes[row["sku"]] = row["source_hash"]
    applied = apply_localization_correction(db_path, run_id=run_id, localizations_by_sku=candidates, source_hashes=source_hashes)
    result.update({"formal_apply": True, "applied": applied.get("applied_skus", 0), "correction_commit": applied})
    return result

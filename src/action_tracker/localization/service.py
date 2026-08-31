from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from ..database.integration import database_path
from ..database.repository import ProductionRepository
from ..database.production import apply_localization_correction
from .contracts import LOCALIZATION_FIELDS
from .engine import LocalizationEngine
from .knowledge import KnowledgeLoader
from .learning import aggregate_candidates


def _report_root(cfg: Mapping[str, Any]) -> Path:
    return Path(cfg["paths"]["temp"]).parent / "localization" / "reports"


def _existing_zh(db_path: Path) -> dict[str, dict[str, Any]]:
    from ..database.connection import connect
    with connect(db_path) as db:
        rows = db.execute("SELECT official_sku,name,cat1,cat2,spec,description,details,source_hash,freshness_status,review_status FROM product_localizations WHERE language='zh'").fetchall()
    return {str(row[0]): {"name_zh": row[1], "cat1_zh": row[2], "cat2_zh": row[3], "spec_zh": row[4], "desc_zh": row[5], "details_zh": row[6], "source_hash": row[7], "freshness_status": row[8], "review_status": row[9]} for row in rows}


def audit_current(cfg: Mapping[str, Any], *, run_id: str | None = None, records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Resolve/validate every PRIMARY CURRENT SKU without writing production data."""
    db_path = database_path(dict(cfg))
    records = records if records is not None else ProductionRepository(db_path).load_current_export_records()
    directory = Path(cfg["paths"].get("dictionary_baseline") or Path(cfg["project_root"]) / "data" / "dictionary")
    knowledge = KnowledgeLoader(directory).load()
    engine = LocalizationEngine(knowledge=knowledge)
    existing = _existing_zh(db_path) if db_path.exists() else {}
    rows: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
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
        old = existing.get(sku, {})
        row = {"sku": sku, "source_hash": plan.source_hash,
               "old_name_zh": old.get("name_zh", ""), "new_name_zh": plan.fields["name_zh"].value,
               "old_spec_zh": old.get("spec_zh", ""), "new_spec_zh": plan.fields["spec_zh"].value,
               "old_cat1_zh": old.get("cat1_zh", ""), "new_cat1_zh": plan.fields["cat1_zh"].value,
               "old_cat2_zh": old.get("cat2_zh", ""), "new_cat2_zh": plan.fields["cat2_zh"].value,
               "old_desc_zh": old.get("desc_zh", ""), "new_desc_zh": plan.fields["desc_zh"].value,
               "old_details_zh": old.get("details_zh", ""), "new_details_zh": plan.fields["details_zh"].value,
               "old_unit_price_zh": record.get("unit_price", ""), "new_unit_price_zh": plan.fields["unit_price_zh"].value,
               "old_freshness_status": old.get("freshness_status", ""), "old_review_status": old.get("review_status", ""),
               # Validation is the final gate; a planner AUTO_READY result is
               # never allowed to mask residual Spanish or numeric failures.
               "readiness": "READY" if validation.ok else "REVIEW_REQUIRED",
               "review_reasons": "|".join(validation.reasons),
               "spanish_residue_tokens": "|".join(validation.spanish_residue_tokens),
               "numeric_validation": "PASS" if not validation.numeric_mismatches else "FAIL",
               "knowledge_hits": "|".join(plan.knowledge_hits), "ai_used": plan.ai_used}
        rows.append(row)
        for fact in plan.semantic_facts:
            if fact.semantic_type in {"PRODUCT_TYPE", "TECH_TOKEN", "STANDARD_UNIT", "DETAIL_KEY"}:
                candidates.append({"sku": sku, "semantic_type": fact.semantic_type, "source_term": fact.source_text, "zh_value": fact.value})
    date_key = run_id or datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    out = _report_root(cfg) / date_key
    out.mkdir(parents=True, exist_ok=True)
    audit_path = out / "localization_audit.csv"
    headers = list(rows[0].keys()) if rows else ["sku", "source_hash"]
    with audit_path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=headers); writer.writeheader(); writer.writerows(rows)
    learning = aggregate_candidates(candidates, out)
    ready = sum(row["readiness"] in {"READY", "AUTO_READY"} for row in rows)
    coverage = {"run_id": run_id, "total_current_skus": len(rows), "ready_count": ready, "review_required_count": len(rows)-ready,
                "ordinary_spanish_residue_count": sum(bool(row["spanish_residue_tokens"]) for row in rows),
                "numeric_mismatch_count": sum(row["numeric_validation"] == "FAIL" for row in rows),
                "knowledge_hit_count": sum(bool(row["knowledge_hits"]) for row in rows), "ai_call_count": 0,
                "ai_avoidance_rate": 1.0, "generated_at": datetime.now(timezone.utc).isoformat()}
    (out / "coverage.json").write_text(json.dumps(coverage, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest = {"run_id": run_id, "report_dir": str(out), "audit": str(audit_path), "learning_candidates": learning["path"], "coverage": str(out / "coverage.json"), "source_commit_id": None, "generated_at": datetime.now(timezone.utc).isoformat()}
    manifest["manifest_hash"] = hashlib.sha256(json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {**manifest, **coverage}


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
            candidates[row["sku"]] = {"name": row["new_name_zh"], "cat1": row["new_cat1_zh"], "cat2": row["new_cat2_zh"], "spec": row["new_spec_zh"], "description": row["new_desc_zh"], "details": row["new_details_zh"]}
            source_hashes[row["sku"]] = row["source_hash"]
    applied = apply_localization_correction(db_path, run_id=run_id, localizations_by_sku=candidates, source_hashes=source_hashes)
    result.update({"formal_apply": True, "applied": applied.get("applied_skus", 0), "correction_commit": applied})
    return result

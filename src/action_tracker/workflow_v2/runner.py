"""Resumable Workflow V2 orchestration.

The runner owns stage boundaries and state only.  Existing collectors, writers,
registry and export implementations remain injectable adapters; this keeps the
development path safe for fixtures and makes the fact/translation boundary
explicit.
"""
from __future__ import annotations

import csv
import json
import os
import shutil
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable, Mapping

from .context import new_context, run_directory
from .contracts import STAGES, STAGE_DEPENDENCIES, StageResult, WorkflowBlocker, WorkflowContext, WorkflowResult
from .export_audit import audit_es, audit_parity, audit_zh, export_readiness
from .source_audit import audit_source_records, source_clean_and_reaudit


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    os.replace(temporary, path)


def _write_csv(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    values = [dict(row) for row in rows]
    fields = sorted({key for row in values for key in row}) or ["value"]
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader(); writer.writerows(values)


def _state_payload(context: WorkflowContext, stages: Mapping[str, StageResult], state: str, report: Mapping[str, Any]) -> dict[str, Any]:
    return {"workflow_run_id": context.workflow_run_id, "business_date": context.business_date,
            "context": context.as_dict(), "state": state,
            "stages": {key: {"status": value.status, "details": dict(value.details), "error_code": value.error_code,
                             "retryable": value.retryable} for key, value in stages.items()}, "report": dict(report)}


class WorkflowV2Runner:
    def __init__(self, *, root: Path, context: WorkflowContext, records: list[dict[str, Any]] | None = None,
                 expected_skus: Iterable[str] | None = None, expected_new_skus: Iterable[str] | None = None,
                 expected_reappeared_skus: Iterable[str] | None = None, provider: Any = None,
                 approved: Mapping[str, Mapping[str, str]] | None = None, auto_translation: bool = False,
                 auto_policy: bool = False, auto_export: bool = False, apply_enabled: bool = False,
                 dry_run: bool = False, production_apply: bool = False, temp_db: Path | None = None,
                 cfg: Mapping[str, Any] | None = None, detail_adapter: Any = None,
                 extraction_adapter: Any = None, allow_high_risk_auto_approval: bool = False):
        self.root = Path(root); self.context = context; self.directory = run_directory(self.root, context)
        self.records = records
        self.expected_skus = set(str(item) for item in (expected_skus or ()))
        self.expected_new_skus = set(str(item) for item in (expected_new_skus or ()))
        self.expected_reappeared_skus = set(str(item) for item in (expected_reappeared_skus or ()))
        self.provider = provider; self.approved = approved or {}
        self.auto_translation = auto_translation; self.auto_policy = auto_policy; self.auto_export = auto_export
        self.apply_enabled = apply_enabled; self.dry_run = dry_run; self.production_apply = production_apply
        self.temp_db = Path(temp_db) if temp_db else self.directory / "workflow_v2.sqlite3"
        self.cfg = dict(cfg or {})
        self.detail_adapter = detail_adapter
        self.extraction_adapter = extraction_adapter
        self.allow_high_risk_auto_approval = bool(allow_high_risk_auto_approval)
        self.context.database_path = str(self.temp_db)
        self.translation_runtime = None
        self.stages: dict[str, StageResult] = {}; self.report: dict[str, Any] = {}
        self.translation_plan: list[Any] = []; self.translation_results: list[dict[str, Any]] = []; self.policy_results: list[dict[str, Any]] = []
        self.es_projection: list[dict[str, Any]] = []; self.zh_projection: list[dict[str, Any]] = []
        self.source_readiness: dict[str, Any] = {}

    def run(self, *, resume: bool = False) -> WorkflowResult:
        state_path = self.directory / "workflow_state.json"
        if resume and not state_path.exists():
            candidates = sorted(self.root.glob(f"*/{self.context.workflow_run_id}")) if self.root.exists() else []
            candidates = [path for path in candidates if (path / "workflow_state.json").exists()]
            if not candidates:
                raise ValueError("WORKFLOW_RUN_NOT_FOUND")
            if len(candidates) > 1:
                raise ValueError("WORKFLOW_RUN_AMBIGUOUS")
            self.directory = candidates[0]
            state_path = self.directory / "workflow_state.json"
        if resume and state_path.exists():
            prior = json.loads(state_path.read_text(encoding="utf-8"))
            if prior.get("context"):
                self.context = WorkflowContext.from_dict(prior["context"])
                if self.context.database_path:
                    self.temp_db = Path(self.context.database_path)
            self.stages = {key: StageResult(value.get("status", "PASS"), value.get("details", {}), value.get("error_code"), bool(value.get("retryable"))) for key, value in (prior.get("stages") or {}).items()}
            self.report.update(prior.get("report") or {})
            self.source_readiness = self._load_json_artifact("source_readiness.json", {})
            if self.records is None and (self.directory / "records.json").exists(): self.records = json.loads((self.directory / "records.json").read_text(encoding="utf-8"))
            self._restore_artifacts()
        self._persist_state("RUN_START")
        self._stage("PREFLIGHT", self._preflight)
        self._stage("BACKUP", self._backup)
        self._stage("EXTRACT", self._extract)
        self._stage("SOURCE_AUDIT", self._source_audit)
        self._stage("SOURCE_CLEAN", self._source_clean)
        self._stage("SOURCE_REAUDIT", self._source_reaudit)
        self._stage("FACT_COMMIT", self._fact_commit)
        self._stage("DETAIL_PLAN", self._detail_plan)
        self._stage("DETAIL_ENRICH", self._detail_enrich)
        self._stage("TRANSLATION_SOURCE_AUDIT", self._translation_source_audit)
        self._stage("REGISTRY_INGEST", self._registry_ingest)
        self._stage("TRANSLATION_PLAN", self._translation_plan)
        self._stage("QWEN_TRANSLATE", self._qwen_translate)
        self._stage("TRANSLATION_QA", self._translation_qa)
        self._stage("TRANSLATION_POLICY", self._translation_policy)
        self._stage("TRANSLATION_APPLY", self._translation_apply)
        self._stage("EXPORT_AUDIT", self._export_audit)
        self._stage("EXPORT_WRITE", self._export_write)
        state = self._final_state()
        self.report.update({"Business Date": self.context.business_date, "Fact commit ID": self.context.source_commit_id,
                            "Translation queued": len(self.translation_plan), "Qwen called": self.stages.get("QWEN_TRANSLATE", StageResult()).details.get("provider_calls", 0),
                            "Translation completed": sum(1 for item in self.translation_results if item.get("status") == "PASS"),
                            "Export ready": self.context.export_ready, "presence_state": "PASS" if self.context.presence_ready else "BLOCKED",
                            "fact_state": "PASS" if self.context.fact_ready else "FACT_REFRESH_NOT_READY",
                            "translation_state": "PASS" if self.context.translation_ready else "BLOCKED",
                            "localization_state": "PASS" if self.context.localization_commit_id else "PENDING",
                            "export_state": "PASS" if self.context.export_ready else "BLOCKED",
                            "FINAL_STATUS": state})
        self._stage("REPORT", lambda: StageResult("PASS", self.report))
        payload = _state_payload(self.context, self.stages, state, self.report)
        _write_json(state_path, payload); _write_json(self.directory / "workflow_manifest.json", {"workflow_run_id": self.context.workflow_run_id, "business_date": self.context.business_date, "stages": list(STAGES)})
        _write_json(self.directory / "workflow_report.json", payload)
        return WorkflowResult(self.context, state, self.stages, self.report)

    def _stage(self, name: str, fn) -> None:
        if name != "REPORT" and name in self.stages and self.stages[name].status == "PASS": return
        dependencies = STAGE_DEPENDENCIES.get(name, ())
        failed = [dep for dep in dependencies if self.stages.get(dep) and self.stages[dep].status not in {"PASS", "SKIPPED", "NOT_REQUIRED"}]
        if failed:
            result = StageResult("BLOCKED_BY_DEPENDENCY", {"dependencies": failed}, "DEPENDENCY_BLOCKED")
            self._record_blocker(name, result)
            self.stages[name] = result
            self._persist_state(name)
            return
        self._persist_state(name, start=True)
        try: result = fn()
        except Exception as exc: result = StageResult("FAILED", {"error": str(exc)}, type(exc).__name__)
        self.stages[name] = result
        if result.status == "PASS":
            # A retry that really passes clears only the originating stage's
            # blocker.  Dependency blockers remain until their own stage is
            # retried and succeeds.
            self.context.blockers = [item for item in self.context.blockers if item.stage != name]
        if result.status in {"BLOCKED", "FAILED", "BLOCKED_BY_DEPENDENCY"}: self._record_blocker(name, result)
        self._persist_state(name)

    def _record_blocker(self, stage: str, result: StageResult) -> None:
        blocker = WorkflowBlocker(stage=stage, code=result.error_code or result.status, details=dict(result.details))
        existing = {json.dumps(item.as_dict(), sort_keys=True, default=str) for item in self.context.blockers}
        if json.dumps(blocker.as_dict(), sort_keys=True, default=str) not in existing:
            self.context.blockers.append(blocker)
        self.report.setdefault("blockers", []).append(blocker.as_dict())

    def _persist_state(self, stage: str, *, start: bool = False) -> None:
        state = "RUNNING" if start else self._final_state() if self.stages else "RUNNING"
        payload = _state_payload(self.context, self.stages, state, {**self.report, "last_stage": stage, "stage_phase": "START" if start else "END"})
        _write_json(self.directory / "workflow_state.json", payload)

    def _restore_artifacts(self) -> None:
        def load_json(name: str, default):
            path = self.directory / name
            if not path.exists(): return default
            try: return json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError): return default
        self.translation_results = load_json("translation_result.json", self.translation_results) or self.translation_results
        self.policy_results = load_json("translation_policy.json", self.policy_results) or self.policy_results
        plans = load_json("translation_plan.json", [])
        if plans:
            self.translation_plan = list(plans)

    def _load_json_artifact(self, name: str, default: Any) -> Any:
        path = self.directory / name
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return default

    def _runtime_cfg(self) -> dict[str, Any]:
        cfg = dict(self.cfg)
        cfg.setdefault("project_root", str(self.root))
        cfg.setdefault("paths", {})
        cfg.setdefault("localization", {"ai": {"enabled": False}})
        cfg.setdefault("storage", {})
        cfg["storage"] = {**dict(cfg.get("storage") or {}), "db_path": str(self.temp_db)}
        return cfg

    def _records_with_preserved_es(self) -> list[dict[str, Any]]:
        """Keep reliable ES fields when a detail page is still pending.

        Listing facts and detail facts arrive through different contracts.  An
        empty detail field in today's observation is therefore not permission
        to erase yesterday's committed Spanish detail or its translation
        source.  The merge is read-only and is scoped to the isolated canary
        database.
        """
        existing: dict[str, dict[str, Any]] = {}
        if self.temp_db.exists():
            try:
                with sqlite3.connect(self.temp_db) as db:
                    rows = db.execute(
                        "SELECT official_sku,name,cat1,cat2,spec,description,details "
                        "FROM product_localizations WHERE language='es'"
                    ).fetchall()
                existing = {
                    str(row[0]): {
                        "name_es": row[1], "cat1_es": row[2], "cat2_es": row[3],
                        "spec_es": row[4], "desc_es": row[5], "details_es": row[6],
                    }
                    for row in rows
                }
            except sqlite3.OperationalError:
                existing = {}
        fields = ("name_es", "cat1_es", "cat2_es", "spec_es", "desc_es", "details_es")
        merged: list[dict[str, Any]] = []
        for row in self.records or []:
            item = dict(row)
            prior = existing.get(str(item.get("sku") or item.get("official_sku") or ""), {})
            for field in fields:
                if item.get(field) in (None, "") and prior.get(field) not in (None, ""):
                    item[field] = prior[field]
            merged.append(item)
        return merged

    def _records_for_registry(self) -> list[dict[str, Any]]:
        """Build translation input from fields ready in *this* observation.

        ``_records_with_preserved_es`` is intentionally used by fact commit so
        a pending detail page cannot erase a previously committed source.  It
        must not also be used as translation input: a missing current fact
        (for example ``name_es``) would otherwise be silently restored and
        sent to the provider.  Registry ingestion receives the preserved row
        only for persistence, while fields that are not ready in the current
        source are omitted from the translation source version.
        """
        merged = self._records_with_preserved_es()
        missing = {
            (str(item.get("sku") or ""), str(item.get("field") or ""))
            for item in self.source_readiness.get("fact_missing_required", [])
        }
        pending_detail = {
            (str(item.get("sku") or ""), str(item.get("field") or ""))
            for item in self.stages.get("TRANSLATION_SOURCE_AUDIT", StageResult()).details.get("pending_fields", [])
        }
        for item in merged:
            sku = str(item.get("sku") or item.get("official_sku") or "")
            for field in ("name_es", "cat1_es", "cat2_es", "spec_es", "desc_es", "details_es"):
                if (sku, field) in missing or (sku, field) in pending_detail:
                    item[field] = None
        return merged

    def _preflight(self) -> StageResult:
        options = dict(self.cfg.get("workflow_v2") or {})
        ai = dict((self.cfg.get("localization") or {}).get("ai") or {})
        configured_qwen = str(ai.get("provider") or "").casefold() in {"qwen_mt", "qwen-mt", "qwen_mt_flash"}
        return StageResult("PASS", {"workflow_enabled": bool(options.get("enabled", False)), "workflow_v2": True,
                                     "auto_translation": self.auto_translation, "provider": ai.get("provider", "fake" if self.provider else "disabled"),
                                     "model": ai.get("model", getattr(self.provider, "model", "")), "auto_policy": self.auto_policy,
                                     "auto_export": self.auto_export, "production_apply": self.production_apply,
                                     "business_date": self.context.business_date, "database_path": str(self.temp_db),
                                     "real_qwen": bool(self.provider is None and self.auto_translation and ai.get("enabled") and configured_qwen),
                                     "production_primary": False})
    def _backup(self) -> StageResult:
        if not self.production_apply:
            return StageResult("PASS", {"mode": "fixture_or_external_adapter", "production_primary_modified": False})
        # A production-targeted run must leave a verified rollback artifact
        # before the first write.  The backup API reopens the copy and checks
        # integrity, foreign keys and database identity; a plain file copy is
        # not sufficient for a live SQLite database.
        from ..operations.backup import backup_sqlite
        backup_root = Path((self.cfg.get("paths") or {}).get("backups") or self.root / "backups")
        backup_path = backup_root / "workflow_v2" / self.context.business_date / f"{self.context.workflow_run_id}.sqlite3"
        try:
            evidence = backup_sqlite(self.temp_db, backup_path, run_id=self.context.workflow_run_id)
        except Exception as exc:
            return StageResult("BLOCKED", {"reason": "PRODUCTION_BACKUP_FAILED", "error": str(exc)}, "PRODUCTION_BACKUP_FAILED")
        _write_json(self.directory / "production_backup.json", evidence)
        return StageResult("PASS", {"mode": "production_primary", "production_primary_modified": False, **evidence})
    def _extract(self) -> StageResult:
        adapter = self.extraction_adapter
        if self.records is None:
            adapter = adapter or self._default_extraction_adapter
            try:
                extracted = adapter(cfg=self.cfg, business_date=self.context.business_date,
                                    workflow_run_id=self.context.workflow_run_id)
            except Exception as exc:
                return StageResult("BLOCKED", {"reason": "EXTRACTION_ADAPTER_FAILED", "error": str(exc)}, "EXTRACTION_ADAPTER_FAILED")
            metadata = dict(extracted) if isinstance(extracted, Mapping) else {}
            candidate_records = metadata.get("records") if metadata else extracted
            if not isinstance(candidate_records, list):
                return StageResult("BLOCKED", {"reason": "EXTRACTION_RECORDS_INVALID"}, "EXTRACTION_RECORDS_INVALID")
            self.records = [dict(row) for row in candidate_records]
            if metadata.get("authoritative_skus") is not None:
                self.expected_skus = {str(item) for item in metadata["authoritative_skus"]}
            if metadata.get("expected_new_skus") is not None:
                self.expected_new_skus = {str(item) for item in metadata["expected_new_skus"]}
            if metadata.get("expected_reappeared_skus") is not None:
                self.expected_reappeared_skus = {str(item) for item in metadata["expected_reappeared_skus"]}
            extraction_run_id = str(metadata.get("extraction_run_id") or "")
            if extraction_run_id:
                self.context.extraction_run_id = extraction_run_id
        if self.records is None:
            return StageResult("BLOCKED", {"reason": "NO_EXTRACTION_ADAPTER_OR_FIXTURE"}, "EXTRACTION_NOT_CONFIGURED")
        self.records = [dict(row) for row in self.records]; _write_json(self.directory / "records.json", self.records)
        self.context.authoritative_skus = self.expected_skus or {str(row.get("sku") or row.get("official_sku") or "") for row in self.records}
        self.context.new_skus = self.expected_new_skus; self.context.reappeared_skus = self.expected_reappeared_skus
        self.context.extraction_run_id = self.context.extraction_run_id or self.context.workflow_run_id
        self.context.source_snapshot = str(self.directory / "records.json")
        return StageResult("PASS", {"extracted_skus": len(self.records), "authoritative_skus": len(self.context.authoritative_skus)})

    def _default_extraction_adapter(self, *, cfg: Mapping[str, Any], business_date: str,
                                    workflow_run_id: str) -> dict[str, Any]:
        """Run the established daily collector in read-only snapshot mode.

        Workflow V2 no longer requires callers to manufacture a fixture.  The
        collector remains the single browser/listing/detail implementation;
        this adapter only reads its dry-run snapshot and hands records to the
        isolated V2 stages.
        """
        from ..orchestrator.daily import run_daily

        result = run_daily(dict(cfg), dry_run=True, fetch_details=True)
        snapshot_dir = Path(str(result.get("snapshot_dir") or ""))
        records_path = snapshot_dir / "products_normalized.csv"
        if not records_path.exists():
            raise FileNotFoundError(f"EXTRACTION_SNAPSHOT_MISSING: {records_path}")
        with records_path.open("r", encoding="utf-8-sig", newline="") as handle:
            records = list(csv.DictReader(handle))
        delta_path = snapshot_dir / "sku_delta.csv"
        delta = []
        if delta_path.exists():
            with delta_path.open("r", encoding="utf-8-sig", newline="") as handle:
                delta = list(csv.DictReader(handle))
        return {
            "records": records,
            "authoritative_skus": [str(row.get("sku") or "") for row in records if row.get("sku")],
            "expected_new_skus": [str(row.get("sku") or "") for row in delta if str(row.get("status") or "").upper() == "NEW"],
            "expected_reappeared_skus": [str(row.get("sku") or "") for row in delta if str(row.get("status") or "").upper() == "REAPPEARED"],
            "extraction_run_id": str(result.get("run_id") or workflow_run_id),
            "snapshot": str(snapshot_dir),
        }
    def _source_audit(self) -> StageResult:
        result = audit_source_records(self.records or [], authoritative_skus=self.context.authoritative_skus, expected_new_skus=self.context.new_skus, expected_reappeared_skus=self.context.reappeared_skus)
        self.source_readiness = result
        _write_json(self.directory / "source_readiness.json", result); _write_csv(self.directory / "source_field_audit.csv", result["field_statuses"])
        return StageResult("PASS" if result["source_ready"] else "BLOCKED", result, "SOURCE_QA_FAILED" if not result["source_ready"] else None)
    def _source_clean(self) -> StageResult:
        prior = self.stages.get("SOURCE_AUDIT")
        if prior and prior.status != "PASS": return StageResult("BLOCKED_BY_DEPENDENCY", {"reason": "SOURCE_AUDIT_NOT_READY"}, "DEPENDENCY_BLOCKED")
        cleaned, audits, result = source_clean_and_reaudit(self.records or [], authoritative_skus=self.context.authoritative_skus, expected_new_skus=self.context.new_skus, expected_reappeared_skus=self.context.reappeared_skus)
        self.records = cleaned; _write_csv(self.directory / "source_cleaning_audit.csv", audits)
        return StageResult("PASS" if result["source_ready"] else "BLOCKED", {"cleaned_fields": sum(1 for row in audits if row.get("changed")), "blocked": result.get("cleaning_blocked_count", 0)}, "SOURCE_CLEANING_BLOCKED" if not result["source_ready"] else None)
    def _source_reaudit(self) -> StageResult:
        result = audit_source_records(self.records or [], authoritative_skus=self.context.authoritative_skus, expected_new_skus=self.context.new_skus, expected_reappeared_skus=self.context.reappeared_skus)
        self.source_readiness = result
        self.context.source_ready = bool(result["source_ready"]); self.context.presence_ready = bool(result.get("presence_ready")); self.context.fact_ready = bool(result.get("fact_ready")); _write_json(self.directory / "source_readiness.json", result)
        return StageResult("PASS" if self.context.source_ready else "BLOCKED", result, "SOURCE_REAUDIT_FAILED" if not self.context.source_ready else None)
    def _fact_commit(self) -> StageResult:
        if not self.context.source_ready: return StageResult("BLOCKED", {"reason": "SOURCE_NOT_READY"}, "SOURCE_NOT_READY")
        if self.production_apply and not self.temp_db.exists():
            return StageResult("BLOCKED", {"reason": "PRODUCTION_PRIMARY_MISSING", "database": str(self.temp_db)}, "PRODUCTION_PRIMARY_MISSING")
        from ..database.production import CommitBundle, ProductionWriter
        self.temp_db.parent.mkdir(parents=True, exist_ok=True)
        fact_issues = {(str(item.get("sku") or ""), str(item.get("field") or ""))
                       for item in self.source_readiness.get("fact_missing_required", [])}
        products_list: list[dict[str, Any]] = []
        for row in self.records or []:
            item = dict(row)
            sku = str(item.get("sku") or item.get("official_sku") or "")
            if any((sku, field) in fact_issues for field in ("name_es", "cat1_es", "product_url", "current_price")):
                # Presence can still be committed, but a partial listing must
                # not overwrite an existing reliable fact with empty values.
                item["_historical_minimal"] = True
            products_list.append(item)
        products = tuple(products_list)
        observations = tuple({"run_id": self.context.workflow_run_id, "sku": str(row.get("sku") or row.get("official_sku") or ""), "observation_date": self.context.business_date, "presence_state": "PRESENT", "observation_complete": True, "absence_capable": True} for row in products)
        localization = []
        source_versions = []
        commit_records = self._records_with_preserved_es()
        commit_by_sku = {str(row.get("sku") or row.get("official_sku") or ""): row for row in commit_records}
        for row in products:
            sku = str(row.get("sku") or row.get("official_sku") or "")
            if row.get("_historical_minimal"):
                continue
            source_row = commit_by_sku.get(sku, row)
            localization.append({"sku": sku, "language": "es", "name": source_row.get("name_es", ""), "cat1": source_row.get("cat1_es", ""), "cat2": source_row.get("cat2_es", ""), "spec": source_row.get("spec_es", ""), "description": source_row.get("desc_es", ""), "details": source_row.get("details_es", ""), "source": "OFFICIAL_FACT", "review_status": "VERIFIED"})
            localization.append({"sku": sku, "language": "zh", "name": "", "cat1": "", "cat2": "", "spec": "", "description": "", "details": "", "source": "PENDING", "review_status": "PENDING"})
            source_versions.append({"sku": sku, "run_id": self.context.workflow_run_id, "observed_at": self.context.business_date, "source_name": "workflow_v2", "facts": {"name": {"raw": source_row.get("name_es"), "normalized": source_row.get("name_es")}, "cat1": {"raw": source_row.get("cat1_es"), "normalized": source_row.get("cat1_es")}, "cat2": {"raw": source_row.get("cat2_es"), "normalized": source_row.get("cat2_es")}, "spec": {"raw": source_row.get("spec_es"), "normalized": source_row.get("spec_es")}, "description": {"raw": source_row.get("desc_es"), "normalized": source_row.get("desc_es")}, "details": {"raw": source_row.get("details_es"), "normalized": source_row.get("details_es")}}})
        bundle = CommitBundle(run_id=self.context.workflow_run_id, observation_date=self.context.business_date, qa_state="PASS", current_products=products, localization_updates=tuple(localization), source_fact_versions=tuple(source_versions), observations=observations, run_record={"dry_run": False, "run_id": self.context.workflow_run_id}, requires_collection_integrity=False)
        self.context.source_commit_id = ProductionWriter(self.temp_db, role="PRIMARY").commit(bundle)
        return StageResult("PASS", {"state": "PRIMARY_SQLITE_COMMITTED" if self.production_apply else "TEMP_SQLITE_COMMITTED", "commit_id": self.context.source_commit_id, "database": str(self.temp_db), "fact_ready": self.context.fact_ready, "presence_only_rows": sum(1 for row in products if row.get("_historical_minimal"))})
    def _detail_plan(self) -> StageResult:
        from .detail_stage import plan_detail_refresh
        planned = plan_detail_refresh(self.records or [], db_path=self.temp_db, max_age_days=int((self.cfg.get("workflow_v2") or {}).get("detail_max_age_days", 7)))
        _write_json(self.directory / "detail_plan.json", planned); _write_csv(self.directory / "detail_plan.csv", planned)
        return StageResult("PASS", {"planned": len(planned), "skus": [row["sku"] for row in planned], "state_source": "product_detail_state"})
    def _detail_enrich(self) -> StageResult:
        from .detail_stage import classify_detail_outcome
        pending = [str(row.get("sku") or row.get("official_sku")) for row in self.records or [] if classify_detail_outcome(row) == "DETAIL_PENDING"]
        plan = self._load_json_artifact("detail_plan.json", [])
        adapter_result: Mapping[str, Any] = {}
        adapter = self.detail_adapter
        if adapter is None and plan and bool(((self.cfg.get("workflow_v2") or {}).get("detail_retry") or {}).get("enabled", False)):
            adapter = self._default_detail_adapter
        if plan and adapter is not None:
            adapter_result = dict(adapter(
                records=[dict(row) for row in (self.records or [])],
                plan=list(plan), business_date=self.context.business_date,
                workflow_run_id=self.context.workflow_run_id,
            ) or {})
            updated = adapter_result.get("records")
            if isinstance(updated, list):
                self.records = [dict(row) for row in updated]
                _write_json(self.directory / "records.json", self.records)
            self.context.detail_commit_id = str(adapter_result.get("detail_commit_id") or "") or self.context.detail_commit_id
            pending = [str(row.get("sku") or row.get("official_sku")) for row in self.records or [] if classify_detail_outcome(row) == "DETAIL_PENDING"]
        return StageResult("PASS", {"completed": len(self.records or []) - len(pending), "pending": pending, "adapter": "injected-existing-detail-retry-contract" if self.detail_adapter else ("existing-detail-retry-contract" if adapter else "disabled"), "adapter_result": dict(adapter_result)})

    def _default_detail_adapter(self, *, records: list[dict[str, Any]], plan: list[dict[str, Any]],
                                business_date: str, workflow_run_id: str) -> dict[str, Any]:
        """Use the established detail-retry/apply contract on the canary DB."""
        parent_run_id = str(self.context.extraction_run_id or "")
        if not parent_run_id or not self.cfg.get("paths"):
            return {"records": records, "pending": [str(row.get("sku") or "") for row in plan], "status": "NOT_CONFIGURED"}
        from ..orchestrator.detail_retry import _completed_details, _snapshot_root, run_detail_retry
        retry_report = run_detail_retry(dict(self.cfg), parent_run_id)
        parent = _snapshot_root(self.cfg["paths"], parent_run_id)
        completed = _completed_details(parent)
        updated = [dict(row) for row in records]
        for row in updated:
            detail = completed.get(str(row.get("sku") or row.get("official_sku") or ""))
            if detail:
                for field in ("name_es", "cat1_es", "cat2_es", "spec_es", "desc_es", "details_es", "product_url", "image_url"):
                    if detail.get(field) not in (None, ""):
                        row[field] = detail[field]
                row["detail_status"] = "COMPLETE"
        details_by_sku = {
            str(row.get("sku") or row.get("official_sku") or ""): row
            for row in updated if str(row.get("sku") or row.get("official_sku") or "") in completed
        }
        detail_commit_id = ""
        if details_by_sku:
            from ..database.production import apply_detail_corrections
            applied = apply_detail_corrections(
                self.temp_db, parent_run_id=workflow_run_id,
                details_by_sku=details_by_sku, mode="APPLY", source_run_date=business_date,
            )
            detail_commit_id = str(applied.get("commit_id") or "")
        return {"records": updated, "detail_commit_id": detail_commit_id,
                "retry_report": retry_report, "completed": len(details_by_sku)}
    def _translation_source_audit(self) -> StageResult:
        if not self.context.source_ready: return StageResult("BLOCKED", {"reason": "SOURCE_NOT_READY"}, "TRANSLATION_SOURCE_NOT_READY")
        pending_fields = []
        ready_fields = []
        for row in self.records or []:
            pending = str(row.get("detail_status") or "").upper() in {"PENDING", "DETAIL_PENDING", "INCOMPLETE", "ACCESS_INTERRUPTED", "403", "429", "CHALLENGE", "TIMEOUT"}
            for field in ("name_es", "cat1_es", "cat2_es", "spec_es", "desc_es", "details_es"):
                value = str(row.get(field) or "").strip()
                if not value:
                    if pending and field in {"desc_es", "details_es"}:
                        pending_fields.append({"sku": row.get("sku"), "field": field, "status": "PENDING_DETAIL"})
                    continue
                ready_fields.append({"sku": row.get("sku"), "field": field, "status": "READY"})
        return StageResult("PASS", {"translation_source_ready": True, "source_hashes": len(self.records or []), "ready_fields": ready_fields, "pending_fields": pending_fields, "field_level": True})
    def _registry_ingest(self) -> StageResult:
        from ..localization.runtime_builder import build_translation_runtime
        configured_ai = dict((self.cfg.get("localization") or {}).get("ai") or {})
        allow_configured_provider = bool(self.auto_translation and self.provider is None and configured_ai.get("enabled"))
        self.translation_runtime = build_translation_runtime(self._runtime_cfg(), db_path=self.temp_db, allow_provider=allow_configured_provider)
        result = self.translation_runtime.registry.ingest_records(self._records_for_registry(), source_run_id=self.context.workflow_run_id, observed_at=self.context.business_date)
        return StageResult("PASS", {"registry": "TranslationRegistryV1", **result, "production_registry_write": False})
    def _translation_plan(self) -> StageResult:
        if self.translation_runtime is None: self._registry_ingest()
        from ..database.connection import connect
        with connect(self.temp_db) as db:
            rows = [dict(row) for row in db.execute("SELECT official_sku AS sku, requested_fields, source_hash, reason FROM translation_queue WHERE status IN ('PENDING','RETRY') ORDER BY created_at").fetchall()]
        self.translation_plan = rows
        _write_json(self.directory / "translation_plan.json", rows); _write_csv(self.directory / "translation_plan.csv", rows)
        return StageResult("PASS", {"queued": len(rows), "registry_queue": True})
    def _qwen_translate(self) -> StageResult:
        if not self.translation_plan: return StageResult("PASS", {"called": 0, "reason": "NO_CHANGED_FIELDS"})
        if self.translation_results and all(str(item.get("status") or "").upper() == "PASS" for item in self.translation_results):
            return StageResult("PASS", {"called": 0, "reason": "RESTORED_FROM_ARTIFACT", "results": self.translation_results})
        if not self.auto_translation: return StageResult("SKIPPED", {"reason": "AUTO_TRANSLATION_DISABLED", "queued": len(self.translation_plan)})
        if not self.context.source_ready or not self.context.source_commit_id: return StageResult("BLOCKED", {"reason": "QWEN_GATE_NOT_SATISFIED"}, "QWEN_GATE_BLOCKED")
        if self.translation_runtime is None: self._registry_ingest()
        provider = self.provider or self.translation_runtime.provider
        if provider is None or not hasattr(provider, "translate"):
            return StageResult("BLOCKED", {"reason": "PROVIDER_NOT_CONFIGURED"}, "TRANSLATION_PROVIDER_MISSING")
        self.translation_runtime.resolver.provider = provider
        self.translation_runtime.worker.resolver.provider = provider
        result = self.translation_runtime.worker.process_once(limit=max(50, len(self.translation_plan)), worker_id=f"workflow-v2:{self.context.workflow_run_id}")
        from ..database.connection import connect
        canonical_to_field = {"name": "name", "cat1": "cat1", "cat2": "cat2", "spec": "spec", "description": "description", "details": "details"}
        grouped: dict[str, dict[str, Any]] = {}
        with connect(self.temp_db) as db:
            revisions = db.execute("""SELECT s.official_sku,u.field_name,r.revision_id,r.target_text,
                r.source_hash,r.qa_status,r.canonical_qa_status,r.review_status
                FROM translation_revisions r JOIN translation_units u ON u.current_revision_id=r.revision_id
                JOIN translation_source_versions s ON s.source_version_id=u.source_version_id
                WHERE s.source_run_id=?""", (self.context.workflow_run_id,)).fetchall()
        for row in revisions:
            item = grouped.setdefault(str(row[0]), {"sku": str(row[0]), "status": "PASS", "fields": {}, "source_hash": str(row[4]), "qa": {"status": str(row[5]), "overall_ready": True}})
            item["fields"][canonical_to_field.get(str(row[1]), str(row[1]))] = str(row[3] or "")
        self.translation_results = list(grouped.values()) or [{"sku": "", "status": "RETRY", "fields": {}, "worker": result.as_dict()}]
        for item in self.translation_results: item.setdefault("worker", result.as_dict())
        _write_json(self.directory / "translation_result.json", self.translation_results)
        _write_csv(self.directory / "translation_result.csv", self.translation_results)
        status = "PASS" if result.failed == 0 and result.blocked == 0 and result.retried == 0 else ("DEGRADED" if result.retried else "BLOCKED")
        return StageResult(status, {"called": len({str(row.get('sku') or '') for row in self.records or []}), "provider_calls": result.completed + result.retried + result.failed + result.blocked, "worker": result.as_dict()})
    def _translation_qa(self) -> StageResult:
        if not self.translation_results:
            self.context.translation_ready = not self.translation_plan
            return StageResult("SKIPPED", {"reason": "NO_TRANSLATION_RESULTS"})
        rows = [{"sku": row.get("sku"), "qa_status": (row.get("qa") or {}).get("status", row.get("status")), "status": row.get("status")} for row in self.translation_results]; _write_csv(self.directory / "translation_qa.csv", rows)
        passed = all(row.get("status") == "PASS" for row in self.translation_results); self.context.translation_ready = passed
        return StageResult("PASS" if passed else "BLOCKED", {"passed": passed, "rows": rows}, "TRANSLATION_QA_FAILED" if not passed else None)
    def _translation_policy(self) -> StageResult:
        if not self.translation_results: self.context.translation_ready = not self.translation_plan; return StageResult("SKIPPED", {"reason": "NO_RESULTS"})
        if self.translation_runtime is None:
            self._registry_ingest()
        from ..database.connection import connect
        from ..knowledge.approval import LOW_RISK_FIELDS
        field_map = {"name_es": "name", "cat1_es": "cat1", "cat2_es": "cat2", "spec_es": "spec", "desc_es": "description", "details_es": "details"}
        decisions = []
        with connect(self.temp_db) as db:
            rows = db.execute("""SELECT s.official_sku,u.field_name,u.unit_id,r.revision_id,r.source_hash,
                r.qa_status,r.canonical_qa_status,r.review_status,r.target_text
                FROM translation_revisions r JOIN translation_units u ON u.current_revision_id=r.revision_id
                JOIN translation_source_versions s ON s.source_version_id=u.source_version_id
                WHERE s.source_run_id=?""", (self.context.workflow_run_id,)).fetchall()
        for row in rows:
            ready = bool(self.context.source_ready and self.context.source_commit_id
                         and str(row[5] or "").upper() == "PASS"
                         and str(row[6] or "NOT_RUN").upper() in {"PASS", "NOT_REQUIRED"})
            canonical_field = field_map.get(str(row[1]), str(row[1]))
            auto_allowed = canonical_field in LOW_RISK_FIELDS or self.allow_high_risk_auto_approval
            human_approved = ready and str(row[7] or "").upper() in {"APPROVED", "HUMAN_APPROVED", "HUMAN_REVIEWED", "LOCKED"}
            decision = (
                "HUMAN_APPROVED" if human_approved else
                "AUTO_VALIDATED" if ready and self.auto_policy and auto_allowed else
                "REVIEW_REQUIRED"
            )
            decisions.append({"decision_id": f"{row[3]}:QWEN_AUTO_VALIDATE_V1", "revision_id": row[3], "unit_id": row[2], "sku": row[0], "field": row[1], "fields": [row[1]], "decision": decision, "policy_id": "QWEN_AUTO_VALIDATE_V1", "policy_version": "QWEN_AUTO_VALIDATE_V1", "source_hash": row[4], "qa_status": row[5], "canonical_qa_status": row[6], "rules_passed": ["SOURCE_READY", "FACT_COMMITTED", "TYPED_QA_PASS", "CANONICAL_QA_PASS" if str(row[6]).upper() == "PASS" else "CANONICAL_QA_NOT_REQUIRED"], "rules_failed": [] if ready else ["AUTO_POLICY_GATE"]})
            if human_approved:
                decisions[-1]["rules_passed"].append("HUMAN_APPROVAL_PRESENT")
            self.translation_runtime.registry.record_policy_decision(str(row[3]), decision=decision, policy_id="QWEN_AUTO_VALIDATE_V1", policy_version="QWEN_AUTO_VALIDATE_V1", evidence={"sku": row[0], "field": row[1], "source_hash": row[4], "qa_status": row[5], "canonical_qa_status": row[6], "review_status": row[7], "decision_id": f"{row[3]}:QWEN_AUTO_VALIDATE_V1"})
            if decision == "AUTO_VALIDATED":
                self.translation_runtime.registry.approve_revision(str(row[3]), actor="human:workflow-v2-auto-policy")
        self.policy_results = decisions
        _write_json(self.directory / "translation_policy.json", self.policy_results); _write_csv(self.directory / "translation_policy.csv", self.policy_results)
        self.context.translation_ready = all(row["decision"] in {"AUTO_VALIDATED", "HUMAN_APPROVED"} for row in self.policy_results); return StageResult("PASS" if self.context.translation_ready else "BLOCKED", {"decisions": self.policy_results}, "AUTO_POLICY_BLOCKED" if not self.context.translation_ready else None)
    def _translation_apply(self) -> StageResult:
        if not self.apply_enabled: return StageResult("SKIPPED", {"reason": "TRANSLATION_APPLY_DISABLED"})
        if not self.context.translation_ready: return StageResult("BLOCKED", {"reason": "TRANSLATION_NOT_READY"}, "TRANSLATION_APPLY_BLOCKED")
        if self.translation_runtime is None:
            self._registry_ingest()
        from ..knowledge.storage import KnowledgeStore
        store = KnowledgeStore(self.temp_db, role="PRIMARY")
        staged = store.stage_approved_registry_patches(expected_base_commit_id=self.context.source_commit_id, actor="human:workflow-v2-local-canary")
        from ..database.production import apply_approved_localization_patches
        applied = apply_approved_localization_patches(self.temp_db, patch_ids=staged["patch_ids"], expected_base_commit_id=self.context.source_commit_id, actor="service:workflow-v2-local-canary", run_id=f"{self.context.workflow_run_id}-localization-apply") if staged["patch_ids"] else {"applied_fields": 0}
        # The apply coordinator creates a real immutable commit batch.  Keep
        # that exact ID in the workflow context so export provenance and
        # resume/report consumers never have to infer or reconstruct it.
        self.context.localization_commit_id = str(
            applied.get("commit_id") or self.context.source_commit_id or ""
        ) or None
        return StageResult("PASS", {"changed_fields": applied.get("applied_fields", 0), "commit_id": self.context.localization_commit_id, "patches": staged.get("patch_ids", [])})
    def _export_audit(self) -> StageResult:
        es = [dict(row) for row in self.records or []]
        zh = [dict(row) for row in self.records or []]
        if self.translation_runtime is not None:
            from ..database.connection import connect
            with connect(self.temp_db) as db:
                localized = {str(row[0]): dict(row) for row in db.execute("SELECT official_sku,name,cat1,cat2,spec,description,details,review_status,source_hash,freshness_status FROM product_localizations WHERE language='zh'").fetchall()}
                revision_rows = db.execute(
                    """SELECT s.official_sku,u.field_name,r.source_hash,r.review_status,
                              r.qa_status,r.canonical_qa_status
                       FROM translation_revisions r
                       JOIN translation_units u ON u.current_revision_id=r.revision_id
                       JOIN translation_source_versions s ON s.source_version_id=u.source_version_id
                      WHERE s.source_run_id=?""",
                    (self.context.workflow_run_id,),
                ).fetchall()
            approved_fields: dict[str, set[str]] = {}
            approved_hash: dict[str, str] = {}
            registry_field_names = {"name_es": "name", "cat1_es": "cat1", "cat2_es": "cat2", "spec_es": "spec", "desc_es": "description", "details_es": "details"}
            for item in revision_rows:
                if (str(item[3] or "").upper() in {"APPROVED", "HUMAN_REVIEWED", "LOCKED"}
                        and str(item[4] or "").upper() == "PASS"
                        and str(item[5] or "NOT_RUN").upper() in {"PASS", "NOT_REQUIRED"}):
                    approved_fields.setdefault(str(item[0]), set()).add(registry_field_names.get(str(item[1]), str(item[1])))
                    approved_hash[str(item[0])] = str(item[2] or "")
            for row in zh:
                sku = str(row.get("sku") or "")
                loc = localized.get(sku, {})
                row.update({"name_zh": loc.get("name") or row.get("name_zh", ""), "cat1_zh": loc.get("cat1") or row.get("cat1_zh", ""), "cat2_zh": loc.get("cat2") or row.get("cat2_zh", ""), "spec_zh": loc.get("spec") or row.get("spec_zh", ""), "desc_zh": loc.get("description") or row.get("desc_zh", ""), "details_zh": loc.get("details") or row.get("details_zh", "")})
                if loc:
                    canonical_by_source = {"name_es": "name", "cat1_es": "cat1", "cat2_es": "cat2", "spec_es": "spec", "desc_es": "description", "details_es": "details"}
                    required_fields = {canonical_by_source[field] for field in canonical_by_source if str(row.get(field) or "").strip()}
                    row["translation_status"] = "APPROVED" if required_fields.issubset(approved_fields.get(sku, set())) else (loc.get("review_status") or "")
                    row["translation_source_hash"] = approved_hash.get(sku) or loc.get("source_hash") or ""
                    row["translation_freshness"] = loc.get("freshness_status") or ""
        # The two projections are separate objects even when a fixture has no
        # localization rows yet; parity is evaluated across their identities.
        from ..exporting.service import build_es_rows, validate_output_rows
        from ..exporting.dictionary_join import build_zh_rows_from_localized_source
        self.es_projection = build_es_rows(es)
        self.zh_projection, _ = build_zh_rows_from_localized_source(zh)
        validate_output_rows(self.es_projection)
        validate_output_rows(self.zh_projection)
        # Audit the rows that will actually be published.  The row builders
        # use frozen Chinese headers, while the audit contract uses canonical
        # field names, so join each projection back to its source metadata
        # before running the field and parity gates.
        projection_by_sku = lambda rows: {
            str(row.get("编号") or ""): dict(row) for row in rows
        }
        es_projected = projection_by_sku(self.es_projection)
        zh_projected = projection_by_sku(self.zh_projection)
        es_audit_rows: list[dict[str, Any]] = []
        zh_audit_rows: list[dict[str, Any]] = []
        zh_source_by_sku = {
            str(row.get("sku") or row.get("official_sku") or ""): dict(row)
            for row in zh
        }
        for source in es:
            sku = str(source.get("sku") or source.get("official_sku") or "")
            es_row = dict(source)
            projected = es_projected.get(sku, {})
            es_row.update({
                "name_es": projected.get("标题"), "cat1_es": projected.get("分类1"),
                "cat2_es": projected.get("分类2"), "spec_es": projected.get("规格"),
                "unit_price": projected.get("单价"), "desc_es": projected.get("描述"),
                "details_es": projected.get("产品详情"),
            })
            es_audit_rows.append(es_row)
            zh_row = dict(zh_source_by_sku.get(sku, source))
            projected = zh_projected.get(sku, {})
            zh_row.update({
                "name_zh": projected.get("标题"), "cat1_zh": projected.get("分类1"),
                "cat2_zh": projected.get("分类2"), "spec_zh": projected.get("规格"),
                "unit_price_zh": projected.get("单价"), "desc_zh": projected.get("描述"),
                "details_zh": projected.get("产品详情"),
            })
            zh_audit_rows.append(zh_row)
        es_audit = audit_es(es_audit_rows)
        zh_audit = audit_zh(zh_audit_rows)
        parity = audit_parity(es_audit_rows, zh_audit_rows)
        ready = export_readiness(source_ready=self.context.source_ready, fact_committed=bool(self.context.source_commit_id), translation_ready=self.context.translation_ready, es_audit=es_audit, zh_audit=zh_audit, parity=parity)
        self.context.export_ready = bool(ready["export_ready"]); self.report.update({"ES audit": es_audit, "ZH audit": zh_audit, "Parity audit": parity})
        _write_json(self.directory / "export_readiness.json", ready); _write_csv(self.directory / "export_es_audit.csv", [{"status": es_audit["status"], "missing": json.dumps(es_audit.get("missing_required"), ensure_ascii=False)}]); _write_csv(self.directory / "export_zh_audit.csv", [{"status": zh_audit["status"], "issues": json.dumps(zh_audit.get("issues"), ensure_ascii=False)}]); _write_csv(self.directory / "export_parity_audit.csv", [{"status": parity["status"], "issues": json.dumps(parity.get("issues"), ensure_ascii=False)}])
        return StageResult("PASS" if self.context.export_ready else "BLOCKED", ready, "EXPORT_NOT_READY" if not self.context.export_ready else None)
    def _export_write(self) -> StageResult:
        if not self.auto_export: return StageResult("SKIPPED", {"reason": "AUTO_EXPORT_DISABLED"})
        if not self.context.export_ready: return StageResult("BLOCKED", {"reason": "EXPORT_GATE_BLOCKED"}, "EXPORT_GATE_BLOCKED")
        staging = self.directory / "staging"; staging.mkdir(exist_ok=True)
        pending = staging / ".pending"
        if pending.exists():
            shutil.rmtree(pending)
        pending.mkdir()
        published = False
        formal_exports: dict[str, Any] = {}
        try:
            _write_json(pending / "es.json", self.es_projection)
            _write_json(pending / "zh.json", self.zh_projection)
            # When the caller supplies the normal project configuration, run
            # the existing formal exporter against the isolated canary DB and
            # redirect its output into the pending directory.  Fixture tests
            # without export configuration retain the row-builder contract.
            if self.cfg.get("paths"):
                from ..exporting.service import export_catalog
                formal_root = pending / "formal"
                formal_cfg = self._runtime_cfg()
                formal_cfg["paths"] = {**dict(self.cfg.get("paths") or {}), "exports": str(formal_root)}
                for language in ("es", "zh"):
                    formal_exports[language] = export_catalog(
                        formal_cfg, language=language, export_date=self.context.business_date,
                        no_images=True, run_id=self.context.workflow_run_id,
                    )
            os.replace(pending / "es.json", staging / "es.json")
            os.replace(pending / "zh.json", staging / "zh.json")
            if (pending / "formal").exists():
                if (staging / "formal").exists():
                    shutil.rmtree(staging / "formal")
                os.replace(pending / "formal", staging / "formal")
            _write_json(pending / "publish_manifest.json", {"status": "PUBLISHED", "files": ["es.json", "zh.json"], "formal_exports": formal_exports})
            os.replace(pending / "publish_manifest.json", staging / "publish_manifest.json")
            published = True
        finally:
            if pending.exists():
                shutil.rmtree(pending)
            if not published:
                for path in (staging / "es.json", staging / "zh.json", staging / "publish_manifest.json"):
                    if path.exists():
                        path.unlink()
                if (staging / "formal").exists():
                    shutil.rmtree(staging / "formal")
        return StageResult("PASS", {"staging": str(staging), "atomic": published, "existing_exporter": True, "formal_exports": formal_exports, "es_rows": len(self.es_projection), "zh_rows": len(self.zh_projection)})
    def _final_state(self) -> str:
        blockers = [item for item in self.stages.values() if item.status in {"BLOCKED", "FAILED", "BLOCKED_BY_DEPENDENCY"}]
        if any(item.status == "FAILED" for item in blockers): return "FAILED"
        if blockers: return "BLOCKED"
        if any(item.status == "DEGRADED" for item in self.stages.values()) or not self.context.export_ready: return "DEGRADED"
        return "SUCCESS"


def run_workflow_v2(cfg: Mapping[str, Any], *, business_date: str | None = None, run_id: str | None = None,
                    resume: bool = False, records: list[dict[str, Any]] | None = None, provider: Any = None,
                    expected_skus: Iterable[str] | None = None, expected_new_skus: Iterable[str] | None = None,
                    expected_reappeared_skus: Iterable[str] | None = None, dry_run: bool = True,
                    auto_translation: bool | None = None, auto_policy: bool | None = None, auto_export: bool | None = None,
                    apply_enabled: bool = False, production_apply: bool = False,
                    temp_db: Path | None = None, detail_adapter: Any = None,
                    extraction_adapter: Any = None,
                    allow_high_risk_auto_approval: bool = False) -> dict[str, Any]:
    root = Path(cfg.get("project_root") or ".") / "runtime" / "reports" / "workflow_v2"
    options = dict(cfg.get("workflow_v2") or {})
    if resume and (not run_id or not business_date):
        candidates = sorted(root.glob(f"*/{run_id or '*'}")) if root.exists() else []
        candidates = [path for path in candidates if (path / "workflow_state.json").exists()]
        if not candidates:
            raise ValueError("WORKFLOW_RUN_NOT_FOUND")
        if len(candidates) > 1:
            raise ValueError("WORKFLOW_RUN_AMBIGUOUS")
        run_id = candidates[0].name
        if not business_date:
            business_date = candidates[0].parent.name
    context = new_context(root, business_date=business_date, run_id=run_id)
    from ..database.integration import database_path, storage_mode
    configured_primary = Path(database_path(cfg)).resolve()
    if production_apply:
        # Production mode is intentionally multi-gated.  It must target the
        # configured PRIMARY database and cannot be combined with the canary
        # temp-db path.  The old behavior silently created a workflow-local
        # SQLite file, which looked like a successful production commit while
        # leaving PRIMARY unchanged.
        if dry_run:
            raise ValueError("WORKFLOW_V2_PRODUCTION_REQUIRES_NO_DRY_RUN")
        if not options.get("enabled", False):
            raise ValueError("WORKFLOW_V2_PRODUCTION_NOT_ENABLED")
        if storage_mode(cfg) != "SQLITE_PRIMARY":
            raise ValueError("WORKFLOW_V2_PRODUCTION_REQUIRES_SQLITE_PRIMARY")
        knowledge = dict(cfg.get("knowledge") or {})
        localization = dict(cfg.get("localization") or {})
        if not bool(knowledge.get("production_apply_enabled")) or not bool(localization.get("production_apply_enabled")):
            raise ValueError("WORKFLOW_V2_PRODUCTION_APPLY_DISABLED")
        if temp_db is not None and Path(temp_db).resolve() != configured_primary:
            raise ValueError("WORKFLOW_V2_PRODUCTION_DB_MUST_BE_CONFIGURED_PRIMARY")
        temp_db = configured_primary
    elif temp_db is not None and Path(temp_db).resolve() == configured_primary:
        raise ValueError("WORKFLOW_V2_PRODUCTION_DB_FORBIDDEN")
    runner = WorkflowV2Runner(root=root, context=context, records=records, expected_skus=expected_skus, expected_new_skus=expected_new_skus, expected_reappeared_skus=expected_reappeared_skus, provider=provider, auto_translation=options.get("auto_translation", {}).get("enabled", False) if auto_translation is None else auto_translation, auto_policy=options.get("auto_policy_approval", {}).get("enabled", False) if auto_policy is None else auto_policy, auto_export=options.get("auto_export", {}).get("enabled", False) if auto_export is None else auto_export, apply_enabled=apply_enabled, dry_run=dry_run, production_apply=production_apply, temp_db=temp_db, cfg=cfg, detail_adapter=detail_adapter, extraction_adapter=extraction_adapter, allow_high_risk_auto_approval=allow_high_risk_auto_approval)
    return runner.run(resume=resume).as_dict()

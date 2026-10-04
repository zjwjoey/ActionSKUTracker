"""Resumable Workflow V2 orchestration.

The runner owns stage boundaries and state only.  Existing collectors, writers,
registry and export implementations remain injectable adapters; this keeps the
development path safe for fixtures and makes the fact/translation boundary
explicit.
"""
from __future__ import annotations

import csv
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable, Mapping

from .context import new_context, run_directory
from .contracts import STAGES, StageResult, WorkflowContext, WorkflowResult
from .export_audit import audit_es, audit_parity, audit_zh, export_readiness
from .source_audit import audit_source_records, source_clean_and_reaudit
from .translation_stage import auto_validate, apply_to_fixture, plan_translations, translate_plan


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


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
                 dry_run: bool = False, production_apply: bool = False):
        self.root = Path(root); self.context = context; self.directory = run_directory(self.root, context)
        self.records = records
        self.expected_skus = set(str(item) for item in (expected_skus or ()))
        self.expected_new_skus = set(str(item) for item in (expected_new_skus or ()))
        self.expected_reappeared_skus = set(str(item) for item in (expected_reappeared_skus or ()))
        self.provider = provider; self.approved = approved or {}
        self.auto_translation = auto_translation; self.auto_policy = auto_policy; self.auto_export = auto_export
        self.apply_enabled = apply_enabled; self.dry_run = dry_run; self.production_apply = production_apply
        self.stages: dict[str, StageResult] = {}; self.report: dict[str, Any] = {}
        self.translation_plan: list[Any] = []; self.translation_results: list[dict[str, Any]] = []; self.policy_results: list[dict[str, Any]] = []

    def run(self, *, resume: bool = False) -> WorkflowResult:
        state_path = self.directory / "workflow_state.json"
        if resume and state_path.exists():
            prior = json.loads(state_path.read_text(encoding="utf-8"))
            self.stages = {key: StageResult(value.get("status", "PASS"), value.get("details", {}), value.get("error_code"), bool(value.get("retryable"))) for key, value in (prior.get("stages") or {}).items()}
            if self.records is None and (self.directory / "records.json").exists(): self.records = json.loads((self.directory / "records.json").read_text(encoding="utf-8"))
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
                            "Translation queued": len(self.translation_plan), "Qwen called": sum(len(item.get("requested_fields", ())) for item in self.translation_results),
                            "Translation completed": sum(1 for item in self.translation_results if item.get("status") == "PASS"),
                            "Export ready": self.context.export_ready, "FINAL_STATUS": state})
        self._stage("REPORT", lambda: StageResult("PASS", self.report))
        payload = _state_payload(self.context, self.stages, state, self.report)
        _write_json(state_path, payload); _write_json(self.directory / "workflow_manifest.json", {"workflow_run_id": self.context.workflow_run_id, "business_date": self.context.business_date, "stages": list(STAGES)})
        _write_json(self.directory / "workflow_report.json", payload)
        return WorkflowResult(self.context, state, self.stages, self.report)

    def _stage(self, name: str, fn) -> None:
        if name != "REPORT" and name in self.stages and self.stages[name].status == "PASS": return
        try: result = fn()
        except Exception as exc: result = StageResult("FAILED", {"error": str(exc)}, type(exc).__name__)
        self.stages[name] = result
        if result.status in {"BLOCKED", "FAILED"}:
            self.report.setdefault("blockers", []).append({"stage": name, "code": result.error_code or result.status, "details": dict(result.details)})

    def _preflight(self) -> StageResult:
        return StageResult("PASS", {"workflow_v2": True, "auto_translation": self.auto_translation, "auto_policy": self.auto_policy, "auto_export": self.auto_export, "production_apply": self.production_apply})
    def _backup(self) -> StageResult:
        return StageResult("PASS", {"mode": "fixture_or_external_adapter", "production_primary_modified": False})
    def _extract(self) -> StageResult:
        if self.records is None: return StageResult("BLOCKED", {"reason": "NO_EXTRACTION_ADAPTER_OR_FIXTURE"}, "EXTRACTION_NOT_CONFIGURED")
        self.records = [dict(row) for row in self.records]; _write_json(self.directory / "records.json", self.records)
        self.context.authoritative_skus = self.expected_skus or {str(row.get("sku") or row.get("official_sku") or "") for row in self.records}
        self.context.new_skus = self.expected_new_skus; self.context.reappeared_skus = self.expected_reappeared_skus
        self.context.extraction_run_id = self.context.workflow_run_id; self.context.source_snapshot = str(self.directory / "records.json")
        return StageResult("PASS", {"extracted_skus": len(self.records), "authoritative_skus": len(self.context.authoritative_skus)})
    def _source_audit(self) -> StageResult:
        result = audit_source_records(self.records or [], authoritative_skus=self.context.authoritative_skus, expected_new_skus=self.context.new_skus, expected_reappeared_skus=self.context.reappeared_skus)
        _write_json(self.directory / "source_readiness.json", result); _write_csv(self.directory / "source_field_audit.csv", result["field_statuses"])
        return StageResult("PASS" if result["source_ready"] else "BLOCKED", result, "SOURCE_QA_FAILED" if not result["source_ready"] else None)
    def _source_clean(self) -> StageResult:
        cleaned, audits, result = source_clean_and_reaudit(self.records or [], authoritative_skus=self.context.authoritative_skus, expected_new_skus=self.context.new_skus, expected_reappeared_skus=self.context.reappeared_skus)
        self.records = cleaned; _write_csv(self.directory / "source_cleaning_audit.csv", audits)
        return StageResult("PASS" if result["source_ready"] else "BLOCKED", {"cleaned_fields": sum(1 for row in audits if row.get("changed")), "blocked": result.get("cleaning_blocked_count", 0)}, "SOURCE_CLEANING_BLOCKED" if not result["source_ready"] else None)
    def _source_reaudit(self) -> StageResult:
        result = audit_source_records(self.records or [], authoritative_skus=self.context.authoritative_skus, expected_new_skus=self.context.new_skus, expected_reappeared_skus=self.context.reappeared_skus)
        self.context.source_ready = bool(result["source_ready"]); _write_json(self.directory / "source_readiness.json", result)
        return StageResult("PASS" if self.context.source_ready else "BLOCKED", result, "SOURCE_REAUDIT_FAILED" if not self.context.source_ready else None)
    def _fact_commit(self) -> StageResult:
        if not self.context.source_ready: return StageResult("BLOCKED", {"reason": "SOURCE_NOT_READY"}, "SOURCE_NOT_READY")
        if self.production_apply: return StageResult("BLOCKED", {"reason": "PRODUCTION_APPLY_REQUIRES_EXPLICIT_ADAPTER"}, "PRODUCTION_APPLY_DISABLED_IN_DEV")
        self.context.source_commit_id = f"fixture-fact-{self.context.workflow_run_id}"; return StageResult("PASS", {"state": "PREVIEW_ONLY" if self.dry_run else "FIXTURE_COMMITTED", "commit_id": self.context.source_commit_id})
    def _detail_plan(self) -> StageResult:
        planned = [str(row.get("sku") or row.get("official_sku")) for row in self.records or [] if str(row.get("detail_status") or "").upper() in {"NEW", "MISSING_DETAIL", "DETAIL_SOURCE_CHANGED", "DETAIL_EXPIRED", "MANUAL"}]
        _write_csv(self.directory / "detail_plan.csv", [{"sku": sku, "reason": "DETAIL_REFRESH"} for sku in planned]); return StageResult("PASS", {"planned": len(planned), "skus": planned})
    def _detail_enrich(self) -> StageResult:
        pending = [str(row.get("sku") or row.get("official_sku")) for row in self.records or [] if str(row.get("detail_status") or "").upper() in {"PENDING", "DETAIL_PENDING", "403", "429", "CHALLENGE", "TIMEOUT"}]
        return StageResult("PASS", {"completed": len(self.records or []) - len(pending), "pending": pending})
    def _translation_source_audit(self) -> StageResult:
        if not self.context.source_ready: return StageResult("BLOCKED", {"reason": "SOURCE_NOT_READY"}, "TRANSLATION_SOURCE_NOT_READY")
        pending_detail = [str(row.get("sku") or row.get("official_sku")) for row in self.records or []
                          if str(row.get("detail_status") or "").upper() in {"PENDING", "DETAIL_PENDING", "INCOMPLETE", "ACCESS_INTERRUPTED", "403", "429", "CHALLENGE", "TIMEOUT"}
                          and (str(row.get("desc_es") or "").strip() or str(row.get("details_es") or "").strip())]
        if pending_detail:
            return StageResult("BLOCKED", {"reason": "DETAIL_SOURCE_NOT_READY", "skus": pending_detail}, "TRANSLATION_SOURCE_NOT_READY")
        return StageResult("PASS", {"translation_source_ready": True, "source_hashes": len(self.records or [])})
    def _registry_ingest(self) -> StageResult:
        return StageResult("PASS", {"registry": "existing_translation_v1_adapter", "production_registry_write": False})
    def _translation_plan(self) -> StageResult:
        self.translation_plan = plan_translations(self.records or [], approved=self.approved)
        rows = [item.as_dict() for item in self.translation_plan]; _write_csv(self.directory / "translation_plan.csv", rows)
        return StageResult("PASS", {"queued": len(rows), "reused_approved": 0 if not self.approved else "field_scoped"})
    def _qwen_translate(self) -> StageResult:
        if not self.translation_plan: return StageResult("PASS", {"called": 0, "reason": "NO_CHANGED_FIELDS"})
        if not self.auto_translation: return StageResult("SKIPPED", {"reason": "AUTO_TRANSLATION_DISABLED", "queued": len(self.translation_plan)})
        if self.provider is None: return StageResult("BLOCKED", {"reason": "PROVIDER_NOT_CONFIGURED"}, "TRANSLATION_PROVIDER_MISSING")
        if not self.context.source_ready or not self.context.source_commit_id: return StageResult("BLOCKED", {"reason": "QWEN_GATE_NOT_SATISFIED"}, "QWEN_GATE_BLOCKED")
        self.translation_results = translate_plan(self.records or [], self.translation_plan, self.provider)
        _write_csv(self.directory / "translation_result.csv", [{"sku": row.get("sku"), "status": row.get("status"), "fields": json.dumps(row.get("fields"), ensure_ascii=False), "error": row.get("error", "")} for row in self.translation_results])
        return StageResult("PASS" if all(row.get("status") == "PASS" for row in self.translation_results) else "DEGRADED", {"called": len(self.translation_results), "results": self.translation_results})
    def _translation_qa(self) -> StageResult:
        if not self.translation_results:
            self.context.translation_ready = not self.translation_plan
            return StageResult("SKIPPED", {"reason": "NO_TRANSLATION_RESULTS"})
        rows = [{"sku": row.get("sku"), "qa_status": (row.get("qa") or {}).get("status"), "status": row.get("status")} for row in self.translation_results]; _write_csv(self.directory / "translation_qa.csv", rows)
        passed = all(row.get("status") == "PASS" for row in self.translation_results); self.context.translation_ready = passed
        return StageResult("PASS" if passed else "BLOCKED", {"passed": passed, "rows": rows}, "TRANSLATION_QA_FAILED" if not passed else None)
    def _translation_policy(self) -> StageResult:
        if not self.translation_results: self.context.translation_ready = not self.translation_plan; return StageResult("SKIPPED", {"reason": "NO_RESULTS"})
        if not self.auto_policy: return StageResult("SKIPPED", {"reason": "AUTO_POLICY_DISABLED"})
        self.policy_results = auto_validate(self.translation_results, source_ready=self.context.source_ready, fact_committed=bool(self.context.source_commit_id)); _write_csv(self.directory / "translation_policy.csv", self.policy_results)
        self.context.translation_ready = all(row["decision"] == "AUTO_VALIDATED" for row in self.policy_results); return StageResult("PASS" if self.context.translation_ready else "BLOCKED", {"decisions": self.policy_results}, "AUTO_POLICY_BLOCKED" if not self.context.translation_ready else None)
    def _translation_apply(self) -> StageResult:
        if not self.apply_enabled: return StageResult("SKIPPED", {"reason": "TRANSLATION_APPLY_DISABLED"})
        if not self.context.translation_ready: return StageResult("BLOCKED", {"reason": "TRANSLATION_NOT_READY"}, "TRANSLATION_APPLY_BLOCKED")
        changed = apply_to_fixture(self.records or [], self.translation_results, self.policy_results); self.context.localization_commit_id = f"fixture-localization-{self.context.workflow_run_id}"; return StageResult("PASS", {"changed_fields": changed, "commit_id": self.context.localization_commit_id})
    def _export_audit(self) -> StageResult:
        es = self.records or []; zh = self.records or []
        es_audit = audit_es(es); zh_audit = audit_zh(zh); parity = audit_parity(es, zh)
        ready = export_readiness(source_ready=self.context.source_ready, fact_committed=bool(self.context.source_commit_id), translation_ready=self.context.translation_ready, es_audit=es_audit, zh_audit=zh_audit, parity=parity)
        self.context.export_ready = bool(ready["export_ready"]); self.report.update({"ES audit": es_audit, "ZH audit": zh_audit, "Parity audit": parity})
        _write_json(self.directory / "export_readiness.json", ready); _write_csv(self.directory / "export_es_audit.csv", [{"status": es_audit["status"], "missing": json.dumps(es_audit.get("missing_required"), ensure_ascii=False)}]); _write_csv(self.directory / "export_zh_audit.csv", [{"status": zh_audit["status"], "issues": json.dumps(zh_audit.get("issues"), ensure_ascii=False)}]); _write_csv(self.directory / "export_parity_audit.csv", [{"status": parity["status"], "issues": json.dumps(parity.get("issues"), ensure_ascii=False)}])
        return StageResult("PASS" if self.context.export_ready else "BLOCKED", ready, "EXPORT_NOT_READY" if not self.context.export_ready else None)
    def _export_write(self) -> StageResult:
        if not self.auto_export: return StageResult("SKIPPED", {"reason": "AUTO_EXPORT_DISABLED"})
        if not self.context.export_ready: return StageResult("BLOCKED", {"reason": "EXPORT_GATE_BLOCKED"}, "EXPORT_GATE_BLOCKED")
        staging = self.directory / "staging"; staging.mkdir(exist_ok=True); _write_json(staging / "es.json", self.records or []); _write_json(staging / "zh.json", self.records or []); return StageResult("PASS", {"staging": str(staging), "atomic": True})
    def _final_state(self) -> str:
        blockers = [item for item in self.stages.values() if item.status in {"BLOCKED", "FAILED"}]
        if any(item.status == "FAILED" for item in blockers): return "FAILED"
        if blockers: return "BLOCKED"
        if any(item.status == "DEGRADED" for item in self.stages.values()) or not self.context.export_ready: return "DEGRADED"
        return "SUCCESS"


def run_workflow_v2(cfg: Mapping[str, Any], *, business_date: str | None = None, run_id: str | None = None,
                    resume: bool = False, records: list[dict[str, Any]] | None = None, provider: Any = None,
                    expected_skus: Iterable[str] | None = None, expected_new_skus: Iterable[str] | None = None,
                    expected_reappeared_skus: Iterable[str] | None = None, dry_run: bool = True,
                    auto_translation: bool | None = None, auto_policy: bool | None = None, auto_export: bool | None = None,
                    apply_enabled: bool = False, production_apply: bool = False) -> dict[str, Any]:
    root = Path(cfg.get("project_root") or ".") / "runtime" / "reports" / "workflow_v2"
    options = dict(cfg.get("workflow_v2") or {})
    context = new_context(root, business_date=business_date, run_id=run_id)
    runner = WorkflowV2Runner(root=root, context=context, records=records, expected_skus=expected_skus, expected_new_skus=expected_new_skus, expected_reappeared_skus=expected_reappeared_skus, provider=provider, auto_translation=options.get("auto_translation", {}).get("enabled", False) if auto_translation is None else auto_translation, auto_policy=options.get("auto_policy_approval", {}).get("enabled", False) if auto_policy is None else auto_policy, auto_export=options.get("auto_export", {}).get("enabled", False) if auto_export is None else auto_export, apply_enabled=apply_enabled, dry_run=dry_run, production_apply=production_apply)
    return runner.run(resume=resume).as_dict()

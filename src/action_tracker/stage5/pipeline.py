"""Deterministic Stage 5 planning, guarding, provenance and artifact storage.

This module has no model loader and no production store dependency.  A CLI may
provide raw outputs from the frozen local adapter, but this module alone owns
the contracts, candidate identity, guards, evaluation and immutable artifacts.
"""
from __future__ import annotations

import csv
import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import tempfile
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..dictionary_resolver import FieldResolution, resolve_record
from ..exporting.dictionary_join import DictionaryContext
from ..services.hashing import localization_source_hash
from ..translation.model_guard import ModelOutputCheck, numeric_tokens, technical_tokens, validate_model_output


FIELDS = ("name", "cat1", "cat2", "spec", "description", "details")
FIELD_TO_SOURCE = {
    "name": "name_es", "cat1": "cat1_es", "cat2": "cat2_es",
    "spec": "spec_es", "description": "desc_es", "details": "details_es",
}
RULE_SOURCES = frozenset({
    "manual_override", "product_dictionary", "category_dictionary", "term_dictionary", "model_cache",
})
FAILURE_COLUMNS = (
    "failure_id", "batch", "sku", "field", "source", "resolver", "model_prediction",
    "final_candidate", "failure_type", "severity", "root_cause", "guard_detected",
    "escaped_guard", "evidence", "action", "owner", "status",
)
REVIEW_COLUMNS = (
    "review_id", "candidate_id", "batch_id", "sku", "field", "spanish_source",
    "resolver_result", "model_candidate", "guard_findings", "historical_chinese",
    "dictionary_evidence", "category", "brand", "numeric_facts", "technical_tokens",
    "proposed_disposition", "review_status", "reviewer", "reviewed_at",
)


class ContractError(ValueError):
    """A frozen Stage 5 contract or input precondition was violated."""


class ImmutableArtifactError(RuntimeError):
    """An existing immutable artifact differs from the requested content."""


@dataclass(frozen=True)
class Stage5Contracts:
    input_contract: dict[str, Any]
    ownership: dict[str, Any]
    pipeline: dict[str, Any]
    guard: dict[str, Any]
    hashes: dict[str, str]


@dataclass(frozen=True)
class FieldPlan:
    row: dict[str, Any]
    field: str
    source_value: str
    request_id: str
    resolver: FieldResolution | None
    resolution_path: str
    reason: str
    allowed_brand_phrases: tuple[str, ...] = ()


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_tree(path: Path) -> str:
    digest = hashlib.sha256()
    files = sorted(item for item in Path(path).rglob("*") if item.is_file())
    for item in files:
        relative = item.relative_to(path).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(bytes.fromhex(sha256_file(item)))
    return digest.hexdigest()


def tokenizer_sha256(model_path: Path) -> str | None:
    files = [
        item for item in Path(model_path).iterdir()
        if item.is_file() and ("token" in item.name.lower() or item.name == "special_tokens_map.json")
    ]
    if not files:
        return None
    digest = hashlib.sha256()
    for item in sorted(files):
        name = item.name.encode("utf-8")
        digest.update(len(name).to_bytes(8, "big"))
        digest.update(name)
        digest.update(bytes.fromhex(sha256_file(item)))
    return digest.hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"CONTRACT_READ_FAILED: {path}") from exc
    if not isinstance(value, dict):
        raise ContractError(f"CONTRACT_NOT_OBJECT: {path}")
    return value


def load_contracts(repo: Path) -> Stage5Contracts:
    root = Path(repo)
    locations = {
        "input_contract": root / "config/stage5/stage5_input_contract.json",
        "ownership": root / "config/stage5/stage5_field_ownership.json",
        "pipeline": root / "config/stage5/stage5_pipeline_contract.json",
        "guard": root / "config/stage5/stage5_guard_policy.json",
    }
    values = {key: _load_json(path) for key, path in locations.items()}
    return Stage5Contracts(
        values["input_contract"], values["ownership"], values["pipeline"], values["guard"],
        {key: sha256_file(path) for key, path in locations.items()},
    )


def _git(repo: Path, *args: str) -> str | None:
    try:
        return subprocess.run(
            ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def environment_manifest(repo: Path) -> dict[str, Any]:
    packages = {}
    for name in ("torch", "transformers", "peft", "bitsandbytes"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    status = _git(repo, "status", "--porcelain")
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "packages": packages,
        "git_commit": _git(repo, "rev-parse", "HEAD"),
        "git_worktree_clean": status == "",
        "git_status_line_count": len(status.splitlines()) if status is not None else None,
    }


def validate_frozen_identity(repo: Path, contracts: Stage5Contracts) -> dict[str, Any]:
    root = Path(repo)
    reference = contracts.pipeline["stage4_reference"]
    model = root / reference["base_model_path"]
    adapter = root / reference["adapter_path"]
    stage4_contract = root / reference["stage4_inference_contract_path"]
    state_path = root / "runtime/training/qwen3_8b/20260911/stage4_recovery_state.json"
    state = _load_json(state_path)
    observed = {
        "base_model_config_sha256": sha256_file(model / "config.json"),
        "tokenizer_sha256": tokenizer_sha256(model),
        "adapter_tree_sha256": sha256_tree(adapter),
        "stage4_inference_contract_sha256": sha256_file(stage4_contract),
    }
    expected = {
        "base_model_config_sha256": reference["base_model_config_sha256"],
        "tokenizer_sha256": reference["tokenizer_sha256"],
        "adapter_tree_sha256": reference["adapter_tree_sha256"],
        "stage4_inference_contract_sha256": reference["stage4_inference_contract_sha256"],
    }
    mismatches = [key for key in expected if observed.get(key) != expected[key]]
    required_state = reference["recovery_state_required_for_shadow"]
    if state.get("state") != required_state:
        mismatches.append("stage4_recovery_state")
    if state.get("production_writes_authorized") is not False:
        mismatches.append("stage4_production_write_boundary")
    if mismatches:
        raise ContractError("FROZEN_IDENTITY_MISMATCH: " + ",".join(sorted(set(mismatches))))
    return {
        **observed,
        "model_path": str(model.resolve()),
        "adapter_path": str(adapter.resolve()),
        "stage4_inference_contract_path": str(stage4_contract.resolve()),
        "stage4_recovery_state": state.get("state"),
        "stage4_full_release": bool((state.get("gate_results") or {}).get("FULL_STAGE4_RELEASE")),
        "training_authorized": bool(state.get("training_authorized")),
        "production_writes_authorized": bool(state.get("production_writes_authorized")),
    }


def _source_record(source: Mapping[str, Any]) -> dict[str, str]:
    return {
        "name_es": str(source.get("name") or "").strip(),
        "cat1_es": str(source.get("cat1") or "").strip(),
        "cat2_es": str(source.get("cat2") or "").strip(),
        "spec_es": str(source.get("spec") or "").strip(),
        "desc_es": str(source.get("description") or "").strip(),
        "details_es": str(source.get("details") or "").strip(),
    }


def _parse_source_message(row: Mapping[str, Any]) -> dict[str, str]:
    messages = row.get("messages")
    if not isinstance(messages, list) or len(messages) != 1 or messages[0].get("role") != "user":
        raise ContractError("SOURCE_MESSAGE_ROLES_INVALID")
    try:
        source = json.loads(str(messages[0].get("content") or ""))
    except json.JSONDecodeError as exc:
        raise ContractError("SOURCE_MESSAGE_JSON_INVALID") from exc
    if not isinstance(source, dict) or set(source) != set(FIELDS):
        raise ContractError("SOURCE_FIELDS_INVALID")
    if any(not isinstance(source[field], str) for field in FIELDS):
        raise ContractError("SOURCE_FIELD_NOT_STRING")
    return {field: source[field].strip() for field in FIELDS}


def validate_input_rows(rows: Iterable[Mapping[str, Any]], contracts: Stage5Contracts) -> list[dict[str, Any]]:
    required = set(contracts.input_contract["required_metadata"])
    allowed_reasons = set(contracts.input_contract["allowed_selection_reasons"])
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    batch_identity: tuple[str, str, str] | None = None
    for position, raw in enumerate(rows, start=1):
        metadata = raw.get("metadata")
        if not isinstance(metadata, Mapping) or required - set(metadata):
            missing = sorted(required - set(metadata or {}))
            raise ContractError(f"INPUT_METADATA_MISSING line={position} fields={','.join(missing)}")
        source = _parse_source_message(raw)
        sku = str(metadata.get("sku") or "").strip()
        canonical_id = str(metadata.get("canonical_id") or "").strip()
        batch_id = str(metadata.get("batch_id") or "").strip()
        run_id = str(metadata.get("run_id") or "").strip()
        observation_date = str(metadata.get("observation_date") or "").strip()
        try:
            date.fromisoformat(observation_date)
        except ValueError as exc:
            raise ContractError(f"OBSERVATION_DATE_INVALID line={position}") from exc
        reasons_value = metadata.get("selection_reasons")
        if not isinstance(reasons_value, list) or not reasons_value:
            raise ContractError(f"SELECTION_REASONS_INVALID line={position}")
        reasons = tuple(sorted({str(item).strip().upper() for item in reasons_value if str(item).strip()}))
        if not reasons or set(reasons) - allowed_reasons:
            raise ContractError(f"SELECTION_REASON_NOT_ALLOWED line={position}")
        if not sku or not canonical_id or not batch_id or not run_id:
            raise ContractError(f"IDENTITY_FIELD_EMPTY line={position}")
        computed_hash = localization_source_hash(_source_record(source))
        source_hash = str(metadata.get("source_hash") or "").strip().lower()
        if source_hash != computed_hash:
            raise ContractError(f"SOURCE_HASH_MISMATCH line={position} sku={sku}")
        key = (sku, source_hash)
        if key in seen:
            raise ContractError(f"DUPLICATE_INPUT sku={sku}")
        seen.add(key)
        identity = (batch_id, run_id, observation_date)
        if batch_identity is not None and identity != batch_identity:
            raise ContractError("MIXED_BATCH_IDENTITY")
        batch_identity = identity
        normalized.append({
            "messages": [{"role": "user", "content": canonical_json(source)}],
            "metadata": {
                **dict(metadata), "sku": sku, "canonical_id": canonical_id,
                "batch_id": batch_id, "run_id": run_id, "observation_date": observation_date,
                "source_hash": source_hash, "selection_reasons": list(reasons),
            },
            "source": source,
        })
    if not normalized:
        raise ContractError("EMPTY_BATCH")
    return sorted(normalized, key=lambda row: (row["metadata"]["sku"], row["metadata"]["source_hash"]))


def _request_id(row: Mapping[str, Any], field: str, contracts: Stage5Contracts) -> str:
    metadata = row["metadata"]
    payload = [
        "stage5-request-v1", metadata["batch_id"], metadata["sku"], metadata["source_hash"], field,
        contracts.pipeline["inference"]["task_id"], contracts.hashes["pipeline"],
    ]
    return sha256_bytes(canonical_json(payload).encode("utf-8"))


def plan_batch(
    rows: list[dict[str, Any]], context: DictionaryContext, contracts: Stage5Contracts,
) -> list[FieldPlan]:
    allowed_model_fields = set(contracts.pipeline["resolver"]["model_allowed_fields"])
    plans: list[FieldPlan] = []
    for row in rows:
        source = row["source"]
        record = {"sku": row["metadata"]["sku"], **_source_record(source)}
        resolution = resolve_record(record, context)
        brand_field = resolution.fields.get("brand")
        allowed_brands = (
            (brand_field.value,) if brand_field and brand_field.status == "READY" and brand_field.value else ()
        )
        for field in FIELDS:
            source_value = source[field]
            request_id = _request_id(row, field, contracts)
            if not source_value:
                plans.append(FieldPlan(row, field, source_value, request_id, None, "SOURCE", "SOURCE_AMBIGUOUS", allowed_brands))
                continue
            resolved = resolution.fields.get(field)
            safe_rule = bool(
                resolved and resolved.status == "READY" and resolved.source in RULE_SOURCES
                and not (
                    resolved.source in {"product_dictionary", "model_cache"}
                    and resolution.source_hash_status != "MATCH"
                )
            )
            if safe_rule and resolved is not None:
                check = validate_model_output(
                    {field: source_value}, {field: resolved.value}, expected_fields=[field],
                    allowed_brand_phrases=allowed_brands,
                )
                if check.accepted:
                    plans.append(FieldPlan(row, field, source_value, request_id, resolved, "RULE", "RESOLVER_READY", allowed_brands))
                    continue
            if field not in allowed_model_fields:
                reason = "RESOLVER_FAILURE" if resolved is None or resolved.status != "READY" else "RULE_GUARD_REJECT"
                plans.append(FieldPlan(row, field, source_value, request_id, resolved, "RESOLVER", reason, allowed_brands))
                continue
            plans.append(FieldPlan(row, field, source_value, request_id, resolved, "MODEL", "RESOLVER_GAP", allowed_brands))
    return plans


def model_requests(plans: Iterable[FieldPlan]) -> list[dict[str, Any]]:
    return [
        {
            "request_id": plan.request_id,
            "batch_id": plan.row["metadata"]["batch_id"],
            "sku": plan.row["metadata"]["sku"],
            "source_hash": plan.row["metadata"]["source_hash"],
            "field": plan.field,
            "source": plan.source_value,
        }
        for plan in plans if plan.resolution_path == "MODEL"
    ]


def strict_parse_prediction(raw_output: str | None, field: str) -> dict[str, str] | None:
    if raw_output is None:
        return None
    try:
        parsed = json.loads(raw_output.strip())
    except (AttributeError, json.JSONDecodeError):
        return None
    if not isinstance(parsed, dict) or set(parsed) != {field} or not isinstance(parsed.get(field), str):
        return None
    return {field: parsed[field].strip()}


def _candidate_id(plan: FieldPlan, contracts: Stage5Contracts, identity: Mapping[str, Any], dictionary_hash: str) -> str:
    metadata = plan.row["metadata"]
    payload = [
        "stage5-candidate-v1", metadata["batch_id"], metadata["sku"], metadata["source_hash"], plan.field,
        contracts.pipeline["resolver"]["version"], contracts.hashes, dictionary_hash,
        identity["base_model_config_sha256"], identity["tokenizer_sha256"], identity["adapter_tree_sha256"],
    ]
    return sha256_bytes(canonical_json(payload).encode("utf-8"))


def _stable_timestamp(observation_date: str) -> str:
    return f"{observation_date}T00:00:00+00:00"


def build_batch(
    plans: list[FieldPlan], raw_outputs: Mapping[str, str | None], contracts: Stage5Contracts,
    identity: Mapping[str, Any], *, dictionary_hash: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    reviews: list[dict[str, Any]] = []
    hard_reasons = set(contracts.guard["hard_reasons"])
    failure_classes = contracts.guard["failure_classes"]
    for plan in plans:
        metadata = plan.row["metadata"]
        resolver_payload = None if plan.resolver is None else {
            "value": plan.resolver.value, "source": plan.resolver.source,
            "status": plan.resolver.status, "approval_status": plan.resolver.approval_status,
        }
        model_invoked = plan.resolution_path == "MODEL"
        raw_output = raw_outputs.get(plan.request_id) if model_invoked else None
        parsed = strict_parse_prediction(raw_output, plan.field) if model_invoked else None
        if plan.resolution_path == "RULE" and plan.resolver is not None:
            candidate_value = plan.resolver.value
            check = validate_model_output(
                {plan.field: plan.source_value}, {plan.field: candidate_value}, expected_fields=[plan.field],
                allowed_brand_phrases=plan.allowed_brand_phrases,
            )
            status = "RULE_RESOLVED"
            final_candidate = candidate_value if check.accepted else None
            review_status = "NOT_REQUIRED" if check.accepted else "PENDING"
        elif plan.resolution_path == "MODEL":
            check = validate_model_output(
                {plan.field: plan.source_value}, parsed, expected_fields=[plan.field],
                allowed_brand_phrases=plan.allowed_brand_phrases,
            )
            candidate_value = parsed.get(plan.field) if parsed else None
            status = "GUARD_PASS_PENDING_REVIEW" if check.accepted else (
                "MODEL_FAILURE" if "JSON_PARSE" in check.reasons or "SCHEMA" in check.reasons else "GUARD_REJECT"
            )
            final_candidate = candidate_value if check.accepted else None
            review_status = "PENDING"
        else:
            parsed = None
            candidate_value = None
            if plan.resolver is not None and plan.reason == "RULE_GUARD_REJECT":
                check = validate_model_output(
                    {plan.field: plan.source_value}, {plan.field: plan.resolver.value},
                    expected_fields=[plan.field], allowed_brand_phrases=plan.allowed_brand_phrases,
                )
            else:
                check = ModelOutputCheck(False, (plan.reason,), {plan.field: (plan.reason,)})
            status = plan.reason
            final_candidate = None
            review_status = "PENDING"
        candidate_id = _candidate_id(plan, contracts, identity, dictionary_hash)
        timestamp = _stable_timestamp(metadata["observation_date"])
        field_reasons = list(check.field_reasons.get(plan.field, check.reasons))
        candidate = {
            "candidate_id": candidate_id,
            "run_id": metadata["run_id"],
            "batch_id": metadata["batch_id"],
            "observation_date": metadata["observation_date"],
            "sku": metadata["sku"],
            "canonical_id": metadata["canonical_id"],
            "source_hash": metadata["source_hash"],
            "selection_reasons": metadata["selection_reasons"],
            "source_field": plan.field,
            "source_spanish_value": plan.source_value,
            "dictionary_result": resolver_payload,
            "resolver_result": resolver_payload,
            "resolver_confidence": "DETERMINISTIC" if plan.resolution_path == "RULE" else "UNRESOLVED",
            "resolver_reason": plan.reason,
            "model_invoked": model_invoked,
            "model_adapter_id": identity["adapter_tree_sha256"] if model_invoked else None,
            "base_model_hash": identity["base_model_config_sha256"] if model_invoked else None,
            "tokenizer_hash": identity["tokenizer_sha256"] if model_invoked else None,
            "contract_hash": contracts.hashes["pipeline"],
            "guard_policy_hash": contracts.hashes["guard"],
            "prompt_task_id": contracts.pipeline["inference"]["task_id"] if model_invoked else None,
            "raw_model_output": raw_output,
            "parsed_candidate": candidate_value,
            "guard_result": {
                "accepted": bool(check.accepted), "reasons": list(check.reasons),
                "field_reasons": field_reasons,
            },
            "final_candidate": final_candidate,
            "status": status,
            "review_status": review_status,
            "reviewer": "",
            "human_disposition": "",
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        candidates.append(candidate)
        if review_status == "PENDING":
            review_id = sha256_bytes(f"stage5-review-v1|{candidate_id}".encode("utf-8"))
            reviews.append({
                "review_id": review_id, "candidate_id": candidate_id, "batch_id": metadata["batch_id"],
                "sku": metadata["sku"], "field": plan.field, "spanish_source": plan.source_value,
                "resolver_result": canonical_json(resolver_payload), "model_candidate": candidate_value or "",
                "guard_findings": "|".join(field_reasons), "historical_chinese": "",
                "dictionary_evidence": canonical_json(resolver_payload),
                "category": plan.row["source"].get("cat1", ""), "brand": "",
                "numeric_facts": canonical_json(numeric_tokens(plan.source_value)),
                "technical_tokens": canonical_json(technical_tokens(plan.source_value)),
                "proposed_disposition": status,
                "review_status": "PENDING", "reviewer": "", "reviewed_at": "",
            })
        if not check.accepted or plan.resolution_path not in {"RULE", "MODEL"}:
            reasons = field_reasons or [plan.reason]
            failure_type = failure_classes.get(reasons[0], plan.reason)
            failure_id = sha256_bytes(f"stage5-failure-v1|{candidate_id}|{'|'.join(reasons)}".encode("utf-8"))
            failures.append({
                "failure_id": failure_id, "batch": metadata["batch_id"], "sku": metadata["sku"],
                "field": plan.field, "source": plan.source_value,
                "resolver": canonical_json(resolver_payload), "model_prediction": candidate_value or "",
                "final_candidate": final_candidate or "", "failure_type": failure_type,
                "severity": "P1" if any(reason in hard_reasons for reason in reasons) else "P2",
                "root_cause": "source" if plan.reason == "SOURCE_AMBIGUOUS" else (
                    "resolver" if plan.resolution_path == "RESOLVER" else (
                        "parser" if status == "MODEL_FAILURE" else "validator_guard"
                    )
                ),
                "guard_detected": bool(field_reasons), "escaped_guard": False,
                "evidence": "|".join(reasons), "action": "HUMAN_REVIEW_REQUIRED",
                "owner": "human_reviewer", "status": "OPEN",
            })
    candidates.sort(key=lambda item: (item["sku"], FIELDS.index(item["source_field"]), item["candidate_id"]))
    failures.sort(key=lambda item: (item["sku"], item["field"], item["failure_id"]))
    reviews.sort(key=lambda item: (item["sku"], item["field"], item["review_id"]))
    evaluation = evaluate_batch(candidates, failures, reviews)
    return candidates, failures, reviews, evaluation


def evaluate_batch(
    candidates: list[dict[str, Any]], failures: list[dict[str, Any]], reviews: list[dict[str, Any]],
) -> dict[str, Any]:
    sku_rows: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        sku_rows.setdefault(candidate["sku"], candidate)
    reasons = Counter(
        reason for candidate in candidates for reason in candidate["guard_result"]["reasons"]
    )
    model_candidates = [candidate for candidate in candidates if candidate["model_invoked"]]
    guard_pass = [candidate for candidate in model_candidates if candidate["guard_result"]["accepted"]]
    guard_reject = [candidate for candidate in model_candidates if not candidate["guard_result"]["accepted"]]
    escaped = [candidate for candidate in candidates if candidate["guard_result"]["accepted"] and candidate["guard_result"]["reasons"]]
    selections = Counter(reason for row in sku_rows.values() for reason in row["selection_reasons"])
    human = Counter(row["review_status"] for row in reviews)
    return {
        "batch_id": candidates[0]["batch_id"] if candidates else None,
        "eligible_sku_count": len(sku_rows),
        "new_count": selections["NEW"],
        "source_hash_changed_count": selections["SOURCE_HASH_CHANGED"],
        "needs_review_count": selections["NEEDS_REVIEW"],
        "resolver_gap_input_count": selections["RESOLVER_GAP"],
        "field_count": len(candidates),
        "resolver_coverage": sum(item["status"] == "RULE_RESOLVED" for item in candidates),
        "resolver_gap": len(model_candidates),
        "model_invocation_count": len(model_candidates),
        "model_invocation_ratio": len(model_candidates) / len(candidates) if candidates else 0.0,
        "guard_pass": len(guard_pass),
        "guard_reject": len(guard_reject),
        "numeric_reject": reasons["NUMERIC_DROPPED"] + reasons["NUMERIC_HALLUCINATED"],
        "unit_reject": reasons["UNIT_DROPPED"] + reasons["UNIT_HALLUCINATED"],
        "tech_token_reject": reasons["TECH_TOKEN_DROPPED"] + reasons["TECH_TOKEN_HALLUCINATED"],
        "category_reject": reasons["INVALID_CATEGORY"],
        "residual_language_reject": reasons["SPANISH_RESIDUAL"] + reasons["ENGLISH_RESIDUAL"],
        "schema_reject": reasons["JSON_PARSE"] + reasons["SCHEMA"],
        "human_accept_as_is": human["ACCEPT_AS_IS"],
        "human_minor_edit": human["ACCEPT_WITH_MINOR_EDIT"],
        "human_major_edit": human["REQUIRES_MAJOR_EDIT"],
        "human_reject": human["REJECT"],
        "human_pending": human["PENDING"],
        "unresolved": len(reviews),
        "duplicate": len(candidates) - len({item["candidate_id"] for item in candidates}),
        "pipeline_error": 0,
        "fact_hallucination_escaped": sum(
            "NUMERIC_HALLUCINATED" in item["guard_result"]["reasons"] or
            "UNIT_HALLUCINATED" in item["guard_result"]["reasons"] or
            "TECH_TOKEN_HALLUCINATED" in item["guard_result"]["reasons"]
            for item in escaped
        ),
        "source_fact_loss_escaped": sum(
            "EMPTY_REQUIRED_FIELD" in item["guard_result"]["reasons"] or
            "NUMERIC_DROPPED" in item["guard_result"]["reasons"] or
            "UNIT_DROPPED" in item["guard_result"]["reasons"] or
            "TECH_TOKEN_DROPPED" in item["guard_result"]["reasons"]
            for item in escaped
        ),
        "numeric_fact_corruption_escaped": sum(
            any(reason.startswith("NUMERIC_") for reason in item["guard_result"]["reasons"])
            for item in escaped
        ),
        "unit_fact_corruption_escaped": sum(
            any(reason.startswith("UNIT_") for reason in item["guard_result"]["reasons"])
            for item in escaped
        ),
        "tech_token_corruption_escaped": sum(
            any(reason.startswith("TECH_TOKEN_") for reason in item["guard_result"]["reasons"])
            for item in escaped
        ),
        "invalid_category_escaped": sum(
            "INVALID_CATEGORY" in item["guard_result"]["reasons"] for item in escaped
        ),
        "failure_count": len(failures),
    }


def _atomic_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() == content:
            return
        raise ImmutableArtifactError(f"IMMUTABLE_ARTIFACT_CONFLICT: {path}")
    handle, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _jsonl_bytes(rows: Iterable[Mapping[str, Any]]) -> bytes:
    return ("".join(canonical_json(dict(row)) + "\n" for row in rows)).encode("utf-8")


def _csv_bytes(rows: list[dict[str, Any]], columns: tuple[str, ...]) -> bytes:
    import io

    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8-sig")


def write_batch_artifacts(
    output_dir: Path, *, input_path: Path, contracts: Stage5Contracts,
    identity: Mapping[str, Any], environment: Mapping[str, Any], dictionary_hash: str,
    requests: list[dict[str, Any]], raw_outputs: Mapping[str, str | None],
    candidates: list[dict[str, Any]], failures: list[dict[str, Any]],
    reviews: list[dict[str, Any]], evaluation: dict[str, Any],
) -> dict[str, Any]:
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    files = {
        "model_requests": (destination / "stage5_model_requests.jsonl", _jsonl_bytes(requests)),
        "model_outputs": (
            destination / "stage5_model_outputs.jsonl",
            _jsonl_bytes({"request_id": key, "raw_output": raw_outputs.get(key)} for key in sorted(raw_outputs)),
        ),
        "candidates": (destination / "stage5_candidates.jsonl", _jsonl_bytes(candidates)),
        "manual_review_jsonl": (destination / "stage5_manual_review_queue.jsonl", _jsonl_bytes(reviews)),
        "manual_review_csv": (destination / "stage5_manual_review_queue.csv", _csv_bytes(reviews, REVIEW_COLUMNS)),
        "failure_report": (destination / "stage5_failure_report.csv", _csv_bytes(failures, FAILURE_COLUMNS)),
        "evaluation": (
            destination / "stage5_batch_evaluation.json",
            (json.dumps(evaluation, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        ),
        "environment": (
            destination / "stage5_environment_manifest.json",
            (json.dumps(dict(environment), ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        ),
    }
    for _, (path, content) in files.items():
        _atomic_bytes(path, content)
    artifact_entries = {
        key: {"path": str(path.resolve()), "sha256": sha256_file(path), "bytes": path.stat().st_size}
        for key, (path, _) in files.items()
    }
    batch_id = evaluation["batch_id"]
    manifest = {
        "manifest_version": "stage5-batch-manifest-v1",
        "batch_id": batch_id,
        "input": {"path": str(Path(input_path).resolve()), "sha256": sha256_file(input_path)},
        "contracts": contracts.hashes,
        "frozen_identity": dict(identity),
        "dictionary_manifest_sha256": dictionary_hash,
        "environment_manifest_sha256": artifact_entries["environment"]["sha256"],
        "artifacts": artifact_entries,
        "evaluation": evaluation,
        "reference_targets_used_for_inference": false_value(),
        "production_writes": {
            "sqlite_primary": False, "master": False, "production_dictionary": False,
            "production_localization": False, "lifecycle": False, "price_facts": False,
            "availability": False,
        },
    }
    manifest_path = destination / f"{batch_id}_manifest.json"
    _atomic_bytes(manifest_path, (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    return {**manifest, "manifest_path": str(manifest_path.resolve()), "manifest_sha256": sha256_file(manifest_path)}


def false_value() -> bool:
    """Keep the no-reference/no-production evidence explicit and testable."""

    return False

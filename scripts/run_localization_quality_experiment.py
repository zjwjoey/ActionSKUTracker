"""LOCAL_ONLY experiment orchestration around the existing Translation V1.

Never writes Registry/PRIMARY/Master. Frozen sources and independent reviews
are distinct artifacts; neither candidate generation nor QA grants approval.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import uuid

from action_tracker.localization.contracts import CANONICAL_AI_FIELDS, CANONICAL_TO_ZH, SourceFacts
from action_tracker.localization.engine import LocalizationEngine
from action_tracker.localization.knowledge import KnowledgeLoader
from action_tracker.localization.pipeline import translate_pending_requests
from action_tracker.localization.providers.base import TranslationResponse
from action_tracker.localization.providers.qwen_mt import QwenMTProvider
from action_tracker.localization.resolver import TranslationResolver
from action_tracker.localization.qa import guard_translation
from action_tracker.localization.canonical_qa import canonical_guard
from action_tracker.localization.product_family import context_for_field
from action_tracker.services.hashing import localization_field_source_hash


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


def file_digest(path):
    sha = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            sha.update(block)
    return sha.hexdigest()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".writing")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), "utf8")
    temporary.replace(path)


def validate_source(row, field):
    evidence = row["field_evidence"][field]
    if evidence["field_source_hash"] != localization_field_source_hash(row["record"], field):
        raise ValueError("EXPERIMENT_SOURCE_HASH_MISMATCH")
    if evidence["status"] != "TRUSTED":
        return False
    proof = evidence.get("proof") or {}
    if not proof.get("reference") or proof.get("text") != evidence.get("source") or not evidence.get("source"):
        raise ValueError("EXPERIMENT_TRUSTED_PROOF_REQUIRED")
    return True


class EvidenceProvider:
    """Capture full usage before native finite checkpoint, including Resume.

    A sidecar closes the window between a returned response and checkpoint
    saving. It binds the entire request; it never approves its text.
    """
    def __init__(self, provider, directory):
        self.inner = provider
        self.provider, self.model = provider.provider, provider.model
        self.directory = Path(directory)
        self.actual_calls = 0
        self.reused = 0
        self.usage = []
        self.failures = []

    def finite_batch_identity(self):
        return self.inner.finite_batch_identity()

    def _path(self, request):
        return self.directory / f"{request.sku}_{request.field_name}.response.json"

    def translate(self, request):
        path = self._path(request)
        identity = digest({"request": asdict(request), "adapter": self.finite_batch_identity(),
                           "provider": self.provider, "model": self.model})
        if path.exists():
            saved = json.loads(path.read_text("utf8"))
            if saved["identity"] != identity:
                raise ValueError("EXPERIMENT_PROVIDER_REQUEST_CHANGED")
            self.reused += 1
            return TranslationResponse(**saved["response"])
        start = time.monotonic()
        self.actual_calls += 1
        try:
            response = self.inner.translate(request)
        except Exception as exc:
            self.failures.append({"sku": request.sku, "field": request.field_name,
                                  "code": getattr(exc, "code", type(exc).__name__),
                                  "retry_count": getattr(exc, "retry_count", None),
                                  "usage": getattr(exc, "usage", None)})
            raise
        if response.source_hash != request.source_hash or not response.fields.get(request.field_name):
            raise ValueError("EXPERIMENT_INVALID_PROVIDER_RESPONSE")
        self.usage.append(dict(response.usage))
        atomic_json(path, {"identity": identity, "response": asdict(response),
                           "elapsed_seconds": time.monotonic() - start})
        return response


class FiniteResolverProvider:
    def __init__(self, evidence):
        self.evidence = evidence
        self.provider, self.model = evidence.provider, evidence.model

    def translate(self, request):
        checkpoint = self.evidence.directory / f"{request.sku}_{request.field_name}.checkpoint.json"
        translate_pending_requests((request,), self.evidence, checkpoint)
        # Sidecar carries usage omitted by the native finite checkpoint.
        return TranslationResponse(**json.loads(self.evidence._path(request).read_text("utf8"))["response"])


def run(manifest_path, round_number, split, output, *, allow_provider=False, experimental=False, limit=None,
        candidate_freeze=None):
    manifest_path, output = Path(manifest_path), Path(output)
    manifest = json.loads(manifest_path.read_text("utf8"))
    part = manifest["rounds"][round_number - 1]["artifacts"][split]
    source_path = Path(part["path"])
    if hashlib.sha256(source_path.read_bytes()).hexdigest() != part["sha256"]:
        raise ValueError("EXPERIMENT_FROZEN_SAMPLE_CHANGED")
    rows = json.loads(source_path.read_text("utf8"))
    if len(rows) != part["skus"] or len({r["sku"] for r in rows}) != len(rows):
        raise ValueError("EXPERIMENT_INVALID_SKU_COHORT")
    dictionary = Path(manifest["dictionary_snapshot"])
    actual = {str(p.relative_to(dictionary)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(dictionary.rglob("*")) if p.is_file()}
    if actual != manifest["dictionary_hashes"]:
        raise ValueError("EXPERIMENT_FROZEN_DICTIONARY_CHANGED")
    repo = Path(__file__).resolve().parents[1]
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo).decode().strip()
    if split == "holdout":
        if candidate_freeze is None:
            raise ValueError("EXPERIMENT_HOLDOUT_REQUIRES_CANDIDATE_FREEZE")
        freeze = json.loads(Path(candidate_freeze).read_text("utf8"))
        if freeze.get("git_sha") != sha or freeze.get("sample_sha256") != part["sha256"]:
            raise ValueError("EXPERIMENT_HOLDOUT_FREEZE_MISMATCH")
    database = Path(manifest["source_database"])
    if not database.is_file() or file_digest(database) != manifest["source_snapshot_sha256"]:
        raise ValueError("EXPERIMENT_SOURCE_SNAPSHOT_CHANGED")
    adapter = QwenMTProvider(os.environ.get("QWEN_MT_BASE_URL") or os.environ.get("DASHSCOPE_BASE_URL") or "",
                             model=os.environ.get("QWEN_MT_MODEL") or "qwen-mt-flash")
    identity = {"contract": "LOCALIZATION_QUALITY_EXPERIMENT_V1", "git_sha": sha,
                "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
                "sample_sha256": part["sha256"], "round": round_number, "split": split,
                "experimental_historical_candidates": experimental,
                "provider": adapter.provider, "model": adapter.model,
                "adapter": adapter.finite_batch_identity(), "production_apply": False}
    identity_path = output / "run_identity.json"
    if identity_path.exists():
        if json.loads(identity_path.read_text("utf8")) != identity:
            raise ValueError("EXPERIMENT_RUN_IDENTITY_CHANGED")
    else:
        atomic_json(identity_path, identity)
    evidence = EvidenceProvider(adapter, output / "provider")
    engine = LocalizationEngine(knowledge=KnowledgeLoader(dictionary).load(),
                                experimental_historical_candidates=experimental)
    resolver = TranslationResolver(db_path=database, engine=engine,
                                   provider=FiniteResolverProvider(evidence) if allow_provider else None)
    start = time.monotonic()
    completed, cached, refused = 0, 0, 0
    try:
        for row in rows[:limit] if limit is not None else rows:
            record = row["record"]
            plan = engine.resolve(record)
            source = SourceFacts.from_record(record)
            for field in CANONICAL_AI_FIELDS:
                trusted = validate_source(row, field)
                path = output / "fields" / f"{row['sku']}_{field}.json"
                if path.exists():
                    saved = json.loads(path.read_text("utf8"))
                    if (saved.get("sku"), saved.get("field"), saved.get("field_source_hash")) != (
                            row["sku"], field, row["field_evidence"][field]["field_source_hash"]):
                        raise ValueError("EXPERIMENT_RESULT_SOURCE_CHANGED")
                    cached += 1
                    continue
                before = str(record.get(CANONICAL_TO_ZH[field]) or "")
                base = {"sku": row["sku"], "field": field, "source": row["field_evidence"][field]["source"],
                        "source_hash": source.source_hash,
                        "field_source_hash": row["field_evidence"][field]["field_source_hash"],
                        "source_status": row["field_evidence"][field]["status"], "before": before,
                        "semantic_status": "PENDING", "independent_review": False,
                        "approved": False, "production_apply": False, "quality_verified": False}
                if not trusted:
                    result = {**base, "candidate": "", "resolution_source": "SOURCE_REFUSAL",
                              "status": "REVIEW_REQUIRED", "source_eligible_for_quality": False}
                    refused += 1
                else:
                    t0 = time.monotonic()
                    if before:
                        candidate, origin, provenance = before, "EXISTING_PRIMARY_BASELINE_UNVERIFIED", {}
                    else:
                        resolved = resolver.resolve_field(record, field, allow_provider=allow_provider)
                        candidate, origin, provenance = resolved.value, resolved.source, dict(resolved.provenance)
                    if not candidate:
                        # An offline discovery is not a terminal provider result.
                        print(json.dumps({"sku": row["sku"], "field": field, "status": "PROVIDER_REQUIRED"}), flush=True)
                        continue
                    context = context_for_field(plan.context, field)
                    qa = guard_translation(source, {field: candidate}, (field,),
                                           semantic_facts=plan.semantic_facts, context=context)
                    canonical = canonical_guard(context, {field: candidate})
                    result = {**base, "candidate": candidate, "resolution_source": origin,
                              "candidate_hash": digest(candidate), "source_eligible_for_quality": True,
                              "qa": qa, "canonical_qa": canonical, "provenance": provenance,
                              "elapsed_seconds": time.monotonic() - t0,
                              "status": "PENDING_INDEPENDENT_SEMANTIC_REVIEW"}
                atomic_json(path, result)
                completed += 1
            print(json.dumps({"sku": row["sku"], "completed_fields_this_run": completed,
                              "actual_provider_calls": evidence.actual_calls}), flush=True)
    finally:
        resolver.close()
        atomic_json(output / "invocations" / f"{uuid.uuid4()}.json",
                    {"elapsed_seconds": time.monotonic() - start, "completed_fields": completed,
                     "resume_cached_fields": cached, "refused_fields": refused,
                     "actual_provider_invocations": evidence.actual_calls, "usage": evidence.usage,
                     "failures": evidence.failures,
                     "production_writes": 0, "apply_count": 0, "review_provider_calls": 0})
    return {"completed_fields": completed, "resume_cached_fields": cached,
            "actual_provider_invocations": evidence.actual_calls, "apply_count": 0}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--round", type=int, choices=(1, 2, 3), required=True)
    parser.add_argument("--split", choices=("development", "holdout"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-provider", action="store_true")
    parser.add_argument("--experimental-historical-candidates", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--candidate-freeze", type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.manifest, args.round, args.split, args.output,
                         allow_provider=args.allow_provider,
                         experimental=args.experimental_historical_candidates, limit=args.limit,
                         candidate_freeze=args.candidate_freeze)))

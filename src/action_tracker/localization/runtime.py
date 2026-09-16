"""Read-only shadow/canary/report orchestration for the Translation V1 core."""
from __future__ import annotations

import csv
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from .resolver import TranslationResolver


def _now_id(prefix: str) -> str:
    return f"{prefix}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"


def _write_report(output_dir: Path, summary: Mapping[str, Any], rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "translation_run_summary.json").write_text(json.dumps(dict(summary), ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    with (output_dir / "translation_units.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        fields = ["sku", "field_name", "source", "status", "needs_provider", "source_hash", "value", "provenance"]
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore"); writer.writeheader(); writer.writerows(rows)
    artifact_specs = {
        "tm_hits.csv": (["sku", "field_name", "source", "source_hash", "value", "provenance"], lambda row: str(row.get("source", "")).startswith("tm_")),
        "terminology_hits.csv": (["sku", "field_name", "source", "source_hash", "value", "provenance"], lambda row: str(row.get("source", "")) in {"terminology", "term_dictionary", "deterministic"}),
        "qwen_calls.csv": (["sku", "field_name", "source", "source_hash", "value", "provenance"], lambda row: str(row.get("source", "")) == "qwen_mt"),
        "qa_findings.csv": (["sku", "field_name", "rule_id", "severity", "message", "source_hash"], lambda row: bool(row.get("qa_rule_id"))),
        "blocked.csv": (["sku", "field_name", "status", "source_hash", "value"], lambda row: str(row.get("status", "")).upper() in {"PENDING", "BLOCKED"} and bool(row.get("needs_provider"))),
        "review_required.csv": (["sku", "field_name", "status", "source_hash", "value", "provenance"], lambda row: str(row.get("status", "")).upper() in {"PENDING", "REVIEW_REQUIRED"}),
    }
    for filename, (fields, predicate) in artifact_specs.items():
        with (output_dir / filename).open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader(); writer.writerows(row for row in rows if predicate(row))
    (output_dir / "manifest.json").write_text(json.dumps({"schema_version": "TRANSLATION_RUNTIME_REPORT_V1", "summary": dict(summary), "artifacts": ["translation_run_summary.json", "translation_units.csv", *sorted(artifact_specs)], "production_writes": False, "generated_at": datetime.now(timezone.utc).isoformat()}, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"output_dir": str(output_dir), **dict(summary)}


def shadow_run(records: Iterable[Mapping[str, Any]], *, output_dir: Path, run_id: str | None = None,
               resolver: TranslationResolver | None = None, db_path=None,
               allow_provider: bool = False) -> dict[str, Any]:
    # Direct library callers may provide the PRIMARY/Shadow DB without having
    # to construct the resolver themselves.  The CLI already injects the
    # resolver explicitly; this prevents silent zero-hit reports in scripts.
    resolver = resolver or TranslationResolver(db_path=db_path)
    rows: list[dict[str, Any]] = []
    counts = Counter()
    for record in records:
        for field_name, result in resolver.resolve(record, allow_provider=allow_provider).items():
            rows.append({"sku": result.sku, "field_name": field_name, "source": result.source, "status": result.status, "needs_provider": result.needs_provider, "source_hash": result.source_hash, "value": result.value, "provenance": json.dumps(dict(result.provenance), ensure_ascii=False, sort_keys=True, default=str)})
            counts[result.source] += 1
            counts["qwen_needed" if result.needs_provider else "no_qwen_needed"] += 1
    summary = {"run_id": run_id or _now_id("shadow"), "total_translation_units": len(rows), "manual_hit": counts.get("manual_field_lock", 0), "approved_revision_reuse": counts.get("approved_revision", 0), "tm_exact": counts.get("tm_exact", 0), "tm_normalized": counts.get("tm_normalized_exact", 0), "tm_context": counts.get("tm_context", 0), "terminology_or_rule": counts.get("terminology", 0) + counts.get("term_dictionary", 0) + counts.get("deterministic", 0), "qwen_translated": counts.get("qwen_mt", 0), "qwen_needed": counts.get("qwen_needed", 0), "qa_pass": counts.get("qa_pass", 0), "qa_fail": counts.get("qa_fail", 0), "review_required": counts.get("missing", 0), "blocked": counts.get("missing", 0), "production_writes": False}
    return _write_report(Path(output_dir), summary, rows)


def canary(records: Iterable[Mapping[str, Any]], *, output_dir: Path, skus: Iterable[str] | None = None,
           field_name: str | None = None, limit: int = 50, resolver: TranslationResolver | None = None, db_path=None,
           allow_provider: bool = False) -> dict[str, Any]:
    wanted = {str(s) for s in skus or ()}
    selected = []
    for record in records:
        sku = str(record.get("sku") or record.get("official_sku") or "")
        if wanted and sku not in wanted:
            continue
        selected.append(record)
        if len(selected) >= limit:
            break
    if field_name:
        # Keep the same report contract while limiting to one field.
        class OneField:
            def __init__(self, wrapped): self.wrapped = wrapped
            def resolve(self, record, *, allow_provider: bool = False):
                return {field_name: self.wrapped.resolve_field(record, field_name, allow_provider=allow_provider)}
        resolver = OneField(resolver or TranslationResolver(db_path=db_path))
    return shadow_run(selected, output_dir=output_dir, run_id=_now_id("canary"), resolver=resolver,
                      db_path=db_path, allow_provider=allow_provider)

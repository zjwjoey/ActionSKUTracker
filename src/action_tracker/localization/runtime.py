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
        fields = ["sku", "field_name", "source", "status", "needs_provider", "source_hash", "value"]
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore"); writer.writeheader(); writer.writerows(rows)
    (output_dir / "manifest.json").write_text(json.dumps({"schema_version": "TRANSLATION_RUNTIME_REPORT_V1", "summary": dict(summary), "production_writes": False, "generated_at": datetime.now(timezone.utc).isoformat()}, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"output_dir": str(output_dir), **dict(summary)}


def shadow_run(records: Iterable[Mapping[str, Any]], *, output_dir: Path, run_id: str | None = None,
               resolver: TranslationResolver | None = None) -> dict[str, Any]:
    resolver = resolver or TranslationResolver()
    rows: list[dict[str, Any]] = []
    counts = Counter()
    for record in records:
        for field_name, result in resolver.resolve(record).items():
            rows.append({"sku": result.sku, "field_name": field_name, "source": result.source, "status": result.status, "needs_provider": result.needs_provider, "source_hash": result.source_hash, "value": result.value})
            counts[result.source] += 1
            counts["qwen_needed" if result.needs_provider else "no_qwen_needed"] += 1
    summary = {"run_id": run_id or _now_id("shadow"), "total_translation_units": len(rows), "manual_hit": counts.get("manual_field_lock", 0), "approved_revision_reuse": counts.get("approved_revision", 0), "tm_exact": counts.get("tm_exact", 0), "tm_normalized": counts.get("tm_normalized_exact", 0), "tm_context": counts.get("tm_context", 0), "qwen_needed": counts.get("qwen_needed", 0), "blocked": counts.get("missing", 0), "production_writes": False}
    return _write_report(Path(output_dir), summary, rows)


def canary(records: Iterable[Mapping[str, Any]], *, output_dir: Path, skus: Iterable[str] | None = None,
           field_name: str | None = None, limit: int = 50, resolver: TranslationResolver | None = None) -> dict[str, Any]:
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
            def resolve(self, record): return {field_name: self.wrapped.resolve_field(record, field_name)}
        resolver = OneField(resolver or TranslationResolver())
    return shadow_run(selected, output_dir=output_dir, run_id=_now_id("canary"), resolver=resolver)


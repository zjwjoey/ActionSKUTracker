from __future__ import annotations

"""Action Localization Lite V1 side-channel.

This runner is deliberately independent of the formal Resolver/Registry and
never writes Master, Dictionary, State, or Production Apply data.  It creates
CSV/JSON artifacts; the companion workbook builder turns the reviewed rows
into the requested XLSX views.
"""

import argparse
import csv
import hashlib
import json
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = Path(r"F:\ActionSKUTracker")
DEFAULT_INPUT = DATA_ROOT / "runtime/localization/retranslation_batches/retranslation_500_20260918_qwen_rerun/retranslation_candidates.csv"
FIELDS = ("name", "cat1", "cat2", "spec", "description", "details")
SOURCE_COLUMNS = {"name": "name_es", "cat1": "cat1_es", "cat2": "cat2_es", "spec": "spec_es", "description": "desc_es", "details": "details_es"}
SOURCE_MARKERS = re.compile(r"\b(?:de|del|la|el|los|las|para|con|sin|una|uno|un|y|en|por|más|color|colores|tamaño|unidades|piezas|pack|set)\b", re.I)
NUMBER_TOKEN = re.compile(r"(?<![A-Za-z0-9])(?:\d+(?:[.,]\d+)?|[A-Z]{1,4}\d+[A-Z0-9-]*|\d+[A-Z][A-Z0-9-]*)(?![A-Za-z0-9])")
TECH_TOKEN = re.compile(r"(?<![A-Za-z0-9])(?:USB-C|Bluetooth(?:\s+\d+(?:\.\d+)?)?|LED|HDMI|E\d+|IP\s*-?\d+|[A-Z]{1,4}\d+[A-Z0-9-]*)(?![A-Za-z0-9])", re.I)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def stable_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return [dict(row) for row in csv.DictReader(fh)]


def write_csv(path: Path, rows: Iterable[Mapping[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: "" if row.get(key) is None else row.get(key, "") for key in fieldnames})


def group_source_rows(rows: list[dict[str, str]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        sku = str(row.get("sku") or "").strip()
        field = str(row.get("field_name") or "").strip()
        if not sku or field not in FIELDS:
            continue
        item = grouped.setdefault(sku, {"sku": sku, "fields": {}, "source_hash": row.get("source_hash", ""), "source_run_id": row.get("batch_id", ""), "source_snapshot_path": ""})
        item["fields"][field] = str(row.get("source_es") or "")
        item["source_hash"] = item["source_hash"] or str(row.get("source_hash") or "")
        item["source_run_id"] = item["source_run_id"] or str(row.get("batch_id") or "")
    return grouped


def choose_sample(source: dict[str, dict[str, Any]], rows: list[dict[str, str]], output: Path) -> list[dict[str, str]]:
    """Choose deterministic 120 representative + 30 historically difficult SKUs."""
    by_sku = {sku: [] for sku in source}
    for row in rows:
        if row.get("sku") in by_sku:
            by_sku[row["sku"]].append(row)

    def difficulty(sku: str) -> tuple[int, str]:
        rs = by_sku[sku]
        score = 0
        for row in rs:
            if row.get("qa_rule_id") or row.get("owner_decision") in {"REVIEW", "REJECT", "REVIEW_REQUIRED"}:
                score += 3
            if row.get("candidate_status") in {"REVIEW_REQUIRED", "BLOCKED"}:
                score += 2
            if row.get("old_zh") and row.get("candidate_zh") and row.get("old_zh") != row.get("candidate_zh"):
                score += 1
        return (-score, sku)

    difficult = sorted(source, key=difficulty)[:30]
    selected = set(difficult)
    representative: list[str] = []
    # First cover each observed cat1_es, then fill deterministically.
    for cat in sorted({next((r.get("source_es", "") for r in by_sku[sku] if r.get("field_name") == "cat1"), "") for sku in source}):
        for sku in sorted(source):
            if sku not in selected and next((r.get("source_es", "") for r in by_sku[sku] if r.get("field_name") == "cat1"), "") == cat:
                representative.append(sku)
                selected.add(sku)
                break
    for sku in sorted(source):
        if len(representative) >= 120:
            break
        if sku not in selected:
            representative.append(sku)
            selected.add(sku)
    chosen = difficult + representative[:120]
    manifest: list[dict[str, str]] = []
    for sku in chosen:
        fields = source[sku]["fields"]
        manifest.append({
            "sku": sku,
            "sample_type": "HISTORIC_DIFFICULT" if sku in difficult else "REPRESENTATIVE",
            "cat1_es": fields.get("cat1", ""),
            "cat2_es": fields.get("cat2", ""),
            "selection_reason": "historical difficulty signal" if sku in difficult else "deterministic category-covered representative sample",
        })
    if len(manifest) != 150 or len({row["sku"] for row in manifest}) != 150:
        raise RuntimeError(f"LITE_SAMPLE_INVALID:{len(manifest)}")
    write_csv(output / "lite_150_sample_manifest.csv", manifest, ["sku", "sample_type", "cat1_es", "cat2_es", "selection_reason"])
    return manifest


def load_successes(path: Path) -> dict[tuple[str, str], dict[str, str]]:
    if not path.exists():
        return {}
    return {(row.get("sku", ""), row.get("field_name", "")): row for row in read_csv(path)}


def load_brand_aliases(path: Path | None) -> tuple[str, ...]:
    if not path or not path.exists():
        return ()
    aliases: set[str] = set()
    for row in read_csv(path):
        for key in ("canonical_name", "aliases_es"):
            raw = str(row.get(key) or "")
            for alias in re.split(r"[|;/]", raw):
                alias = alias.strip()
                if len(alias) >= 3:
                    aliases.add(alias)
    return tuple(sorted(aliases, key=lambda value: (-len(value), value.casefold())))


def translate_sample(sample: list[dict[str, str]], source: dict[str, dict[str, Any]], output: Path, provider: Any | None, rate_limit: float) -> list[dict[str, Any]]:
    qwen_path = output / "lite_qwen_results.csv"
    existing = load_successes(qwen_path)
    # Keep every sampled field in the checkpoint, including pending rows. A
    # crash or Ctrl-C must never erase successful fields from a previous run.
    current_by_key: dict[tuple[str, str], dict[str, Any]] = dict(existing)
    checkpoint_fields = ["sku", "field_name", "source_es", "source_hash", "qwen_zh", "provider", "model", "request_id", "request_hash", "response_hash", "retry_count", "status", "error"]

    def checkpoint() -> None:
        ordered = [current_by_key[key] for key in sorted(current_by_key)]
        write_csv(qwen_path, ordered, checkpoint_fields)

    started = time.monotonic()
    stats = Counter()
    for manifest_row in sample:
        sku = manifest_row["sku"]
        item = source[sku]
        for field in FIELDS:
            text = str(item["fields"].get(field, "") or "")
            key = (sku, field)
            prior = existing.get(key)
            if prior and prior.get("source_hash") == item.get("source_hash") and prior.get("status") == "SUCCESS":
                current_by_key[key] = prior
                stats["resume_hits"] += 1
                continue
            result = {
                "sku": sku, "field_name": field, "source_es": text,
                "source_hash": item.get("source_hash", ""), "qwen_zh": "",
                "provider": getattr(provider, "provider", "") if provider else "",
                "model": getattr(provider, "model", "") if provider else "",
                "request_id": "", "request_hash": "", "response_hash": "",
                "retry_count": 0, "status": "NOT_RUN" if provider is None else "FAILED", "error": "",
            }
            if provider is not None:
                try:
                    from action_tracker.localization.providers.base import TranslationRequest
                    request = TranslationRequest(
                        sku=sku,
                        fields={SOURCE_COLUMNS[field]: text},
                        requested_fields=(field,),
                        source_hash=item.get("source_hash", "") or stable_hash({field: text}),
                        target_language="Chinese",
                        domain="e-commerce",
                        request_id=f"lite-{sku}-{field}",
                    )
                    response = provider.translate(request)
                    result.update({
                        "qwen_zh": str(response.fields.get(field, "") or ""),
                        "provider": response.provider, "model": response.model,
                        "request_id": response.request_id, "request_hash": response.request_hash,
                        "response_hash": response.response_hash, "status": "SUCCESS",
                    })
                    stats["qwen_success"] += 1
                except Exception as exc:  # provider errors are persisted for resume/audit
                    result["error"] = f"{type(exc).__name__}:{exc}"
                    stats["qwen_failed"] += 1
            else:
                stats["not_run"] += 1
            current_by_key[key] = result
            checkpoint()
            # QwenMTProvider already smooths every request start internally;
            # avoid double-sleeping when it is the active provider. Fixture or
            # custom providers still receive the runner-level pacing.
            if provider is not None and rate_limit > 0 and not hasattr(provider, "_wait_for_request_slot"):
                time.sleep(rate_limit)
    rows = [current_by_key[key] for key in sorted(current_by_key) if key[0] in {item["sku"] for item in sample}]
    checkpoint()
    summary = {
        "run_id": f"lite_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
        "sku_count": len(sample), "field_count": len(rows), "duration_seconds": round(time.monotonic() - started, 3),
        "qwen_calls": stats["qwen_success"] + stats["qwen_failed"], "qwen_success": stats["qwen_success"],
        "qwen_failed": stats["qwen_failed"], "resume_hits": stats["resume_hits"], "not_run": stats["not_run"],
        "provider": getattr(provider, "provider", "") if provider else "", "model": getattr(provider, "model", "") if provider else "",
        "master_writes": 0, "production_apply": False, "generated_at": now_iso(),
    }
    (output / "lite_run_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return rows


def review_rows(qwen_rows: list[dict[str, Any]], source: dict[str, dict[str, Any]], output: Path, brand_aliases: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    reviewed: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    observations: Counter[tuple[str, str]] = Counter()
    observation_skus: dict[tuple[str, str], list[str]] = {}
    for row in qwen_rows:
        source_text = str(row.get("source_es") or "")
        qwen = str(row.get("qwen_zh") or "")
        reviewed_zh, decision, correction_type, note = qwen, "KEEP", "", ""
        if row.get("status") != "SUCCESS":
            decision, correction_type, note = "REVIEW_REQUIRED", "OTHER", "Qwen result unavailable; owner review required"
        elif not source_text.strip():
            decision, note = "KEEP", "No Spanish source fact; no translation required"
        elif not qwen.strip():
            decision, correction_type, note = "REVIEW_REQUIRED", "MISSING_FACT", "Empty Qwen result"
        elif not re.search(r"[\u3400-\u9fff]", qwen) and SOURCE_MARKERS.search(source_text):
            decision, correction_type, note = "REVIEW_REQUIRED", "MISTRANSLATION", "Possible Spanish residual"
        else:
            if row.get("field_name") == "name":
                source_name = source_text.casefold()
                retained_brands = [alias for alias in brand_aliases if re.search(rf"(?<!\w){re.escape(alias.casefold())}(?!\w)", source_name) and re.search(rf"(?<!\w){re.escape(alias.casefold())}(?!\w)", qwen.casefold())]
                if retained_brands:
                    decision, correction_type, note = "REVIEW_REQUIRED", "BRAND_POLICY", f"Display-name brand retained: {', '.join(retained_brands[:3])}"
            nums = [token.casefold() for token in NUMBER_TOKEN.findall(source_text)]
            missing = [token for token in nums if token not in qwen.casefold()]
            tech = [token.casefold() for token in TECH_TOKEN.findall(source_text)]
            missing_tech = [token for token in tech if token not in qwen.casefold()]
            if decision == "KEEP" and missing_tech:
                decision, correction_type, note = "REVIEW_REQUIRED", "MODEL_OR_SPEC", f"Protected token missing: {','.join(missing_tech)}"
            elif decision == "KEEP" and missing:
                decision, correction_type, note = "REVIEW_REQUIRED", "NUMERIC_OR_UNIT", f"Numeric token missing: {','.join(missing)}"
        out = dict(row)
        out.update({"reviewed_zh": reviewed_zh, "review_decision": decision, "correction_type": correction_type, "review_note": note})
        reviewed.append(out)
        if decision != "KEEP":
            findings.append({"sku": row.get("sku", ""), "field_name": row.get("field_name", ""), "source_es": source_text, "qwen_zh": qwen, "reviewed_zh": reviewed_zh, "decision": decision, "correction_type": correction_type, "review_note": note})
            observation_key = (correction_type, note.split(":", 1)[0])
            observations[observation_key] += 1
            examples = observation_skus.setdefault(observation_key, [])
            sku = str(row.get("sku", ""))
            if sku and sku not in examples and len(examples) < 5:
                examples.append(sku)
    write_csv(output / "lite_translation_review_findings.csv", findings, ["sku", "field_name", "source_es", "qwen_zh", "reviewed_zh", "decision", "correction_type", "review_note"])
    obs_rows = [{"pattern": key[0], "example_skus": ";".join(observation_skus.get(key, [])), "count": count, "suggested_action": key[1]} for key, count in sorted(observations.items())]
    write_csv(output / "review_observations.csv", obs_rows, ["pattern", "example_skus", "count", "suggested_action"])
    return reviewed


def build_provider(args: argparse.Namespace) -> Any | None:
    if not args.allow_provider:
        return None
    from action_tracker.localization.providers.qwen_mt import QwenMTProvider
    if not args.endpoint:
        raise SystemExit("--allow-provider requires --endpoint")
    return QwenMTProvider(base_url=args.endpoint, model=args.model, rate_limit_per_second=args.rate_limit_per_second)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run independent Action Localization Lite V1")
    parser.add_argument("--repo-root", default=str(REPO_ROOT))
    parser.add_argument("--data-root", default=str(DATA_ROOT))
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--mode", choices=("prepare", "translate", "review", "all"), default="prepare")
    parser.add_argument("--limit", type=int, default=150, help="Bounded sample size for smoke/testing; production Lite V1 uses 150")
    parser.add_argument("--allow-provider", action="store_true", help="Allow real Qwen-MT calls; otherwise no network is used")
    parser.add_argument("--endpoint", default="")
    parser.add_argument("--model", default="qwen-mt-flash")
    parser.add_argument("--rate-limit-per-second", type=float, default=0.5)
    parser.add_argument("--brand-dictionary", default=str(DATA_ROOT / "runtime/dictionary/brand_dictionary.csv"))
    args = parser.parse_args()
    sys.path.insert(0, str(Path(args.repo_root) / "src"))
    input_path = Path(args.input)
    output = Path(args.output_dir) if args.output_dir else Path(args.data_root) / "runtime/localization/lite_v1_150"
    output.mkdir(parents=True, exist_ok=True)
    rows = read_csv(input_path)
    source = group_source_rows(rows)
    sample = read_csv(output / "lite_150_sample_manifest.csv") if (output / "lite_150_sample_manifest.csv").exists() else choose_sample(source, rows, output)
    if args.limit < 1 or args.limit > len(sample):
        raise SystemExit(f"--limit must be between 1 and {len(sample)}")
    sample = sample[:args.limit]
    if args.mode == "prepare":
        print(json.dumps({"status": "PREPARED", "sku_count": len(sample), "output_dir": str(output), "master_writes": 0}, ensure_ascii=False))
        return 0
    provider = build_provider(args) if args.mode in {"translate", "all"} else None
    qwen_rows = read_csv(output / "lite_qwen_results.csv") if args.mode == "review" else translate_sample(sample, source, output, provider, 1.0 / args.rate_limit_per_second if args.rate_limit_per_second > 0 else 0)
    if args.mode in {"review", "all"}:
        reviewed = review_rows(qwen_rows, source, output, load_brand_aliases(Path(args.brand_dictionary)))
        write_csv(output / "lite_review_rows.csv", reviewed, list(reviewed[0].keys()) if reviewed else ["sku", "field_name"])
        (output / "lite_review_rows.json").write_text(json.dumps(reviewed, ensure_ascii=False, indent=2), encoding="utf-8")
        # Owner queue is intentionally an artifact, not an approval action.
        queue = [row for row in reviewed if row.get("review_decision") == "REVIEW_REQUIRED"]
        write_csv(output / "lite_owner_review_queue.csv", queue, list(queue[0].keys()) if queue else ["sku", "field_name"])
        (output / "lite_owner_review_queue.json").write_text(json.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8")
        summary = json.loads((output / "lite_run_summary.json").read_text(encoding="utf-8")) if (output / "lite_run_summary.json").exists() else {}
        summary.update({"review_keep": sum(row.get("review_decision") == "KEEP" for row in reviewed), "review_corrected": sum(row.get("review_decision") == "CORRECTED" for row in reviewed), "review_required": len(queue), "master_writes": 0, "production_apply": False})
        (output / "lite_run_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        # Gold candidate is a review artifact only; no row is promoted to Gold.
        write_csv(output / "gold_candidate_150.csv", [row for row in reviewed if row.get("review_decision") in {"KEEP", "CORRECTED"}], list(reviewed[0].keys()) if reviewed else ["sku", "field_name"])
        print(json.dumps({"status": "READY_FOR_REVIEW", "sku_count": len(sample), "field_count": len(reviewed), "review_required": len(queue), "master_writes": 0, "production_apply": False, "output_dir": str(output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

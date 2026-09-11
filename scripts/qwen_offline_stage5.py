"""Stage-5 offline candidate staging for the trained Qwen adapter.

This command is deliberately separate from the daily production chain.  It
accepts the same JSONL shape used by the Qwen datasets, sends only the source
messages to the local adapter, and writes reviewable candidates.  A candidate
is never auto-corrected and this module never writes Master, SQLite, the
dictionary, or the production translation queue.

The input may contain an assistant/reference message (for evaluation
fixtures), but it is ignored for inference and is never copied to the output.
The output therefore remains suitable for a production-like, targetless dry
run as well as for later manual review.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# Reuse the already-tested local generation and parsing implementation.  The
# import is intentionally from a sibling script so this staging entry point
# cannot accidentally become part of the production orchestrator.
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import compare_qwen_baselines as compare  # noqa: E402

from action_tracker.config import load_settings  # noqa: E402
from action_tracker.exporting.dictionary_join import load_dictionary_context  # noqa: E402
from action_tracker.translation.model_guard import validate_model_output  # noqa: E402


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _source_from_row(row: Mapping[str, Any]) -> dict[str, str]:
    for message in row.get("messages", []):
        if message.get("role") == "user":
            source = compare.parse_object(_text(message.get("content")))
            if isinstance(source, dict):
                return {str(key): _text(value) for key, value in source.items()}
    raise ValueError("SOURCE_MESSAGE_MISSING_OR_INVALID")


def _expected_fields(row: Mapping[str, Any], source: Mapping[str, str]) -> tuple[str, ...]:
    requested = _text((row.get("metadata") or {}).get("field"))
    if requested:
        if requested not in source:
            raise ValueError(f"REQUESTED_FIELD_NOT_IN_SOURCE: {requested}")
        return (requested,)
    # Keep a deterministic order for six-field production-like payloads.
    return tuple(field for field in compare.FIELDS if field in source)


def classify_candidate(
    row: Mapping[str, Any],
    prediction: Mapping[str, Any] | None,
    *,
    allowed_brand_phrases: Iterable[str] = (),
) -> dict[str, Any]:
    """Return a non-mutating candidate record with a guard decision."""

    source = _source_from_row(row)
    fields = _expected_fields(row, source)
    missing_fields = [field for field in fields if not _text(source.get(field))]
    if missing_fields:
        # An empty official field is not a translation task.  Keeping it out
        # of model inference prevents the adapter from inventing a value.
        metadata = row.get("metadata") or {}
        return {
            "sku": _text(metadata.get("sku")),
            "field": _text(metadata.get("field")) or "six_fields",
            "source_hash": _text(metadata.get("source_hash")),
            "source": {field: source.get(field, "") for field in fields},
            "prediction": None,
            "accepted_by_guard": False,
            "status": "REVIEW_REQUIRED",
            "reasons": ["SOURCE_EMPTY"],
            "field_reasons": {field: ["SOURCE_EMPTY"] for field in missing_fields},
        }
    check = validate_model_output(
        {field: source.get(field, "") for field in fields},
        prediction,
        expected_fields=fields,
        allowed_brand_phrases=allowed_brand_phrases,
    )
    metadata = row.get("metadata") or {}
    candidate = {
        "sku": _text(metadata.get("sku")),
        "field": _text(metadata.get("field")) or "six_fields",
        "source_hash": _text(metadata.get("source_hash")),
        "source": {field: source.get(field, "") for field in fields},
        "prediction": dict(prediction) if isinstance(prediction, Mapping) else None,
        "accepted_by_guard": bool(check.accepted),
        "status": "AUTO_READY_CANDIDATE" if check.accepted else "REVIEW_REQUIRED",
        "reasons": list(check.reasons),
        "field_reasons": {key: list(value) for key, value in check.field_reasons.items()},
    }
    return candidate


def _load_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"INVALID_JSONL line={line_no}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"ROW_NOT_OBJECT line={line_no}")
            rows.append(value)
    return rows


def _inference_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop assistant/reference messages before model inference."""

    output = []
    for row in rows:
        messages = [message for message in row.get("messages", []) if message.get("role") != "assistant"]
        if not messages or not any(message.get("role") == "user" for message in messages):
            raise ValueError("USER_MESSAGE_REQUIRED")
        output.append({"messages": messages, "metadata": dict(row.get("metadata") or {})})
    return output


def _brand_phrases(rows: list[dict[str, Any]]) -> dict[str, tuple[str, ...]]:
    cfg = load_settings(ROOT / "config" / "settings.yaml")
    context = load_dictionary_context(cfg)
    return compare.allowed_brands_by_sku(rows, context)


def stage(
    rows: list[dict[str, Any]],
    predictions: list[dict[str, Any] | None],
    *,
    allowed_brands: Mapping[str, Iterable[str]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if len(rows) != len(predictions):
        raise ValueError("PREDICTION_ROW_COUNT_MISMATCH")
    allowed_brands = allowed_brands or {}
    candidates = []
    for row, prediction in zip(rows, predictions):
        sku = _text((row.get("metadata") or {}).get("sku"))
        candidates.append(classify_candidate(row, prediction, allowed_brand_phrases=allowed_brands.get(sku, ())))
    accepted = sum(item["accepted_by_guard"] for item in candidates)
    rejected = len(candidates) - accepted
    summary = {
        "rows": len(candidates),
        "accepted_by_guard": accepted,
        "review_required": rejected,
        "json_parse_failures": sum("JSON_PARSE" in item["reasons"] for item in candidates),
        "numeric_failures": sum(any(reason.startswith("NUMERIC_") for reason in item["reasons"]) for item in candidates),
        "language_residual_failures": sum(any(reason.endswith("_RESIDUAL") for reason in item["reasons"]) for item in candidates),
        "source_empty": sum("SOURCE_EMPTY" in item["reasons"] for item in candidates),
        "production_writes": False,
    }
    return candidates, summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Qwen adapter offline candidate staging; never writes production data")
    parser.add_argument("--input", required=True, help="JSONL source fixture; same message shape as Qwen datasets")
    parser.add_argument("--output", required=True, help="JSONL candidate output")
    parser.add_argument("--model-path", default="runtime/models/Qwen3-8B")
    parser.add_argument("--adapter-path", default="runtime/training/qwen3_8b/20260908/baseline_gold_qlora_8b_20260909_clean/adapter")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--max-input-length", type=int, default=1024)
    parser.add_argument("--max-new-tokens", type=int, default=256)
    args = parser.parse_args()

    source_path = Path(args.input)
    source_path = source_path if source_path.is_absolute() else ROOT / source_path
    output_path = Path(args.output)
    output_path = output_path if output_path.is_absolute() else ROOT / output_path
    model_path = Path(args.model_path)
    model_path = model_path if model_path.is_absolute() else ROOT / model_path
    adapter_path = Path(args.adapter_path)
    adapter_path = adapter_path if adapter_path.is_absolute() else ROOT / adapter_path

    rows = _load_rows(source_path)
    if args.limit:
        rows = rows[: args.limit]
    rows = _inference_rows(rows)
    # Never send rows with an empty official source field to the model.  They
    # are still emitted as review items so the missing source is visible.
    model_rows: list[dict[str, Any]] = []
    model_positions: list[int] = []
    predictions_by_position: dict[int, dict[str, Any] | None] = {}
    for index, row in enumerate(rows):
        source = _source_from_row(row)
        fields = _expected_fields(row, source)
        if any(not _text(source.get(field)) for field in fields):
            predictions_by_position[index] = None
        else:
            model_positions.append(index)
            model_rows.append(row)
    allowed = _brand_phrases(rows)
    if model_rows:
        model_predictions = compare.run_model(
            model_path,
            model_rows,
            args.batch_size,
            args.max_input_length,
            args.max_new_tokens,
            adapter_path,
            strict_prompt=True,
        )
        for index, prediction in zip(model_positions, model_predictions):
            predictions_by_position[index] = prediction
    predictions = [predictions_by_position.get(index) for index in range(len(rows))]
    candidates, summary = stage(rows, predictions, allowed_brands=allowed)
    summary["model_inference_rows"] = len(model_rows)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        for candidate in candidates:
            handle.write(json.dumps(candidate, ensure_ascii=False, sort_keys=True) + "\n")
    manifest = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "input": str(source_path),
        "output": str(output_path),
        "model_path": str(model_path),
        "adapter_path": str(adapter_path),
        "summary": summary,
        "reference_targets_used_for_inference": False,
        "master_written": False,
        "dictionary_written": False,
        "sqlite_written": False,
    }
    output_path.with_suffix(".manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

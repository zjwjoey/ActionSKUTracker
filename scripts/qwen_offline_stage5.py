"""Stage-5 offline candidate staging for the trained Qwen adapter.

This command is deliberately separate from the daily production chain.  It
accepts the same JSONL shape used by the Qwen datasets, sends only the source
messages to the local adapter, and writes reviewable candidates. Deterministic
dictionary/category results close fields before the adapter is called; the
adapter only receives remaining field-level resolver gaps. A candidate is
never auto-corrected and this module never writes Master, SQLite, the
dictionary, or the production translation queue.

The input may contain an assistant/reference message (for evaluation
fixtures), but it is ignored for inference and is never copied to the output.
The output therefore remains suitable for a production-like, targetless dry
run as well as for later manual review.
"""
from __future__ import annotations

import argparse
import datetime as dt
import gc
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
from action_tracker.dictionary_resolver import FieldResolution, resolve_record  # noqa: E402
from action_tracker.exporting.dictionary_join import DictionaryContext, load_dictionary_context  # noqa: E402
from action_tracker.translation.model_guard import validate_model_output  # noqa: E402
from action_tracker.translation.source_fact_repair import repair_model_output  # noqa: E402


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


_RULE_SOURCES = frozenset({
    "manual_override", "product_dictionary", "category_dictionary", "term_dictionary", "model_cache",
})


def _record_for_dictionary(row: Mapping[str, Any]) -> dict[str, str]:
    """Adapt a training-style source message to the read-only dictionary resolver."""

    source = _source_from_row(row)
    metadata = row.get("metadata") or {}
    return {
        "sku": _text(metadata.get("sku")),
        "name_es": source.get("name", ""),
        "cat1_es": source.get("cat1", ""),
        "cat2_es": source.get("cat2", ""),
        "spec_es": source.get("spec", ""),
        "desc_es": source.get("description", ""),
        "details_es": source.get("details", ""),
    }


def rule_resolutions(rows: Iterable[Mapping[str, Any]], context: DictionaryContext) -> list[dict[str, FieldResolution]]:
    """Return hash-safe field closures that a local model must not replace."""

    results: list[dict[str, FieldResolution]] = []
    for row in rows:
        source = _source_from_row(row)
        expected = _expected_fields(row, source)
        resolution = resolve_record(_record_for_dictionary(row), context)
        closed: dict[str, FieldResolution] = {}
        for field in expected:
            item = resolution.fields.get(field)
            if item is None or item.status != "READY" or item.source not in _RULE_SOURCES:
                continue
            # Product and cached-model values are only safe for exactly the
            # same official source; category mapping/manual values are applied
            # to the current Spanish category and need no historical hash.
            if item.source in {"product_dictionary", "model_cache"} and resolution.source_hash_status != "MATCH":
                continue
            # A dictionary is authoritative for wording, not an excuse to
            # change official facts.  Validate its value before closing the
            # gap; an unsafe value falls back to model-gap/review handling.
            check = validate_model_output(
                {field: source.get(field, "")}, {field: item.value}, expected_fields=[field],
            )
            if not check.accepted:
                continue
            closed[field] = item
        results.append(closed)
    return results


def classify_candidate(
    row: Mapping[str, Any],
    prediction: Mapping[str, Any] | None,
    *,
    allowed_brand_phrases: Iterable[str] = (),
    rule_fields: Mapping[str, FieldResolution] | None = None,
) -> dict[str, Any]:
    """Return a non-mutating candidate record with a guard decision."""

    source = _source_from_row(row)
    fields = _expected_fields(row, source)
    rule_fields = dict(rule_fields or {})
    model_fields = tuple(field for field in fields if field not in rule_fields)
    invalid_rule_fields: dict[str, list[str]] = {}
    safe_rule_fields: dict[str, FieldResolution] = {}
    for field, item in rule_fields.items():
        check = validate_model_output(
            {field: source.get(field, "")}, {field: item.value}, expected_fields=[field],
        )
        if check.accepted:
            safe_rule_fields[field] = item
        else:
            invalid_rule_fields[field] = list(check.field_reasons.get(field, check.reasons))
    rule_fields = safe_rule_fields
    model_fields = tuple(field for field in fields if field not in rule_fields)
    rule_evidence = {
        field: {"value": item.value, "source": item.source, "status": item.status}
        for field, item in rule_fields.items()
    }
    missing_fields = [field for field in model_fields if not _text(source.get(field))]
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
            "rule_resolved_fields": rule_evidence,
            "model_gap_fields": list(model_fields),
            "rule_validation_failures": invalid_rule_fields,
            "accepted_by_guard": False,
            "status": "REVIEW_REQUIRED",
            "reasons": ["SOURCE_EMPTY"],
            "field_reasons": {field: ["SOURCE_EMPTY"] for field in missing_fields},
        }
    metadata = row.get("metadata") or {}
    if not model_fields:
        return {
            "sku": _text(metadata.get("sku")),
            "field": _text(metadata.get("field")) or "six_fields",
            "source_hash": _text(metadata.get("source_hash")),
            "source": {field: source.get(field, "") for field in fields},
            "prediction": {},
            "rule_resolved_fields": rule_evidence,
            "model_gap_fields": [],
            "rule_validation_failures": invalid_rule_fields,
            "accepted_by_guard": True,
            "status": "RULE_RESOLVED_CANDIDATE",
            "reasons": [],
            "field_reasons": {},
        }
    check = validate_model_output(
        {field: source.get(field, "") for field in model_fields},
        prediction,
        expected_fields=model_fields,
        allowed_brand_phrases=allowed_brand_phrases,
    )
    candidate = {
        "sku": _text(metadata.get("sku")),
        "field": _text(metadata.get("field")) or "six_fields",
        "source_hash": _text(metadata.get("source_hash")),
        "source": {field: source.get(field, "") for field in fields},
        "prediction": dict(prediction) if isinstance(prediction, Mapping) else None,
        "rule_resolved_fields": rule_evidence,
        "model_gap_fields": list(model_fields),
        "rule_validation_failures": invalid_rule_fields,
        "accepted_by_guard": bool(check.accepted),
        # A deterministic Guard is a hard safety check, not semantic approval.
        # Keep the candidate explicitly pending review even when no hard flag
        # fired; this prevents the offline staging adapter from being treated
        # as an approval path by downstream tools.
        "status": "GUARD_PASS_PENDING_REVIEW" if check.accepted else "REVIEW_REQUIRED",
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
    """Validate rows while retaining reference metadata outside model prompts.

    ``run_fieldwise_retry`` builds a fresh system+user prompt and never sends
    an assistant message to the adapter.  Keeping an optional reference here
    preserves the standard evaluation-row shape required by that helper.
    """

    output = []
    for row in rows:
        messages = list(row.get("messages", []))
        if not messages or not any(message.get("role") == "user" for message in messages):
            raise ValueError("USER_MESSAGE_REQUIRED")
        # ``compare.run_fieldwise_retry`` is shared with evaluation fixtures
        # and uses ``source_target`` to read the source payload.  Evaluation
        # rows normally contain an assistant/reference message, while the
        # Stage 6 source-only contract intentionally does not.  Add an empty
        # in-memory assistant sentinel solely for that parser contract; it is
        # never sent to the model and is never copied to any output artifact.
        if not any(message.get("role") == "assistant" for message in messages):
            messages.append({"role": "assistant", "content": "{}"})
        output.append({"messages": messages, "metadata": dict(row.get("metadata") or {})})
    return output


def _run_source_only_fieldwise_retry(
    model_path: Path,
    requests: list[tuple[dict[str, Any], str]],
    max_input_length: int,
    max_new_tokens: int,
    adapter_path: Path,
    batch_size: int,
) -> list[dict[str, Any] | None]:
    """Run the frozen fieldwise prompt in batches for source-only rows.

    ``compare.run_fieldwise_retry`` is intentionally serial because it is an
    evaluation helper.  Stage 6 has no reference targets and can safely batch
    independent field requests while preserving the same system prompt,
    greedy decoding, tokenizer settings, adapter and output parser.
    """
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

    if not requests:
        return []
    tok = AutoTokenizer.from_pretrained(str(model_path), local_files_only=True, trust_remote_code=True)
    tok.pad_token = tok.pad_token or tok.eos_token
    tok.padding_side = "left"
    bnb = BitsAndBytesConfig(
        load_in_4bit=True, bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.float16, bnb_4bit_use_double_quant=True,
    )
    net = AutoModelForCausalLM.from_pretrained(
        str(model_path), quantization_config=bnb, device_map={"": 0},
        torch_dtype=torch.float16, trust_remote_code=True, local_files_only=True,
    )
    from peft import PeftModel
    net = PeftModel.from_pretrained(net, str(adapter_path), local_files_only=True)
    net.eval()
    results: list[dict[str, Any] | None] = []
    width = max(1, int(batch_size))
    for start in range(0, len(requests), width):
        chunk = requests[start : start + width]
        prompts = []
        for row, field in chunk:
            source = _source_from_row(row)
            messages = [
                {"role": "system", "content": compare.FIELDWISE_SYSTEM},
                {"role": "user", "content": json.dumps({field: source.get(field, "")}, ensure_ascii=False)},
            ]
            prompts.append(tok.apply_chat_template(messages, tokenize=False, add_generation_prompt=True, enable_thinking=False))
        inputs = tok(
            prompts, return_tensors="pt", padding=True, truncation=True,
            max_length=max_input_length,
        ).to(net.device)
        with torch.no_grad():
            output = net.generate(**inputs, max_new_tokens=max_new_tokens, do_sample=False)
        generated = output[:, inputs["input_ids"].shape[1] :]
        for tokens in generated:
            results.append(compare.parse_object(tok.decode(tokens, skip_special_tokens=True).strip()))
        print(f"fieldwise {min(start + len(chunk), len(requests))}/{len(requests)}", flush=True)
    del net, tok
    gc.collect()
    torch.cuda.empty_cache()
    return results


def _brand_phrases(rows: list[dict[str, Any]]) -> dict[str, tuple[str, ...]]:
    cfg = load_settings(ROOT / "config" / "settings.yaml")
    context = load_dictionary_context(cfg)
    return compare.allowed_brands_by_sku(rows, context)


def stage(
    rows: list[dict[str, Any]],
    predictions: list[dict[str, Any] | None],
    *,
    allowed_brands: Mapping[str, Iterable[str]] | None = None,
    rule_fields_by_position: Iterable[Mapping[str, FieldResolution]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if len(rows) != len(predictions):
        raise ValueError("PREDICTION_ROW_COUNT_MISMATCH")
    allowed_brands = allowed_brands or {}
    rule_fields_list = list(rule_fields_by_position or [{} for _ in rows])
    if len(rule_fields_list) != len(rows):
        raise ValueError("RULE_RESOLUTION_ROW_COUNT_MISMATCH")
    candidates = []
    for row, prediction, rule_fields in zip(rows, predictions, rule_fields_list):
        sku = _text((row.get("metadata") or {}).get("sku"))
        candidates.append(classify_candidate(
            row, prediction, allowed_brand_phrases=allowed_brands.get(sku, ()), rule_fields=rule_fields,
        ))
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
        "rule_resolved_candidates": sum(item["status"] == "RULE_RESOLVED_CANDIDATE" for item in candidates),
        "rule_closed_fields": sum(len(item["rule_resolved_fields"]) for item in candidates),
        "model_gap_fields": sum(len(item["model_gap_fields"]) for item in candidates),
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
    # The resolver sees the current official source first. Only unresolved
    # fields become isolated model requests, so Qwen cannot decide categories
    # or overwrite a verified dictionary fact.
    cfg = load_settings(ROOT / "config" / "settings.yaml")
    context = load_dictionary_context(cfg)
    resolved_by_position = rule_resolutions(rows, context)
    model_requests: list[tuple[dict[str, Any], str]] = []
    model_request_positions: list[tuple[int, str]] = []
    predictions_by_position: dict[int, dict[str, Any] | None] = {}
    for index, row in enumerate(rows):
        source = _source_from_row(row)
        fields = _expected_fields(row, source)
        gaps = tuple(field for field in fields if field not in resolved_by_position[index])
        if any(not _text(source.get(field)) for field in gaps):
            predictions_by_position[index] = None
            continue
        predictions_by_position[index] = {}
        for field in gaps:
            model_requests.append((row, field))
            model_request_positions.append((index, field))
    allowed = _brand_phrases(rows)
    if model_requests:
        model_predictions = _run_source_only_fieldwise_retry(
            model_path, model_requests, args.max_input_length, args.max_new_tokens,
            adapter_path, args.batch_size,
        )
        for (index, field), prediction in zip(model_request_positions, model_predictions):
            if not isinstance(prediction, Mapping) or not isinstance(prediction.get(field), str):
                predictions_by_position[index] = None
                continue
            current = predictions_by_position[index]
            if isinstance(current, dict):
                current[field] = prediction[field]
    predictions = [predictions_by_position.get(index) for index in range(len(rows))]
    model_repairs_by_position: dict[int, list[dict[str, str]]] = {}
    for index, row in enumerate(rows):
        prediction = predictions[index]
        sku = _text((row.get("metadata") or {}).get("sku"))
        repaired, repairs = repair_model_output(
            _source_from_row(row), prediction,
            confirmed_brand_phrases=allowed.get(sku, ()),
        )
        predictions[index] = repaired
        if repairs:
            model_repairs_by_position[index] = repairs
    candidates, summary = stage(
        rows, predictions, allowed_brands=allowed, rule_fields_by_position=resolved_by_position,
    )
    for index, candidate in enumerate(candidates):
        candidate["raw_model_output"] = predictions_by_position.get(index)
        candidate["model_repairs"] = model_repairs_by_position.get(index, [])
    summary["model_inference_fields"] = len(model_requests)

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

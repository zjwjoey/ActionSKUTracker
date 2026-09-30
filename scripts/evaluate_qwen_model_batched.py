"""Batched read-only evaluation for a frozen Qwen/QLoRA adapter.

This keeps the evaluator's prompt, model, and guard metrics aligned with
``evaluate_qwen_model.py`` while batching generation to make large diagnostic
sets practical. It never writes Master, SQLite, dictionaries, or model files.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from action_tracker.translation.model_guard import numeric_fact_counters  # noqa: E402

NUM = re.compile(r"\d+(?:[.,]\d+)?")
SPANISH = re.compile(r"\b(?:el|la|los|las|para|con|sin|del|de|y|en|color|tamaño|producto|material|cantidad|contenido|piezas|gramos|litros)\b", re.IGNORECASE)
VALID_CAT1 = {
    "DIY五金", "办公文具", "宠物用品", "厨房餐具", "服饰鞋包", "个人美容",
    "家居布置", "家务清洁", "旅行用品", "食品饮料", "数码影音", "玩具",
    "兴趣手作", "园艺户外", "运动用品",
}


def parse_object(text: str) -> dict | None:
    cleaned = str(text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE | re.DOTALL).strip()
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            value = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError:
            return None
    return value if isinstance(value, dict) else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-path", required=True)
    ap.add_argument("--adapter-path", required=True)
    ap.add_argument("--test-file", required=True)
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--batch-size", type=int, default=2)
    ap.add_argument("--max-input-length", type=int, default=1024)
    ap.add_argument("--max-new-tokens", type=int, default=512)
    ap.add_argument("--output", required=True)
    ap.add_argument("--predictions-output")
    ap.add_argument(
        "--sku",
        action="append",
        default=[],
        help="Optional repeatable SKU filter for targeted diagnostics; omitted means the full test file.",
    )
    args = ap.parse_args()
    if args.batch_size < 1:
        raise SystemExit("BATCH_SIZE_INVALID")

    model = Path(args.model_path).resolve()
    adapter = Path(args.adapter_path).resolve()
    test = Path(args.test_file).resolve()
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    tok = AutoTokenizer.from_pretrained(str(model), local_files_only=True, trust_remote_code=True)
    tok.pad_token = tok.pad_token or tok.eos_token
    tok.padding_side = "left"
    bnb = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_compute_dtype=torch.float16, bnb_4bit_use_double_quant=True)
    net = AutoModelForCausalLM.from_pretrained(str(model), quantization_config=bnb, device_map={"": 0}, torch_dtype=torch.float16, trust_remote_code=True, local_files_only=True)
    from peft import PeftModel
    net = PeftModel.from_pretrained(net, str(adapter), local_files_only=True)
    net.eval()

    rows = [json.loads(line) for line in test.open(encoding="utf-8") if line.strip()]
    if args.sku:
        wanted = {str(value) for value in args.sku}
        rows = [row for row in rows if str((row.get("metadata") or {}).get("sku")) in wanted]
    rows = rows[: args.limit]
    parsed_count = schema_count = field_count = nonempty_count = numeric_ok = numeric_hallucination = spanish = 0
    category_total = category_ok = 0
    failures: list[dict[str, str]] = []
    row_audits: list[dict] = []

    for start in range(0, len(rows), args.batch_size):
        batch = rows[start : start + args.batch_size]
        prompts = [tok.apply_chat_template(row["messages"][:2], tokenize=False, add_generation_prompt=True, enable_thinking=False) for row in batch]
        encoded = tok(prompts, return_tensors="pt", padding=True, truncation=True, max_length=args.max_input_length).to(net.device)
        with torch.inference_mode():
            generated = net.generate(**encoded, max_new_tokens=args.max_new_tokens, do_sample=False)
        input_width = encoded["input_ids"].shape[1]
        for row, output_ids in zip(batch, generated):
            source = parse_object(next(m["content"] for m in row["messages"] if m["role"] == "user")) or {}
            expected = parse_object(next(m["content"] for m in row["messages"] if m["role"] == "assistant")) or {}
            field = row["metadata"].get("field")
            expected_fields = {str(field)} if field else set(expected)
            text = tok.decode(output_ids[input_width:], skip_special_tokens=True).strip()
            prediction = parse_object(text)
            sku = str(row["metadata"].get("sku"))
            if prediction is None:
                failures.append({"sku": sku, "field": str(field or "six_fields"), "reason": "JSON_PARSE"})
                row_audits.append({"sku": sku, "field": field, "source": source, "expected": expected, "prediction": None, "status": "JSON_PARSE"})
                continue
            parsed_count += 1
            if set(prediction) != expected_fields or not all(isinstance(prediction.get(name), str) for name in expected_fields):
                failures.append({"sku": sku, "field": str(field or "six_fields"), "reason": "SCHEMA"})
                row_audits.append({"sku": sku, "field": field, "source": source, "expected": expected, "prediction": prediction, "status": "SCHEMA"})
                continue
            schema_count += 1
            row_audit = {"sku": sku, "field": field, "source": source, "expected": expected, "prediction": prediction, "status": "OK", "field_checks": {}}
            for name in sorted(expected_fields):
                field_count += 1
                value = prediction[name].strip()
                if value:
                    nonempty_count += 1
                source_nums, output_nums = numeric_fact_counters(source.get(name), value)
                if not (source_nums - output_nums):
                    numeric_ok += 1
                if output_nums - source_nums:
                    numeric_hallucination += 1
                if SPANISH.search(value):
                    spanish += 1
                if name == "cat1":
                    category_total += 1
                    category_ok += value in VALID_CAT1
                row_audit["field_checks"][name] = {
                    "numeric_missing": dict(source_nums - output_nums),
                    "numeric_extra": dict(output_nums - source_nums),
                    "spanish_residual": bool(SPANISH.search(value)),
                }
            row_audits.append(row_audit)
        print(f"evaluated {min(start + len(batch), len(rows))}/{len(rows)}", flush=True)

    total = len(rows)
    metrics = {
        "status": "BATCHED_DIAGNOSTIC_COMPLETE",
        "rows": total,
        "batch_size": args.batch_size,
        "json_parse_rate": parsed_count / total if total else 0,
        "field_schema_rate": schema_count / total if total else 0,
        "evaluated_field_values": field_count,
        "field_nonempty_rate": nonempty_count / field_count if field_count else 0,
        "numeric_preservation_rate": numeric_ok / field_count if field_count else 0,
        "numeric_hallucination_rate": numeric_hallucination / field_count if field_count else 0,
        "spanish_residual_rate": spanish / field_count if field_count else 0,
        "category_valid_rate": category_ok / category_total if category_total else None,
        "category_rows": category_total,
        "failure_count": len(failures),
        "failure_samples": failures[:100],
        "model_path": str(model),
        "adapter_path": str(adapter),
        "test_file": str(test),
        "max_input_length": args.max_input_length,
        "max_new_tokens": args.max_new_tokens,
        "prompt_contract": "evaluate_qwen_model.py messages[:2], add_generation_prompt=true, enable_thinking=false",
        "production_writes": False,
    }
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.predictions_output:
        prediction_path = Path(args.predictions_output).resolve()
        prediction_path.parent.mkdir(parents=True, exist_ok=True)
        prediction_path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in row_audits), encoding="utf-8")
        metrics["predictions_output"] = str(prediction_path)
        output.write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(metrics, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

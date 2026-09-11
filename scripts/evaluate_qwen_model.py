"""Offline evaluation for a Qwen/QLoRA adapter on held-out JSONL examples.

The evaluator reports structural, numeric, category and residual-language
signals separately. It is deliberately read-only: no Master, dictionary or
production localization is touched.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

NUM = re.compile(r"\d+(?:[.,]\d+)?")
SPANISH = re.compile(
    r"\b(?:el|la|los|las|para|con|sin|del|de|y|en|color|tamaño|producto|material|cantidad|contenido|piezas|gramos|litros)\b",
    re.IGNORECASE,
)
VALID_CAT1 = {
    "DIY五金", "办公文具", "宠物用品", "厨房餐具", "服饰鞋包", "个人美容",
    "家居布置", "家务清洁", "旅行用品", "食品饮料", "数码影音", "玩具",
    "兴趣手作", "园艺户外", "运动用品",
}


def nums(value: object) -> list[str]:
    return sorted(x.replace(",", ".") for x in NUM.findall(str(value or "")))


def parse_object(text: str) -> dict | None:
    """Parse JSON even when a model wraps it in a markdown fence."""
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
    ap.add_argument("--adapter-path")
    ap.add_argument("--test-file", default="runtime/training/qwen3_8b/20260908/qwen_field_test.jsonl")
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--max-input-length", type=int, default=1024)
    ap.add_argument("--max-new-tokens", type=int, default=256)
    ap.add_argument("--output", default="runtime/training/qwen3_8b/20260908/evaluation.json")
    args = ap.parse_args()
    root = Path(__file__).resolve().parents[1]
    model = Path(args.model_path); model = model if model.is_absolute() else root / model
    test = Path(args.test_file); test = test if test.is_absolute() else root / test

    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

    tok = AutoTokenizer.from_pretrained(str(model), local_files_only=True, trust_remote_code=True)
    tok.pad_token = tok.pad_token or tok.eos_token
    bnb = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_compute_dtype=torch.float16, bnb_4bit_use_double_quant=True)
    net = AutoModelForCausalLM.from_pretrained(str(model), quantization_config=bnb, device_map={"": 0}, torch_dtype=torch.float16, trust_remote_code=True, local_files_only=True)
    if args.adapter_path:
        from peft import PeftModel
        adapter = Path(args.adapter_path); adapter = adapter if adapter.is_absolute() else root / adapter
        net = PeftModel.from_pretrained(net, str(adapter), local_files_only=True)
    net.eval()

    rows = [json.loads(x) for x in test.open(encoding="utf-8") if x.strip()][: args.limit]
    parsed_count = schema_count = field_count = nonempty_count = numeric_ok = numeric_hallucination = spanish = 0
    category_total = category_ok = 0
    failures: list[dict[str, str]] = []
    for row in rows:
        messages = row["messages"]
        source = parse_object(next(m["content"] for m in messages if m["role"] == "user")) or {}
        expected = parse_object(next(m["content"] for m in messages if m["role"] == "assistant")) or {}
        field = row["metadata"].get("field")
        expected_fields = {str(field)} if field else set(expected)
        prompt = tok.apply_chat_template(messages[:2], tokenize=False, add_generation_prompt=True, enable_thinking=False)
        inp = tok(prompt, return_tensors="pt", truncation=True, max_length=args.max_input_length).to(net.device)
        with torch.no_grad():
            out = net.generate(**inp, max_new_tokens=args.max_new_tokens, do_sample=False)
        text = tok.decode(out[0][inp["input_ids"].shape[1]:], skip_special_tokens=True).strip()
        prediction = parse_object(text)
        if prediction is None:
            failures.append({"sku": str(row["metadata"].get("sku")), "field": str(field), "reason": "JSON_PARSE"})
            continue
        parsed_count += 1
        if set(prediction) == expected_fields and all(isinstance(prediction.get(name), str) for name in expected_fields):
            schema_count += 1
            for name in sorted(expected_fields):
                field_count += 1
                value = prediction[name].strip()
                if value:
                    nonempty_count += 1
                source_nums, output_nums = Counter(nums(source.get(name))), Counter(nums(value))
                if not (source_nums - output_nums):
                    numeric_ok += 1
                if output_nums - source_nums:
                    numeric_hallucination += 1
                if SPANISH.search(value):
                    spanish += 1
                if name == "cat1":
                    category_total += 1
                    category_ok += value in VALID_CAT1
        else:
            failures.append({"sku": str(row["metadata"].get("sku")), "field": str(field or "six_fields"), "reason": "SCHEMA"})
    total = len(rows)
    metrics = {
        "rows": total,
        "json_parse_rate": parsed_count / total if total else 0,
        "field_schema_rate": schema_count / total if total else 0,
        "evaluated_field_values": field_count,
        "field_nonempty_rate": nonempty_count / field_count if field_count else 0,
        "numeric_preservation_rate": numeric_ok / field_count if field_count else 0,
        "numeric_hallucination_rate": numeric_hallucination / field_count if field_count else 0,
        "spanish_residual_rate": spanish / field_count if field_count else 0,
        "category_valid_rate": category_ok / category_total if category_total else None,
        "category_rows": category_total,
        "failure_samples": failures[:20],
        "model_path": str(model),
        "adapter_path": args.adapter_path,
        "test_file": str(test),
        "max_input_length": args.max_input_length,
        "max_new_tokens": args.max_new_tokens,
        "expected_reference_rows": len([r for r in rows if r.get("messages")]),
    }
    out = Path(args.output); out = out if out.is_absolute() else root / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(metrics, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Offline functional evaluation for a Qwen3 LoRA adapter.

This uses fixed synthetic fixtures only.  It does not call Ollama, the Action
site, PRIMARY, or any cloud provider; the report is written beside the adapter.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from action_tracker.localization.ai import SourceFacts, validate_ai_response
from action_tracker.localization.formatter import format_spec
# Allow `python scripts/evaluate_qwen3_adapter.py` as well as module imports.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.build_local_qwen_dataset import FIELD_POLICIES, NAMING_POLICY_VERSION, SYSTEM_POLICY


def _messages(source: SourceFacts, requested_fields: tuple[str, ...]) -> list[dict[str, str]]:
    payload = {
        "sku": source.sku, "source_hash": source.source_hash,
        "name_es": source.name_es, "cat1_es": source.cat1_es, "cat2_es": source.cat2_es,
        "spec_es": source.spec_es, "desc_es": source.desc_es, "details_es": source.details_es,
        "requested_fields": list(requested_fields),
        "naming_policy_version": NAMING_POLICY_VERSION,
        "field_policy": [rule for field in requested_fields for rule in FIELD_POLICIES.get(field, [])],
    }
    return [
        {"role": "system", "content": SYSTEM_POLICY},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, sort_keys=True)},
    ]


def _parse_completion(text: str) -> dict[str, Any] | None:
    text = str(text).lstrip("\ufeff").strip()
    if text.startswith("```") and text.endswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.rsplit("```", 1)[0].strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _policy_format_pass(payload: dict[str, Any], requested_fields: tuple[str, ...]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
    if "spec" in requested_fields:
        spec = str(fields.get("spec", ""))
        if "|" in spec:
            reasons.append("SPEC_ASCII_PIPE")
        if re.search(r"(?<=\d)[xX](?=\d)", spec):
            reasons.append("SPEC_ASCII_MULTIPLY")
        if re.search(r"\d\s+(?:mAh|ml|mg|kg|cm|mm|m|g|L|W|V|A|Hz|lm|°C)\b", spec):
            reasons.append("SPEC_UNIT_SPACE")
    return not reasons, reasons


def evaluate_adapter(*, base_model: str, adapter_dir: Path, max_new_tokens: int = 256) -> dict[str, Any]:
    try:
        import torch
        from peft import PeftModel
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    except ImportError as exc:
        raise RuntimeError(f"QLORA_DEPENDENCY_MISSING:{exc.name}") from exc
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA_REQUIRED_FOR_ADAPTER_EVALUATION")
    tokenizer = AutoTokenizer.from_pretrained(adapter_dir, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    model = AutoModelForCausalLM.from_pretrained(
        base_model,
        quantization_config=BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_compute_dtype=torch.float16),
        device_map="auto", trust_remote_code=True,
    )
    model = PeftModel.from_pretrained(model, adapter_dir)
    model.eval()
    fixtures = [
        ("product", SourceFacts.from_record({"sku": "TEST-QWEN-PRODUCT", "name_es": "Espumador eléctrico portátil"}), ("name",)),
        ("numeric_tech", SourceFacts.from_record({"sku": "TEST-QWEN-NUM", "name_es": "Lámpara LED USB-C", "spec_es": "220 V | 10 W | 4000 mAh | IP44"}), ("name", "spec")),
    ]
    results = []
    for name, source, fields in fixtures:
        inputs = tokenizer.apply_chat_template(_messages(source, fields), tokenize=True, add_generation_prompt=True, return_tensors="pt", return_dict=True, enable_thinking=False)
        inputs = {key: value.to(model.device) for key, value in inputs.items()}
        with torch.inference_mode():
            generated = model.generate(**inputs, max_new_tokens=max_new_tokens, do_sample=False, pad_token_id=tokenizer.eos_token_id)
        continuation = generated[0][inputs["input_ids"].shape[1]:]
        raw = tokenizer.decode(continuation, skip_special_tokens=True)
        payload = _parse_completion(raw)
        raw_format_valid, raw_format_reasons = _policy_format_pass(payload or {}, fields)
        normalized_payload = dict(payload or {})
        normalized_fields = dict(normalized_payload.get("fields") or {})
        if "spec" in normalized_fields:
            normalized_fields["spec"] = format_spec(normalized_fields["spec"])
        normalized_payload["fields"] = normalized_fields
        valid, reasons = validate_ai_response(normalized_payload, source, fields)
        format_valid, format_reasons = _policy_format_pass(normalized_payload, fields)
        results.append({"fixture": name, "raw": raw, "normalized_fields": normalized_fields, "json_object": payload is not None, "validator_pass": valid, "raw_policy_format_pass": raw_format_valid, "policy_format_pass": format_valid, "validator_reasons": list(reasons) + format_reasons, "raw_format_reasons": raw_format_reasons})
    report = {
        "created_at": datetime.now(timezone.utc).isoformat(), "base_model": base_model,
        "adapter_dir": str(adapter_dir), "results": results,
        "all_pass": all(item["validator_pass"] and item["policy_format_pass"] for item in results),
        "production_apply": False, "primary_write": False, "dictionary_write": False,
    }
    (adapter_dir / "adapter_evaluation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-model", default="F:/LocalAI/Models/hf/Qwen3-8B")
    parser.add_argument("--adapter-dir", type=Path, default=Path("F:/LocalAI/Adapters/action-localization-qwen3-8b"))
    args = parser.parse_args()
    print(json.dumps(evaluate_adapter(base_model=args.base_model, adapter_dir=args.adapter_dir), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Compare raw Qwen, the trained adapter and the offline dictionary resolver.

This is a read-only Stage-4 evaluation.  All three systems receive the same
held-out six-field examples; no Master, SQLite or dictionary files are
modified.  Model inference is batched to keep the fixed 97-row comparison
practical on a 12 GB GPU, while preserving the evaluator's deterministic
greedy-generation and quality metrics.
"""
from __future__ import annotations

import argparse
import gc
import json
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from action_tracker.config import load_settings
from action_tracker.dictionary_resolver import resolve_record
from action_tracker.exporting.dictionary_join import load_dictionary_context
from action_tracker.translation.model_guard import validate_model_output

NUM = re.compile(r"\d+(?:[.,]\d+)?")
SPANISH = re.compile(
    r"\b(?:el|la|los|las|para|con|sin|del|de|y|en|color|colores|tamaño|producto|material|cantidad|contenido|piezas|gramos|litros|antracita|blanco|blanca|negro|negra|gris|rojo|roja|verde|azul|amarillo|amarilla|rosa|marrón)\b",
    re.IGNORECASE,
)
VALID_CAT1 = {
    "DIY五金", "办公文具", "宠物用品", "厨房餐具", "服饰鞋包", "个人美容",
    "家居布置", "家务清洁", "旅行用品", "食品饮料", "数码影音", "玩具",
    "兴趣手作", "园艺户外", "运动用品",
}
FIELDS = ("name", "cat1", "cat2", "spec", "description", "details")
SAFETY_REASONS = frozenset({
    "JSON_PARSE", "SCHEMA", "EMPTY_REQUIRED_FIELD", "INVALID_CAT1",
    "NUMERIC_DROPPED", "NUMERIC_HALLUCINATED", "SPANISH_RESIDUAL", "ENGLISH_RESIDUAL",
})
RESOLVER_FIELDS = {
    "name": "标题", "cat1": "分类1", "cat2": "分类2", "spec": "规格",
    "description": "描述", "details": "产品详情",
}
STRICT_SYSTEM = (
    "将 Action 西语商品六字段忠实标准化为简体中文。输出不得使用英文或西班牙语句子；普通西语必须翻译。品牌、型号、USB/LED/PEFC 等注册符号按规则保留，"
    "但颜色、材质和功能词必须翻译。每个输出字段只能对应同名输入字段，禁止把 details/spec 中的数量搬到 description，"
    "也禁止把一个字段的事实补到另一个字段。严格复制该字段源文本中的每一个数字、百分比、尺寸、数量和单位，既不能遗漏也不能新增；"
    "例如源 description 没有数字时，description 不得因为 details 有数量而新增数字，100 % 必须保留为 100%。"
    "已确认品牌可以保留原文，但普通颜色词必须翻译。只输出 JSON，不补充源文本没有的事实。"
)
FIELDWISE_SYSTEM = (
    "只翻译指定的一个字段为简体中文，不要查看或推断其他字段。输出必须是 JSON，且只能有指定字段。"
    "不得输出英文或西班牙语句子；普通西语必须翻译。忠实翻译输入文字，严格保留该字段原文的每一个数字、百分比、尺寸、数量和单位，"
    "不能新增、删除或从其他字段补入事实。输出前先核对该字段的数字清单，源中每个数字都必须在同一字段出现。"
    "已确认品牌可以保留原文，但普通颜色词必须翻译。"
)


def nums(value: object) -> list[str]:
    return sorted(x.replace(",", ".") for x in NUM.findall(str(value or "")))


def parse_object(text: str) -> dict[str, Any] | None:
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


def load_rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.open(encoding="utf-8") if line.strip()]


def source_target(row: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    source = parse_object(next(m["content"] for m in row["messages"] if m["role"] == "user")) or {}
    target = parse_object(next(m["content"] for m in row["messages"] if m["role"] == "assistant")) or {}
    return source, target


def _has_unallowed_spanish(value: object, allowed_brand_phrases: tuple[str, ...] = ()) -> bool:
    text = str(value or "")
    for phrase in sorted((str(item).strip() for item in allowed_brand_phrases if str(item).strip()), key=len, reverse=True):
        text = re.sub(re.escape(phrase), " ", text, flags=re.IGNORECASE)
    return bool(SPANISH.search(text))


def normalise_reference_value(value: object) -> str:
    """Normalise presentation-only differences for a diagnostic comparison.

    This is intentionally narrow: it only ignores Unicode/spacing/separator
    presentation.  It does not claim two differently worded translations are
    semantically equal, so it can never replace source-fidelity review.
    """

    text = unicodedata.normalize("NFKC", str(value or "").strip())
    text = text.translate(str.maketrans({"×": "x", "｜": "|", "；": ";", "：": ":", "，": ",", "。": "."}))
    return re.sub(r"\s+", "", text)


def score_prediction(
    row: dict[str, Any], prediction: dict[str, Any] | None,
    allowed_brand_phrases: tuple[str, ...] = (),
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    source, expected = source_target(row)
    sku = str(row.get("metadata", {}).get("sku", ""))
    field = row.get("metadata", {}).get("field")
    expected_fields = {str(field)} if field else set(expected)
    details: list[dict[str, Any]] = []
    if prediction is None:
        return {"parsed": False, "schema": False, "sku": sku, "field": field, "exact": 0, "normalised_exact": 0, "total": 0}, [{"sku": sku, "field": field or "six_fields", "reasons": ["JSON_PARSE"]}]
    schema = set(prediction) == expected_fields and all(isinstance(prediction.get(name), str) for name in expected_fields)
    if not schema:
        return {"parsed": True, "schema": False, "sku": sku, "field": field, "exact": 0, "normalised_exact": 0, "total": 0}, [{"sku": sku, "field": field or "six_fields", "reasons": ["SCHEMA"], "prediction": prediction}]
    numeric_ok = hallucination = spanish = english = nonempty = exact = normalised_exact = required_total = required_nonempty = 0
    for name in sorted(expected_fields):
        value = str(prediction[name]).strip()
        expected_value = str(expected.get(name, "")).strip()
        nonempty += bool(value)
        required_total += bool(expected_value)
        required_nonempty += bool(expected_value and value)
        source_nums, output_nums = Counter(nums(source.get(name))), Counter(nums(value))
        missing = list((source_nums - output_nums).elements())
        extra = list((output_nums - source_nums).elements())
        numeric_ok += not missing
        hallucination += bool(extra)
        guard = validate_model_output(
            {name: source.get(name, "")}, {name: value},
            expected_fields=[name], allowed_brand_phrases=allowed_brand_phrases,
        )
        guard_reasons = list(guard.field_reasons.get(name, ()))
        spanish += "SPANISH_RESIDUAL" in guard_reasons
        english += "ENGLISH_RESIDUAL" in guard_reasons
        exact += value == expected_value
        normalised_exact += normalise_reference_value(value) == normalise_reference_value(expected_value)
        reasons = list(guard_reasons)
        if expected_value and not value:
            reasons.append("EMPTY_REQUIRED_FIELD")
        if name == "cat1" and value not in VALID_CAT1:
            reasons.append("INVALID_CAT1")
        reasons = sorted(set(reasons))
        if reasons:
            details.append({
                "sku": sku, "field": name, "reasons": reasons,
                "source": source.get(name, ""), "expected": expected_value, "prediction": value,
                "missing_numbers": missing, "extra_numbers": extra,
            })
    total = len(expected_fields)
    return {
        "parsed": True, "schema": True, "sku": sku, "field": field,
        "exact": exact, "normalised_exact": normalised_exact, "total": total, "nonempty": nonempty,
        "required_total": required_total, "required_nonempty": required_nonempty,
        "numeric_ok": numeric_ok, "numeric_hallucination": hallucination,
        "spanish": spanish, "english": english,
        "category_total": int("cat1" in expected_fields),
        "category_ok": int("cat1" in expected_fields and prediction.get("cat1") in VALID_CAT1),
    }, details


def aggregate(
    rows: list[dict[str, Any]], predictions: list[dict[str, Any] | None], name: str,
    allowed_brands_by_sku: dict[str, tuple[str, ...]] | None = None,
) -> dict[str, Any]:
    scored = []
    issues = []
    for row, prediction in zip(rows, predictions):
        sku = str(row.get("metadata", {}).get("sku", ""))
        allowed = (allowed_brands_by_sku or {}).get(sku, ())
        result, row_issues = score_prediction(row, prediction, allowed)
        scored.append(result)
        issues.extend(row_issues)
    total_rows = len(rows)
    parsed_rows = sum(x["parsed"] for x in scored)
    schema_rows = sum(x["schema"] for x in scored)
    total_fields = sum(x.get("total", 0) for x in scored)
    issue_reasons = Counter(reason for issue in issues for reason in issue.get("reasons", ()))
    return {
        "name": name,
        "rows": total_rows,
        "json_parse_rate": parsed_rows / total_rows if total_rows else 0,
        "field_schema_rate": schema_rows / total_rows if total_rows else 0,
        "evaluated_field_values": total_fields,
        "field_exact_match_rate": sum(x.get("exact", 0) for x in scored) / total_fields if total_fields else 0,
        "field_format_normalized_match_rate": sum(x.get("normalised_exact", 0) for x in scored) / total_fields if total_fields else 0,
        "field_nonempty_rate": sum(x.get("nonempty", 0) for x in scored) / total_fields if total_fields else 0,
        "required_field_completeness_rate": sum(x.get("required_nonempty", 0) for x in scored) / sum(x.get("required_total", 0) for x in scored) if sum(x.get("required_total", 0) for x in scored) else 1,
        "numeric_preservation_rate": sum(x.get("numeric_ok", 0) for x in scored) / total_fields if total_fields else 0,
        "numeric_hallucination_rate": sum(x.get("numeric_hallucination", 0) for x in scored) / total_fields if total_fields else 0,
        "spanish_residual_rate": sum(x.get("spanish", 0) for x in scored) / total_fields if total_fields else 0,
        "english_residual_rate": sum(x.get("english", 0) for x in scored) / total_fields if total_fields else 0,
        "category_valid_rate": sum(x.get("category_ok", 0) for x in scored) / sum(x.get("category_total", 0) for x in scored) if sum(x.get("category_total", 0) for x in scored) else None,
        "category_rows": sum(x.get("category_total", 0) for x in scored),
        "hard_error_count": sum(1 for issue in issues if set(issue.get("reasons", [])) & SAFETY_REASONS),
        "issue_reason_counts": dict(sorted(issue_reasons.items())),
        "issues": issues[:100],
    }


def automated_safety_pass(metrics: dict[str, Any]) -> bool:
    """Return the non-negotiable mechanical safety decision for Stage 4."""

    category_ok = metrics["category_valid_rate"] in (None, 1)
    return (
        metrics["hard_error_count"] == 0
        and metrics["json_parse_rate"] == 1
        and metrics["field_schema_rate"] == 1
        and metrics["required_field_completeness_rate"] == 1
        and metrics["numeric_preservation_rate"] == 1
        and metrics["numeric_hallucination_rate"] == 0
        and metrics["spanish_residual_rate"] == 0
        and metrics["english_residual_rate"] == 0
        and category_ok
    )


def run_model(model_path: Path, rows: list[dict[str, Any]], batch_size: int, max_input_length: int, max_new_tokens: int, adapter_path: Path | None = None, strict_prompt: bool = False) -> list[dict[str, Any] | None]:
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

    tok = AutoTokenizer.from_pretrained(str(model_path), local_files_only=True, trust_remote_code=True)
    tok.pad_token = tok.pad_token or tok.eos_token
    tok.padding_side = "left"
    bnb = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_compute_dtype=torch.float16, bnb_4bit_use_double_quant=True)
    net = AutoModelForCausalLM.from_pretrained(str(model_path), quantization_config=bnb, device_map={"": 0}, torch_dtype=torch.float16, trust_remote_code=True, local_files_only=True)
    if adapter_path:
        from peft import PeftModel
        net = PeftModel.from_pretrained(net, str(adapter_path), local_files_only=True)
    net.eval()
    predictions: list[dict[str, Any] | None] = []
    for start in range(0, len(rows), batch_size):
        chunk = rows[start : start + batch_size]
        prompts = []
        for row in chunk:
            messages = row["messages"][:2]
            if strict_prompt:
                messages = [{"role": "system", "content": STRICT_SYSTEM}, messages[1]]
            prompts.append(tok.apply_chat_template(messages, tokenize=False, add_generation_prompt=True, enable_thinking=False))
        inputs = tok(prompts, return_tensors="pt", padding=True, truncation=True, max_length=max_input_length).to(net.device)
        with torch.no_grad():
            output = net.generate(**inputs, max_new_tokens=max_new_tokens, do_sample=False)
        generated = output[:, inputs["input_ids"].shape[1] :]
        for index, tokens in enumerate(generated):
            text = tok.decode(tokens, skip_special_tokens=True).strip()
            predictions.append(parse_object(text))
        print(f"{start + len(chunk)}/{len(rows)}", flush=True)
    del net, tok
    gc.collect()
    torch.cuda.empty_cache()
    return predictions


def run_fieldwise_retry(
    model_path: Path,
    requests: list[tuple[dict[str, Any], str]],
    max_input_length: int,
    max_new_tokens: int,
    adapter_path: Path | None = None,
) -> list[dict[str, Any] | None]:
    """Retry only unsafe fields with an isolated source payload.

    This is an evaluation-time fallback.  The candidate is accepted only
    after ``model_guard`` validates it; no guessed correction is synthesized.
    """
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

    if not requests:
        return []
    tok = AutoTokenizer.from_pretrained(str(model_path), local_files_only=True, trust_remote_code=True)
    tok.pad_token = tok.pad_token or tok.eos_token
    tok.padding_side = "left"
    bnb = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_compute_dtype=torch.float16, bnb_4bit_use_double_quant=True)
    net = AutoModelForCausalLM.from_pretrained(str(model_path), quantization_config=bnb, device_map={"": 0}, torch_dtype=torch.float16, trust_remote_code=True, local_files_only=True)
    if adapter_path:
        from peft import PeftModel
        net = PeftModel.from_pretrained(net, str(adapter_path), local_files_only=True)
    net.eval()
    results: list[dict[str, Any] | None] = []
    for index, (row, field) in enumerate(requests, start=1):
        source, _ = source_target(row)
        messages = [
            {"role": "system", "content": FIELDWISE_SYSTEM},
            {"role": "user", "content": json.dumps({field: source.get(field, "")}, ensure_ascii=False)},
        ]
        prompt = tok.apply_chat_template(messages, tokenize=False, add_generation_prompt=True, enable_thinking=False)
        inputs = tok([prompt], return_tensors="pt", padding=True, truncation=True, max_length=max_input_length).to(net.device)
        with torch.no_grad():
            output = net.generate(**inputs, max_new_tokens=max_new_tokens, do_sample=False)
        text = tok.decode(output[0, inputs["input_ids"].shape[1] :], skip_special_tokens=True).strip()
        results.append(parse_object(text))
        print(f"fieldwise {index}/{len(requests)}", flush=True)
    del net, tok
    gc.collect()
    torch.cuda.empty_cache()
    return results


def apply_fieldwise_retries(
    rows: list[dict[str, Any]], predictions: list[dict[str, Any] | None],
    retries: list[dict[str, Any] | None], requests: list[tuple[dict[str, Any], str]],
    allowed_brands_by_sku: dict[str, tuple[str, ...]],
) -> tuple[list[dict[str, Any] | None], list[dict[str, Any]]]:
    """Replace a field only when the isolated candidate passes the guard."""
    by_request = {(id(row), field): candidate for (row, field), candidate in zip(requests, retries)}
    accepted: list[dict[str, Any]] = []
    updated: list[dict[str, Any] | None] = []
    for row, original in zip(rows, predictions):
        current = dict(original) if original is not None else None
        if current is not None:
            source, _ = source_target(row)
            sku = str(row.get("metadata", {}).get("sku", ""))
            for field in FIELDS:
                candidate = by_request.get((id(row), field))
                if not isinstance(candidate, dict) or field not in candidate or not isinstance(candidate[field], str):
                    continue
                check = validate_model_output(
                    {field: source.get(field, "")}, {field: candidate[field]},
                    expected_fields=[field], allowed_brand_phrases=allowed_brands_by_sku.get(sku, ()),
                )
                if check.accepted:
                    current[field] = candidate[field]
                    accepted.append({"sku": sku, "field": field, "value": candidate[field]})
        updated.append(current)
    return updated, accepted


def resolver_predictions(rows: list[dict[str, Any]], context=None) -> list[dict[str, str]]:
    if context is None:
        cfg = load_settings(ROOT / "config" / "settings.yaml")
        context = load_dictionary_context(cfg)
    predictions = []
    for row in rows:
        source, _ = source_target(row)
        record = {
            "sku": row["metadata"]["sku"],
            "name_es": source.get("name", ""), "cat1_es": source.get("cat1", ""), "cat2_es": source.get("cat2", ""),
            "spec_es": source.get("spec", ""), "desc_es": source.get("description", ""), "details_es": source.get("details", ""),
        }
        resolved = resolve_record(record, context)
        predictions.append({field: str(resolved.fields[field].value or "") for field in FIELDS})
    return predictions


def allowed_brands_by_sku(rows: list[dict[str, Any]], context) -> dict[str, tuple[str, ...]]:
    """Return confirmed/reference brand phrases permitted in Chinese names."""

    result: dict[str, tuple[str, ...]] = {}
    for row in rows:
        sku = str(row.get("metadata", {}).get("sku", ""))
        # The source title is not a brand authority.  Only the reviewed
        # dictionary entry grants permission to keep a Latin brand phrase.
        # ``brand_id`` is already the normalized key used by the resolver.
        product = context.product_by_sku.get(sku, {})
        brand_id = str(product.get("brand_id") or "").strip()
        brand = context.brand_by_id.get(brand_id, {}) if brand_id else {}
        phrases = [str(brand.get("canonical_name") or brand_id).strip()]
        phrases.extend(x.strip() for x in str(brand.get("aliases_es") or "").split("|") if x.strip())
        result[sku] = tuple(dict.fromkeys(x for x in phrases if x))
    return result


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-path", default="runtime/models/Qwen3-8B")
    ap.add_argument("--adapter-path", default="runtime/training/qwen3_8b/20260908/baseline_gold_qlora_8b_20260909_clean/adapter")
    ap.add_argument("--test-file", default="runtime/training/qwen3_8b/20260908/qwen_gold_clean_test.jsonl")
    ap.add_argument("--output", default="runtime/training/qwen3_8b/20260908/qwen_baseline_comparison.json")
    ap.add_argument("--limit", type=int, default=97)
    ap.add_argument("--batch-size", type=int, default=4)
    ap.add_argument("--max-input-length", type=int, default=1024)
    ap.add_argument("--max-new-tokens", type=int, default=256)
    ap.add_argument("--strict-prompt", action="store_true", help="Use the strengthened numeric/translation contract at inference time.")
    ap.add_argument("--fieldwise-retry", action="store_true", help="Retry only unsafe tuned fields with isolated source text; still subject to the guard.")
    args = ap.parse_args()
    model = Path(args.model_path); model = model if model.is_absolute() else ROOT / model
    adapter = Path(args.adapter_path); adapter = adapter if adapter.is_absolute() else ROOT / adapter
    test = Path(args.test_file); test = test if test.is_absolute() else ROOT / test
    output = Path(args.output); output = output if output.is_absolute() else ROOT / output
    rows = load_rows(test)[: args.limit]
    raw = run_model(model, rows, args.batch_size, args.max_input_length, args.max_new_tokens, strict_prompt=args.strict_prompt)
    cfg = load_settings(ROOT / "config" / "settings.yaml")
    context = load_dictionary_context(cfg)
    allowed_brands = allowed_brands_by_sku(rows, context)
    raw_metrics = aggregate(rows, raw, "raw_qwen", allowed_brands)
    tuned = run_model(model, rows, args.batch_size, args.max_input_length, args.max_new_tokens, adapter, strict_prompt=args.strict_prompt)
    tuned_pre_retry_metrics = aggregate(rows, tuned, "qwen3_8b_qlora_pre_retry", allowed_brands)
    tuned_metrics = tuned_pre_retry_metrics
    fieldwise_accepted: list[dict[str, Any]] = []
    if args.fieldwise_retry:
        retry_requests: list[tuple[dict[str, Any], str]] = []
        for row, prediction in zip(rows, tuned):
            _, issues = score_prediction(row, prediction, allowed_brands.get(str(row.get("metadata", {}).get("sku", "")), ()))
            for field in sorted({str(issue.get("field")) for issue in issues if issue.get("field") in FIELDS}):
                retry_requests.append((row, field))
        retries = run_fieldwise_retry(model, retry_requests, args.max_input_length, min(args.max_new_tokens, 160), adapter)
        tuned, fieldwise_accepted = apply_fieldwise_retries(rows, tuned, retries, retry_requests, allowed_brands)
        tuned_metrics = aggregate(rows, tuned, "qwen3_8b_qlora_fieldwise_guarded", allowed_brands)
    resolved = resolver_predictions(rows, context)
    resolver_metrics = aggregate(rows, resolved, "dictionary_resolver", allowed_brands)
    model_reports = [raw_metrics, tuned_pre_retry_metrics]
    if args.fieldwise_retry:
        model_reports.append(tuned_metrics)
    model_reports.append(resolver_metrics)
    tuned_safety_pass = automated_safety_pass(tuned_metrics)
    resolver_safety_pass = automated_safety_pass(resolver_metrics)
    report = {
        "evaluation_policy_version": "stage4_safety_first_v2_2026-09-11",
        "test_file": str(test), "rows": len(rows), "batch_size": args.batch_size,
        "max_input_length": args.max_input_length, "max_new_tokens": args.max_new_tokens,
        "strict_prompt": args.strict_prompt,
        "fieldwise_retry": args.fieldwise_retry,
        "fieldwise_accepted": fieldwise_accepted,
        "models": model_reports,
        "stage4_gate": {
            "decision_model": "Safety is an automated hard gate; reference-match metrics are diagnostics and cannot prove source fidelity.",
            "all_models_compared": True,
            "automated_safety": {
                "requires": [
                    "JSON/schema 100%", "all expected non-empty fields populated",
                    "no dropped or hallucinated numeric facts", "no ordinary Spanish or English residual",
                    "all evaluated cat1 values in the fixed 15-category set",
                ],
                "tuned_pass": tuned_safety_pass,
                "resolver_pass": resolver_safety_pass,
            },
            "reference_quality_diagnostics": {
                "strict_reference_match_rate": {
                    "tuned": tuned_metrics["field_exact_match_rate"],
                    "resolver": resolver_metrics["field_exact_match_rate"],
                    "delta": tuned_metrics["field_exact_match_rate"] - resolver_metrics["field_exact_match_rate"],
                },
                "format_normalized_reference_match_rate": {
                    "tuned": tuned_metrics["field_format_normalized_match_rate"],
                    "resolver": resolver_metrics["field_format_normalized_match_rate"],
                    "delta": tuned_metrics["field_format_normalized_match_rate"] - resolver_metrics["field_format_normalized_match_rate"],
                },
                "gate_role": "DIAGNOSTIC_ONLY",
            },
            "manual_source_fidelity_review": {
                "required_before_stage5": True,
                "status": "NOT_RECORDED_BY_EVALUATOR",
                "minimum_protocol": "Review a deterministic stratified holdout sample and every model-guard rejection against the Spanish source; any P0 fact error blocks Stage 5.",
            },
            "automated_pass": tuned_safety_pass,
            "stage5_eligible": False,
            "stage5_blocker": "Manual source-fidelity review is intentionally external to this read-only evaluator.",
        },
        "error_localization": tuned_metrics["issues"],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

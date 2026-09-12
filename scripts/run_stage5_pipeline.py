"""Run the frozen, offline Stage 5 rule-first candidate pipeline."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from action_tracker.config import load_settings  # noqa: E402
from action_tracker.exporting.dictionary_join import load_dictionary_context  # noqa: E402
from action_tracker.stage5.pipeline import (  # noqa: E402
    ContractError,
    environment_manifest,
    load_contracts,
    model_requests,
    plan_batch,
    sha256_file,
    validate_frozen_identity,
    validate_input_rows,
    write_batch_artifacts,
    build_batch,
)


def _load_rows(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ContractError(f"INPUT_JSON_INVALID line={line_no}") from exc
        if not isinstance(row, dict):
            raise ContractError(f"INPUT_ROW_NOT_OBJECT line={line_no}")
        rows.append(row)
    return rows


def _load_recorded_outputs(path: Path, expected_request_ids: set[str]) -> dict[str, str | None]:
    output: dict[str, str | None] = {}
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        row = json.loads(line)
        request_id = str(row.get("request_id") or "")
        if not request_id or request_id in output:
            raise ContractError(f"MODEL_OUTPUT_ID_INVALID line={line_no}")
        value = row.get("raw_output")
        if value is not None and not isinstance(value, str):
            raise ContractError(f"MODEL_OUTPUT_NOT_STRING line={line_no}")
        output[request_id] = value
    if set(output) != expected_request_ids:
        raise ContractError("MODEL_OUTPUT_REQUEST_SET_MISMATCH")
    return output


def run_frozen_qwen(
    requests: list[dict[str, Any]], *, model_path: Path, adapter_path: Path,
    inference: dict[str, Any],
) -> dict[str, str | None]:
    """Run exactly the model, prompt and greedy generation in the frozen contract."""

    if not requests:
        return {}
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

    if not torch.cuda.is_available():
        raise ContractError("CUDA_REQUIRED_NO_CPU_FALLBACK")
    tokenizer = AutoTokenizer.from_pretrained(str(model_path), local_files_only=True, trust_remote_code=True)
    tokenizer.pad_token = tokenizer.pad_token or tokenizer.eos_token
    tokenizer.padding_side = inference["tokenization"]["padding_side"]
    quantization = BitsAndBytesConfig(
        load_in_4bit=True, bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.float16, bnb_4bit_use_double_quant=True,
    )
    model = AutoModelForCausalLM.from_pretrained(
        str(model_path), quantization_config=quantization, device_map={"": 0},
        torch_dtype=torch.float16, trust_remote_code=True, local_files_only=True,
    )
    from peft import PeftModel

    model = PeftModel.from_pretrained(model, str(adapter_path), local_files_only=True)
    model.eval()
    outputs: dict[str, str | None] = {}
    for index, request in enumerate(requests, start=1):
        messages = [
            {"role": "system", "content": inference["system_prompt"]},
            {"role": "user", "content": json.dumps({request["field"]: request["source"]}, ensure_ascii=False)},
        ]
        prompt = tokenizer.apply_chat_template(
            messages, tokenize=False,
            add_generation_prompt=inference["chat_template"]["add_generation_prompt"],
            enable_thinking=inference["chat_template"]["enable_thinking"],
        )
        encoded = tokenizer(
            [prompt], return_tensors="pt", padding=True,
            truncation=inference["tokenization"]["truncation"],
            max_length=inference["tokenization"]["max_input_length"],
        ).to(model.device)
        with torch.no_grad():
            generated = model.generate(
                **encoded,
                max_new_tokens=inference["generation"]["max_new_tokens"],
                do_sample=inference["generation"]["do_sample"],
            )
        outputs[request["request_id"]] = tokenizer.decode(
            generated[0, encoded["input_ids"].shape[1]:], skip_special_tokens=True,
        ).strip()
        print(f"stage5 field {index}/{len(requests)}", flush=True)
    return outputs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--recorded-model-outputs", type=Path)
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args()

    input_path = args.input if args.input.is_absolute() else ROOT / args.input
    output_dir = args.output_dir if args.output_dir.is_absolute() else ROOT / args.output_dir
    contracts = load_contracts(ROOT)
    identity = validate_frozen_identity(ROOT, contracts)
    rows = validate_input_rows(_load_rows(input_path), contracts)
    settings = load_settings(ROOT / "config/settings.yaml")
    context = load_dictionary_context(settings)
    plans = plan_batch(rows, context, contracts)
    requests = model_requests(plans)
    if args.preflight_only:
        print(json.dumps({
            "status": "PREFLIGHT_PASS", "rows": len(rows), "field_plans": len(plans),
            "model_requests": len(requests), "frozen_identity": identity,
        }, ensure_ascii=False))
        return 0
    expected = {request["request_id"] for request in requests}
    if args.recorded_model_outputs:
        path = args.recorded_model_outputs if args.recorded_model_outputs.is_absolute() else ROOT / args.recorded_model_outputs
        raw_outputs = _load_recorded_outputs(path, expected)
    else:
        raw_outputs = run_frozen_qwen(
            requests, model_path=Path(identity["model_path"]), adapter_path=Path(identity["adapter_path"]),
            inference=contracts.pipeline["inference"],
        )
    dictionary_manifest = ROOT / "data/dictionary/baseline_manifest.json"
    dictionary_hash = sha256_file(dictionary_manifest)
    candidates, failures, reviews, evaluation = build_batch(
        plans, raw_outputs, contracts, identity, dictionary_hash=dictionary_hash,
    )
    manifest = write_batch_artifacts(
        output_dir, input_path=input_path, contracts=contracts, identity=identity,
        environment=environment_manifest(ROOT), dictionary_hash=dictionary_hash,
        requests=requests, raw_outputs=raw_outputs, candidates=candidates,
        failures=failures, reviews=reviews, evaluation=evaluation,
    )
    print(json.dumps({
        "status": "STAGE5_BATCH_COMPLETE", "batch_id": evaluation["batch_id"],
        "manifest": manifest["manifest_path"], "evaluation": evaluation,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

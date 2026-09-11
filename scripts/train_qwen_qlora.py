"""Train Qwen3 with a small, isolated QLoRA job.

The script consumes the JSONL artifacts produced by the data factory and never
touches Master or production dictionaries.  ``--smoke`` is deliberately small
and is the required first run on a new machine.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path


def assistant_only_labels(input_ids: list[int], prompt_length: int) -> list[int]:
    """Mask system/user tokens so the loss is computed only on the answer."""
    if prompt_length >= len(input_ids):
        raise ValueError("TARGET_TRUNCATED: assistant answer is absent after max_length truncation")
    return [-100] * prompt_length + list(input_ids[prompt_length:])


class AssistantOnlyCollator:
    """Pad already-rendered examples without rebuilding labels from all tokens."""

    def __init__(self, tokenizer):
        self.tokenizer = tokenizer

    def __call__(self, features):
        import torch

        labels = [list(feature["labels"]) for feature in features]
        inputs = [{key: value for key, value in feature.items() if key != "labels"} for feature in features]
        batch = self.tokenizer.pad(inputs, padding=True, return_tensors="pt")
        padded = torch.full((len(labels), batch["input_ids"].shape[1]), -100, dtype=torch.long)
        for index, row in enumerate(labels):
            padded[index, : len(row)] = torch.tensor(row, dtype=torch.long)
        batch["labels"] = padded
        return batch


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def effective_steps(max_steps: int, *, smoke: bool = False, short_run: bool = False) -> int:
    """Resolve the requested step budget without weakening the normal guard.

    The original training entry point intentionally enforced a minimum of 200
    steps for non-smoke runs.  Stage-4 checkpoint selection needs a bounded
    validation run after an overfit signal, so ``--short-run`` opts into the
    exact requested budget while leaving the default behaviour unchanged.
    """

    if smoke or short_run:
        return max_steps
    return max(max_steps, 200)


def validate_early_stopping(*, short_run: bool, patience: int) -> None:
    """Keep early stopping coupled to best-checkpoint restoration."""

    if patience < 0:
        raise ValueError("EARLY_STOPPING_PATIENCE_MUST_BE_NON_NEGATIVE")
    if patience and not short_run:
        raise ValueError("EARLY_STOPPING_REQUIRES_SHORT_RUN")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-path", default="runtime/models/Qwen3-8B")
    ap.add_argument("--train-file", default="runtime/training/qwen3_8b/20260908/qwen_field_train.jsonl")
    ap.add_argument("--eval-file", default="runtime/training/qwen3_8b/20260908/qwen_field_validation.jsonl")
    ap.add_argument("--output-dir", default="runtime/training/qwen3_8b/20260908/smoke_qlora")
    ap.add_argument("--max-length", type=int, default=768)
    ap.add_argument("--max-steps", type=int, default=3)
    ap.add_argument("--limit", type=int, default=64)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument(
        "--short-run",
        action="store_true",
        help="Run the exact max_steps and restore the best eval-loss checkpoint (Stage-4 only).",
    )
    ap.add_argument(
        "--early-stopping-patience",
        type=int,
        default=0,
        help="Stop after this many non-improving validation evaluations; requires --short-run.",
    )
    args = ap.parse_args()
    root = Path(__file__).resolve().parents[1]
    model_path = Path(args.model_path)
    if not model_path.is_absolute(): model_path = root / model_path
    train_file = Path(args.train_file); train_file = train_file if train_file.is_absolute() else root / train_file
    eval_file = Path(args.eval_file); eval_file = eval_file if eval_file.is_absolute() else root / eval_file
    output = Path(args.output_dir); output = output if output.is_absolute() else root / output
    if not (model_path / "config.json").exists(): raise FileNotFoundError(f"MODEL_NOT_FOUND: {model_path}")

    validate_early_stopping(short_run=args.short_run, patience=args.early_stopping_patience)

    import torch
    from datasets import load_dataset
    from peft import LoraConfig, get_peft_model
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig, EarlyStoppingCallback, Trainer, TrainingArguments, set_seed

    if not torch.cuda.is_available(): raise RuntimeError("CUDA_REQUIRED_FOR_QLORA")
    tokenizer = AutoTokenizer.from_pretrained(str(model_path), local_files_only=True, trust_remote_code=True)
    if tokenizer.pad_token is None: tokenizer.pad_token = tokenizer.eos_token
    set_seed(args.seed)
    raw = load_dataset("json", data_files={"train": str(train_file), "eval": str(eval_file)})
    if args.smoke:
        raw["train"] = raw["train"].select(range(min(args.limit, len(raw["train"]))))
        raw["eval"] = raw["eval"].select(range(min(max(16, args.limit // 4), len(raw["eval"]))))

    def render(row):
        messages = row["messages"]
        text = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=False, enable_thinking=False)
        prompt = tokenizer.apply_chat_template(messages[:2], tokenize=False, add_generation_prompt=True, enable_thinking=False)
        encoded = tokenizer(text, truncation=True, max_length=args.max_length, add_special_tokens=False)
        prompt_ids = tokenizer(prompt, truncation=False, add_special_tokens=False)["input_ids"]
        prompt_length = min(len(prompt_ids), len(encoded["input_ids"]))
        encoded["labels"] = assistant_only_labels(list(encoded["input_ids"]), prompt_length)
        return encoded

    tokenized = raw.map(render, remove_columns=raw["train"].column_names)
    bnb = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_compute_dtype=torch.float16, bnb_4bit_use_double_quant=True)
    model = AutoModelForCausalLM.from_pretrained(str(model_path), quantization_config=bnb, device_map={"": 0}, torch_dtype=torch.float16, trust_remote_code=True, local_files_only=True)
    model.config.use_cache = False
    lora = LoraConfig(r=16, lora_alpha=32, lora_dropout=0.05, bias="none", task_type="CAUSAL_LM", target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"])
    model = get_peft_model(model, lora)
    steps = effective_steps(args.max_steps, smoke=args.smoke, short_run=args.short_run)
    output.mkdir(parents=True, exist_ok=True)
    eval_steps = max(1, min(10, steps))
    train_args = TrainingArguments(output_dir=str(output), per_device_train_batch_size=1, per_device_eval_batch_size=1, gradient_accumulation_steps=8, max_steps=steps, learning_rate=2e-4, warmup_steps=1, logging_steps=1, eval_strategy="steps", eval_steps=eval_steps, save_strategy="steps", save_steps=eval_steps, save_total_limit=6 if args.short_run else 2, load_best_model_at_end=args.short_run, metric_for_best_model="eval_loss" if args.short_run else None, greater_is_better=False if args.short_run else None, fp16=True, gradient_checkpointing=True, optim="paged_adamw_8bit", report_to="none", remove_unused_columns=False, seed=args.seed, data_seed=args.seed)
    callbacks = [EarlyStoppingCallback(early_stopping_patience=args.early_stopping_patience)] if args.early_stopping_patience else []
    trainer = Trainer(model=model, args=train_args, train_dataset=tokenized["train"], eval_dataset=tokenized["eval"], data_collator=AssistantOnlyCollator(tokenizer), processing_class=tokenizer, callbacks=callbacks)
    before = torch.cuda.memory_allocated() / 1024**3
    result = trainer.train()
    after = torch.cuda.max_memory_allocated() / 1024**3
    trainer.save_model(str(output / "adapter")); tokenizer.save_pretrained(str(output / "adapter"))
    supervised_tokens = sum(sum(label != -100 for label in row["labels"]) for row in tokenized["train"])
    metrics = dict(result.metrics); metrics.update({"smoke": args.smoke, "short_run": args.short_run, "early_stopping_patience": args.early_stopping_patience, "best_model_checkpoint": trainer.state.best_model_checkpoint if args.short_run else None, "completed_steps": trainer.state.global_step, "train_rows": len(tokenized["train"]), "eval_rows": len(tokenized["eval"]), "supervised_train_tokens": supervised_tokens, "max_length": args.max_length, "seed": args.seed, "max_memory_gb": round(after, 3), "memory_before_gb": round(before, 3), "cuda_device": torch.cuda.get_device_name(0), "model_path": str(model_path), "train_file": str(train_file)})
    (output / "smoke_metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest = {"training_script": str(Path(__file__).resolve()), "training_script_sha256": sha256_file(Path(__file__).resolve()), "model_path": str(model_path), "model_config_sha256": sha256_file(model_path / "config.json"), "train_file": str(train_file), "train_file_sha256": sha256_file(train_file), "eval_file": str(eval_file), "eval_file_sha256": sha256_file(eval_file), "output_dir": str(output), "label_policy": "assistant_only", "max_length": args.max_length, "max_steps": steps, "smoke": args.smoke, "short_run": args.short_run, "early_stopping_patience": args.early_stopping_patience, "best_model_checkpoint": trainer.state.best_model_checkpoint if args.short_run else None, "completed_steps": trainer.state.global_step, "seed": args.seed, "train_rows": len(tokenized["train"]), "eval_rows": len(tokenized["eval"])}
    (output / "training_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(metrics, ensure_ascii=False))
    return 0


if __name__ == "__main__": raise SystemExit(main())

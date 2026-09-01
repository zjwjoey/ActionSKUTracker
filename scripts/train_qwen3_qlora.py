"""Run an isolated QLoRA fine-tune for the local Qwen3 model.

The command is intentionally opt-in and writes only to the supplied F-drive
output directory.  It never changes ActionSKUTracker configuration, dictionary
files, Learning Pool, or SQLite PRIMARY.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REQUIRED_DATASET_SCHEMA_VERSION = 2
REQUIRED_NAMING_POLICY_VERSION = "NAMING_AND_SPEC_PLANNING_STANDARD_V1.0"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line_number, line in enumerate(fh, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict) or not isinstance(row.get("messages"), list):
                raise ValueError(f"INVALID_TRAINING_ROW:{line_number}")
            rows.append(row)
    if not rows:
        raise ValueError("EMPTY_TRAINING_DATA")
    return rows


def _validate_rows(train: list[dict[str, Any]], valid: list[dict[str, Any]]) -> None:
    for name, rows in (("train", train), ("valid", valid)):
        for row in rows:
            messages = row["messages"]
            if not messages or messages[-1].get("role") != "assistant":
                raise ValueError(f"{name.upper()}_MISSING_ASSISTANT_MESSAGE")
            metadata = row.get("metadata") or {}
            if not metadata.get("field") or not metadata.get("sku"):
                raise ValueError(f"{name.upper()}_MISSING_METADATA")
            user_messages = [message for message in messages if message.get("role") == "user"]
            if not user_messages:
                raise ValueError(f"{name.upper()}_MISSING_USER_POLICY")
            try:
                payload = json.loads(user_messages[-1].get("content", ""))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{name.upper()}_USER_POLICY_INVALID") from exc
            if payload.get("naming_policy_version") != REQUIRED_NAMING_POLICY_VERSION:
                raise ValueError(f"{name.upper()}_NAMING_POLICY_MISSING")
            if not payload.get("field_policy") or not payload.get("global_guardrails"):
                raise ValueError(f"{name.upper()}_FIELD_POLICY_MISSING")


def _load_and_validate_dataset_manifest(path: Path, train: list[dict[str, Any]], valid: list[dict[str, Any]]) -> dict[str, Any]:
    if not path.exists():
        raise ValueError("TRAINING_DATASET_MANIFEST_MISSING")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("TRAINING_DATASET_MANIFEST_INVALID")
    if int(manifest.get("schema_version", 0)) < REQUIRED_DATASET_SCHEMA_VERSION:
        raise ValueError("TRAINING_DATASET_POLICY_TOO_OLD")
    if manifest.get("naming_policy_version") != REQUIRED_NAMING_POLICY_VERSION:
        raise ValueError("TRAINING_DATASET_NAMING_POLICY_MISMATCH")
    if not manifest.get("policy_documents") or not manifest.get("field_policy_coverage"):
        raise ValueError("TRAINING_DATASET_POLICY_EVIDENCE_MISSING")
    for document, expected_hash in manifest["policy_documents"].items():
        document_path = Path(document)
        if not document_path.exists() or _sha256(document_path) != expected_hash:
            raise ValueError("TRAINING_DATASET_POLICY_DOCUMENT_HASH_MISMATCH")
    if manifest.get("train_count") != len(train) or manifest.get("valid_count") != len(valid):
        raise ValueError("TRAINING_DATASET_COUNT_MISMATCH")
    return manifest


def run_training(*, train_path: Path, valid_path: Path, dataset_manifest_path: Path, output_dir: Path, base_model: str, epochs: float, max_length: int, batch_size: int, grad_accum: int, lora_r: int, lora_alpha: int, learning_rate: float) -> dict[str, Any]:
    train_rows, valid_rows = _load_rows(train_path), _load_rows(valid_path)
    _validate_rows(train_rows, valid_rows)
    dataset_manifest = _load_and_validate_dataset_manifest(dataset_manifest_path, train_rows, valid_rows)
    output_dir.mkdir(parents=True, exist_ok=True)
    # Optional training dependencies are imported lazily so dataset QA and
    # CI never need CUDA/PEFT installed.
    try:
        import torch
        from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
        from transformers import (AutoModelForCausalLM, AutoTokenizer,
                                  BitsAndBytesConfig, Trainer, TrainingArguments)
    except ImportError as exc:
        raise RuntimeError(f"QLORA_DEPENDENCY_MISSING:{exc.name}") from exc
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA_REQUIRED_FOR_QLORA")

    tokenizer = AutoTokenizer.from_pretrained(base_model, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    def encode(rows: list[dict[str, Any]]) -> list[dict[str, list[int]]]:
        encoded = []
        for row in rows:
            messages = row["messages"]
            prompt_ids = tokenizer.apply_chat_template(
                messages[:-1], tokenize=True, add_generation_prompt=True, enable_thinking=False,
            )
            full_ids = tokenizer.apply_chat_template(
                messages, tokenize=True, add_generation_prompt=False, enable_thinking=False,
            )
            if full_ids[:len(prompt_ids)] != prompt_ids:
                raise ValueError("CHAT_TEMPLATE_ASSISTANT_BOUNDARY_INVALID")
            # Preserve the answer when a long source/policy prompt needs
            # truncation.  Labels are assistant-only so the LoRA learns the
            # structured answer, not how to repeat policy or source facts.
            offset = max(0, len(full_ids) - max_length)
            input_ids = full_ids[offset:]
            assistant_start = max(0, len(prompt_ids) - offset)
            encoded.append({
                "input_ids": input_ids,
                "attention_mask": [1] * len(input_ids),
                "labels": [-100] * assistant_start + input_ids[assistant_start:],
            })
        return encoded

    train_data, valid_data = encode(train_rows), encode(valid_rows)

    def collate(features: list[dict[str, list[int]]]) -> dict[str, Any]:
        max_size = max(len(row["input_ids"]) for row in features)
        padded: dict[str, list[list[int]]] = {"input_ids": [], "attention_mask": [], "labels": []}
        for row in features:
            missing = max_size - len(row["input_ids"])
            padded["input_ids"].append(row["input_ids"] + [tokenizer.pad_token_id] * missing)
            padded["attention_mask"].append(row["attention_mask"] + [0] * missing)
            padded["labels"].append(row["labels"] + [-100] * missing)
        return {key: torch.tensor(value, dtype=torch.long) for key, value in padded.items()}
    class RowsDataset:
        def __init__(self, rows: list[dict[str, list[int]]]): self.rows = rows
        def __len__(self): return len(self.rows)
        def __getitem__(self, index): return self.rows[index]

    quant = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.float16,
        bnb_4bit_use_double_quant=True,
    )
    model = AutoModelForCausalLM.from_pretrained(
        base_model, quantization_config=quant, device_map="auto", trust_remote_code=True,
    )
    model = prepare_model_for_kbit_training(model)
    model = get_peft_model(model, LoraConfig(
        r=lora_r, lora_alpha=lora_alpha, lora_dropout=0.05,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj"],
        bias="none", task_type="CAUSAL_LM",
    ))
    model.print_trainable_parameters()
    args = TrainingArguments(
        output_dir=str(output_dir), num_train_epochs=epochs,
        per_device_train_batch_size=batch_size,
        per_device_eval_batch_size=batch_size,
        gradient_accumulation_steps=grad_accum,
        learning_rate=learning_rate, logging_steps=1,
        eval_strategy="epoch", save_strategy="epoch", report_to="none",
        fp16=True, remove_unused_columns=False,
    )
    trainer = Trainer(
        model=model, args=args, train_dataset=RowsDataset(train_data),
        eval_dataset=RowsDataset(valid_data),
        data_collator=collate,
    )
    train_result = trainer.train()
    eval_result = trainer.evaluate()
    trainer.save_model(str(output_dir))
    tokenizer.save_pretrained(str(output_dir))
    manifest = {
        "schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
        "base_model": base_model, "train_path": str(train_path), "valid_path": str(valid_path),
        "dataset_manifest_path": str(dataset_manifest_path),
        "dataset_manifest_sha256": _sha256(dataset_manifest_path),
        "naming_policy_version": dataset_manifest["naming_policy_version"],
        "policy_documents": dataset_manifest["policy_documents"],
        "train_sha256": _sha256(train_path), "valid_sha256": _sha256(valid_path),
        "train_count": len(train_rows), "valid_count": len(valid_rows),
        "epochs": epochs, "max_length": max_length, "batch_size": batch_size,
        "gradient_accumulation_steps": grad_accum, "lora_r": lora_r, "lora_alpha": lora_alpha,
        "learning_rate": learning_rate, "device": torch.cuda.get_device_name(0),
        "train_metrics": train_result.metrics, "eval_metrics": eval_result,
        "production_apply": False, "dictionary_write": False, "primary_write": False,
    }
    (output_dir / "training_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train", type=Path, default=Path("runtime/local_ai/training_data/train.jsonl"))
    parser.add_argument("--valid", type=Path, default=Path("runtime/local_ai/training_data/valid.jsonl"))
    parser.add_argument("--dataset-manifest", type=Path, default=Path("runtime/local_ai/training_data/manifest.json"))
    parser.add_argument("--output-dir", type=Path, default=Path("F:/LocalAI/Adapters/action-localization-qwen3-8b"))
    parser.add_argument("--base-model", default="Qwen/Qwen3-8B")
    parser.add_argument("--epochs", type=float, default=1.0)
    parser.add_argument("--max-length", type=int, default=1024)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--grad-accum", type=int, default=8)
    parser.add_argument("--lora-r", type=int, default=8)
    parser.add_argument("--lora-alpha", type=int, default=16)
    parser.add_argument("--learning-rate", type=float, default=2e-4)
    args = parser.parse_args()
    print(json.dumps(run_training(train_path=args.train, valid_path=args.valid, dataset_manifest_path=args.dataset_manifest, output_dir=args.output_dir, base_model=args.base_model, epochs=args.epochs, max_length=args.max_length, batch_size=args.batch_size, grad_accum=args.grad_accum, lora_r=args.lora_r, lora_alpha=args.lora_alpha, learning_rate=args.learning_rate), ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

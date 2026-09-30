"""Build a naming-standard-aware Stage 5 training/evaluation artifact pair.

This is deliberately a data-only transformation.  It keeps the frozen Gold
answers unchanged, adds the same protected-fact ledger used by the runtime
pipeline, and replaces the generic system prompt with the approved naming and
dictionary-first contract.  No production data or dictionary is written.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from action_tracker.translation.model_guard import numeric_tokens, technical_tokens  # noqa: E402


PROMPT_VERSION = "STAGE5_NAMING_STANDARD_V3_NUMERIC_EXACT"
SYSTEM_PROMPT = (
    "将 Action 西语商品字段忠实标准化为简体中文。"
    "字典、固定类目和确定性规则优先；只处理请求的字段，不覆盖官网事实。"
    "品名：简洁表达商品本体，禁止价格、促销和无依据卖点；默认不保留品牌/IP，"
    "只有明确批准的例外才可保留；型号、接口、技术标准等识别信息必须保留。"
    "规格：保留原文全部数字、单位、数量、尺寸、颜色、容量、功率、型号和兼容性，"
    "不要把规格事实臆造到其他字段。描述表达卖点但不得新增事实，尤其不得把商品编号或 SKU 添加到描述；"
    "产品详情按字段名和值逐项忠实翻译，重复字段和重复数字也必须按原文出现次数全部保留。"
    "输出前逐个核对受保护事实账本，数字多重集必须与原文一致，不能漏掉重复值，也不能新增数字。"
    "不要输出西班牙语残留、HTML、null 或 undefined。仅输出一个 JSON 对象，键必须是请求字段名。"
    "该样本已通过 Owner Gold Gate；受保护事实清单仅用于校验，不能删除或改写。"
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def transform_row(row: dict[str, Any]) -> dict[str, Any]:
    messages = row.get("messages") or []
    user_message = next(message for message in messages if message.get("role") == "user")
    source = json.loads(user_message.get("content") or "{}")
    field = str((row.get("metadata") or {}).get("field") or "")
    source_value = source.get(field, "")
    ledger = {
        "numbers": list(numeric_tokens(source_value)),
        "technical_tokens": list(technical_tokens(source_value)),
    }
    user_payload = {field: source_value, "__protected_fact_ledger__": {field: ledger}}
    metadata = dict(row.get("metadata") or {})
    metadata["prompt_contract_version"] = PROMPT_VERSION
    metadata["naming_standard_applied"] = True
    metadata["dictionary_first"] = True
    return {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": canonical(user_payload)},
            next(message for message in messages if message.get("role") == "assistant"),
        ],
        "metadata": metadata,
    }


def load(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(canonical(row) + "\n" for row in rows), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--train", type=Path, required=True)
    parser.add_argument("--validation", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    train = [transform_row(row) for row in load(args.train)]
    validation = [transform_row(row) for row in load(args.validation)]
    out = args.output_dir
    out.mkdir(parents=True, exist_ok=True)
    train_path = out / "stage5_shadow_train_20260915_naming_v2.jsonl"
    validation_path = out / "stage5_shadow_validation_20260915_naming_v2.jsonl"
    write(train_path, train)
    write(validation_path, validation)
    manifest = {
        "status": "READY_FOR_ISOLATED_TRAINING",
        "prompt_contract_version": PROMPT_VERSION,
        "train": {"path": str(train_path.resolve()), "rows": len(train), "sha256": sha256(train_path)},
        "validation": {"path": str(validation_path.resolve()), "rows": len(validation), "sha256": sha256(validation_path)},
        "source_train": {"path": str(args.train.resolve()), "sha256": sha256(args.train)},
        "source_validation": {"path": str(args.validation.resolve()), "sha256": sha256(args.validation)},
        "production_writes": False,
        "dictionary_writes": False,
        "gold_answers_changed": False,
    }
    (out / "naming_training_artifact_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

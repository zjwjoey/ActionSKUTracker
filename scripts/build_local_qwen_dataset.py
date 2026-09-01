"""Build an offline, auditable Qwen fine-tuning dataset from trusted localization data.

This script is deliberately data-preparation only: it never calls a model,
never visits the Action website, and never writes the production dictionary or
PRIMARY database.  Generated JSONL/manifest files belong under ``runtime/``.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from action_tracker.localization.policy import has_ordinary_spanish
from action_tracker.localization.validator import _NUMBER


TRUSTED_STATUSES = frozenset({"HUMAN_REVIEWED", "LOCKED", "SEED_REVIEWED", "CAT1_CONFIRMED"})
PRODUCT_FIELDS = (
    ("name", "name_es_raw", "name_zh_standard"),
    ("spec", "spec_es_raw", "spec_zh_standard"),
    ("cat1", "cat1_es", "cat1_zh"),
    ("cat2", "cat2_es", "cat2_zh"),
)
TERM_FILES = (
    ("term_dictionary.csv", "term_es", "term_zh", "TERM"),
    ("phrase_dictionary.csv", "phrase_es", "phrase_zh", "PHRASE"),
    ("tech_token_dictionary.csv", "token_es", "canonical_token", "TECH_TOKEN"),
    ("product_type_dictionary.csv", "source_term", "canonical_zh", "PRODUCT_TYPE"),
    ("detail_key_dictionary.csv", "key_es", "key_zh", "DETAIL_KEY"),
)

# This is a compact, machine-readable projection of the two governing
# standards.  It is deliberately field-specific: training the model with a
# generic "translate accurately" instruction is not equivalent to teaching
# the placement contract for 商品品名 / 规格 / 分类.  The source documents and
# their hashes are recorded in the manifest for auditability.
NAMING_POLICY_VERSION = "NAMING_AND_SPEC_PLANNING_STANDARD_V1.0"
LOCALIZATION_POLICY_VERSION = "CHINESE_LOCALIZATION_STANDARD_V1.0"
POLICY_DOCUMENTS = (
    "docs/NAMING_AND_SPEC_PLANNING_STANDARD.md",
    "docs/CHINESE_LOCALIZATION_STANDARD.md",
)
FIELD_POLICIES: dict[str, list[str]] = {
    "name": [
        "品名只回答这是什么商品：简短、稳定、可搜索，核心商品类型必须中文化且同类统一。",
        "不把尺寸、容量、重量、数量、包装数、颜色、价格和促销机械塞进品名；这些是规格事实。",
        "只在有正式证据且确实影响商品身份时保留确认品牌、系列/IP、接口、技术词或型号；禁止猜品牌、系列和营销形容词。",
        "技术词、接口、标准和型号不得误翻或丢失；当其定义商品类型时可进入品名，否则由规格承载。",
    ],
    "spec": [
        "规格回答同一商品买哪一种：优先尺寸、容量、重量、数量、尺码、颜色、接口、型号、适配范围和电气/技术参数。",
        "规格只使用源事实；不得凭常识补参数。数字、型号、接口、技术 Token 必须保留，允许的仅是格式标准化。",
        "格式：× 作为乘号，– 作为范围，｜ 分隔多项，、 分隔枚举；数字与单位不留空格，例如 50×60cm、220–240V、4000mAh。",
        "数量量词须按商品语义，无法可靠判断才使用受控 fallback 件并标记低置信度。",
    ],
    "cat1": [
        "分类1只能是固定的15个中文类目之一；未知映射必须进入 CATEGORY_REVIEW，禁止创造第16类。",
    ],
    "cat2": [
        "分类2是受控字典映射，不是自由翻译；未知值只能候选或 Review，不能编造正式值。",
    ],
    "description": [
        "描述是客观、简洁的用途和明确卖点摘要；源描述为空时必须保持空，禁止凭名称或常识创作。",
    ],
    "details": [
        "详情使用已确认字段名和字段：值；格式；普通西语值要中文化，详情商品编号必须等于当前 SKU。",
    ],
}
SYSTEM_POLICY = (
    "你是商品结构化中文标准化引擎。只返回 JSON 对象，禁止 Markdown 和解释。"
    "只处理 requested_fields；所有输出必须遵守随请求给出的字段级规划规则。"
    "保留全部数字和允许的技术 Token，未知内容不得猜测，不得改变官网事实。"
)


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return [dict(row) for row in csv.DictReader(fh)]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _trusted(row: dict[str, str]) -> bool:
    review = _text(row.get("review_status")).upper()
    translation = _text(row.get("translation_status")).upper()
    locked = _text(row.get("locked")).lower() in {"1", "true", "yes", "locked"}
    return review in TRUSTED_STATUSES or translation in TRUSTED_STATUSES or locked and review not in {"NEEDS_REVIEW", "NEEDS_HUMAN_REVIEW"}


def _known_tokens(dictionary_dir: Path) -> set[str]:
    tokens: set[str] = set()
    for row in _read_csv(dictionary_dir / "brand_dictionary.csv"):
        tokens.update(filter(None, (_text(row.get("canonical_name")), *(_text(row.get("aliases_es")).split("|")))))
    for row in _read_csv(dictionary_dir / "tech_token_dictionary.csv"):
        tokens.update(filter(None, (_text(row.get("token_es")), _text(row.get("canonical_token")))))
    return tokens


def _numeric_ok(source: str, target: str) -> bool:
    expected = {value.replace(",", ".") for value in _NUMBER.findall(source)}
    found = {value.replace(",", ".") for value in _NUMBER.findall(target)}
    return expected <= found


def _target_ok(target: str, known_tokens: set[str]) -> bool:
    # A translation example must contain Chinese unless it is intentionally a
    # model/brand/technical-token preservation example.
    if not re.search(r"[\u3400-\u9fff]", target) and not any(token and token.casefold() in target.casefold() for token in known_tokens):
        return False
    return not has_ordinary_spanish(target, allowed_tokens=known_tokens)


def _normalize_target(field: str, target: str) -> str:
    """Apply only deterministic output formatting required by the standard."""
    if field != "spec":
        return target
    normalized = target.replace("|", "｜")
    normalized = re.sub(r"(?<=\d)[xX](?=\d)", "×", normalized)
    # Compact retail units (50 ml -> 50ml; 220 V -> 220V).  Do not touch
    # ordinary Chinese word spacing because it may carry meaning.
    normalized = re.sub(r"(?<=\d)\s+(?=(?:mAh|ml|mg|kg|cm|mm|m|g|L|W|V|A|Hz|lm|°C)\b)", "", normalized)
    return normalized.strip()


def _record(*, sku: str, source_hash: str, field: str, source: str, target: str, source_facts: dict[str, str], source_kind: str) -> dict[str, Any]:
    response = {
        "sku": sku,
        "source_hash": source_hash,
        "fields": {field: target},
        "semantic_items": [],
        "product_type_candidate": None,
        "detail_key_candidates": [],
        "tech_token_candidates": [],
        "confidence": 1.0,
    }
    user = {
        "sku": sku,
        "source_hash": source_hash,
        "source_facts": source_facts,
        "requested_fields": [field],
        "source_field": field,
        "source_text": source,
        "naming_policy_version": NAMING_POLICY_VERSION,
        "localization_policy_version": LOCALIZATION_POLICY_VERSION,
        "field_policy": FIELD_POLICIES.get(field, FIELD_POLICIES["name"]),
        "global_guardrails": [
            "普通西班牙语零容忍；只有已确认品牌、系列/IP、技术 Token、型号和标准单位可保留拉丁 Token。",
            "字典和人工覆盖优先；AI 只生成候选，不直接写入正式字段。",
            "不得增加或删除官网事实；数字仅可做单位和格式规范化。",
        ],
    }
    return {
        "messages": [
            {"role": "system", "content": "你是商品结构化中文标准化引擎。只返回 JSON 对象，禁止 Markdown 和解释。只处理 requested_fields，保留数字和技术 Token，未知内容不得猜测。"},
            {"role": "user", "content": json.dumps(user, ensure_ascii=False, sort_keys=True)},
            {"role": "assistant", "content": json.dumps(response, ensure_ascii=False, sort_keys=True)},
        ],
        "metadata": {"sku": sku, "source_hash": source_hash, "field": field, "source_kind": source_kind},
    }


def _resolve_policy_documents(policy_docs: Iterable[Path] | None = None) -> dict[str, str]:
    if policy_docs is None:
        repository_root = Path(__file__).resolve().parents[1]
        policy_docs = (repository_root / relative for relative in POLICY_DOCUMENTS)
    resolved = {str(Path(path)): _sha256(Path(path)) for path in policy_docs}
    if not resolved or any(not Path(path).exists() for path in resolved):
        raise FileNotFoundError("NAMING_POLICY_DOCUMENT_MISSING")
    return resolved


def build_dataset(
    dictionary_dir: Path,
    output_dir: Path,
    *,
    valid_ratio: float = 0.1,
    policy_docs: Iterable[Path] | None = None,
) -> dict[str, Any]:
    dictionary_dir = Path(dictionary_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    policy_document_hashes = _resolve_policy_documents(policy_docs)
    known_tokens = _known_tokens(dictionary_dir)
    examples: list[dict[str, Any]] = []
    skipped: Counter[str] = Counter()
    seen: set[tuple[str, str, str, str]] = set()

    for row in _read_csv(dictionary_dir / "product_dictionary.csv"):
        if not _trusted(row):
            skipped["UNTRUSTED_STATUS"] += 1
            continue
        sku = _text(row.get("sku"))
        if not sku:
            skipped["MISSING_SKU"] += 1
            continue
        source_facts = {key: _text(row.get(key)) for key in ("name_es_raw", "cat1_es", "cat2_es", "spec_es_raw")}
        for field, source_key, target_key in PRODUCT_FIELDS:
            source, target = _text(row.get(source_key)), _normalize_target(field, _text(row.get(target_key)))
            if not source or not target:
                skipped[f"EMPTY_{field.upper()}"] += 1
                continue
            if not _numeric_ok(source, target):
                skipped["NUMERIC_MISMATCH"] += 1
                continue
            if not _target_ok(target, known_tokens):
                skipped["TARGET_NOT_TRUSTWORTHY"] += 1
                continue
            key = (sku, field, source, target)
            if key in seen:
                skipped["DUPLICATE"] += 1
                continue
            seen.add(key)
            examples.append(_record(sku=sku, source_hash=_text(row.get("source_hash")), field=field, source=source, target=target, source_facts=source_facts, source_kind="product_dictionary"))

    for filename, source_key, target_key, source_kind in TERM_FILES:
        for row in _read_csv(dictionary_dir / filename):
            if not _trusted(row):
                skipped["UNTRUSTED_STATUS"] += 1
                continue
            source, target = _text(row.get(source_key)), _text(row.get(target_key))
            if not source or not target:
                skipped["EMPTY_TERM"] += 1
                continue
            if not _numeric_ok(source, target) or not _target_ok(target, known_tokens):
                skipped["TERM_NOT_TRUSTWORTHY"] += 1
                continue
            sku = "TERM-" + hashlib.sha256(f"{source_kind}|{source}".encode("utf-8")).hexdigest()[:16]
            key = (sku, "term", source, target)
            if key in seen:
                skipped["DUPLICATE"] += 1
                continue
            seen.add(key)
            examples.append(_record(sku=sku, source_hash="", field="name", source=source, target=target, source_facts={"name_es_raw": source}, source_kind=source_kind))

    # Split by SKU/group key so the same product cannot leak from train into
    # validation through another field.
    train: list[dict[str, Any]] = []
    valid: list[dict[str, Any]] = []
    for example in sorted(examples, key=lambda item: (item["metadata"]["sku"], item["metadata"]["field"])):
        group = example["metadata"]["sku"]
        bucket = int(hashlib.sha256(group.encode("utf-8")).hexdigest()[:8], 16) / 0xFFFFFFFF
        (valid if bucket < valid_ratio else train).append(example)
    if examples and not valid:
        valid.append(train.pop())
    if examples and not train:
        train.append(valid.pop())

    def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
        with path.open("w", encoding="utf-8", newline="\n") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    write_jsonl(output_dir / "train.jsonl", train)
    write_jsonl(output_dir / "valid.jsonl", valid)
    source_files = {path.name: _sha256(path) for path in sorted(dictionary_dir.glob("*.csv"))}
    manifest = {
        "schema_version": 2,
        "policy_version": LOCALIZATION_POLICY_VERSION,
        "naming_policy_version": NAMING_POLICY_VERSION,
        "policy_documents": policy_document_hashes,
        "field_policy_coverage": sorted(FIELD_POLICIES),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_directory": str(dictionary_dir),
        "source_files": source_files,
        "train_count": len(train),
        "valid_count": len(valid),
        "total_count": len(examples),
        "train_skus": len({row["metadata"]["sku"] for row in train}),
        "valid_skus": len({row["metadata"]["sku"] for row in valid}),
        "skipped": dict(skipped),
        "production_apply": False,
        "model_calls": 0,
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return {**manifest, "output_dir": str(output_dir)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dictionary-dir", type=Path, default=Path("data/dictionary"))
    parser.add_argument("--output-dir", type=Path, default=Path("runtime/local_ai/training_data"))
    parser.add_argument("--valid-ratio", type=float, default=0.1)
    args = parser.parse_args()
    if not 0 < args.valid_ratio < 1:
        parser.error("--valid-ratio must be between 0 and 1")
    print(json.dumps(build_dataset(args.dictionary_dir, args.output_dir, valid_ratio=args.valid_ratio), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

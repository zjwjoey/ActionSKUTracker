"""从增量 SKU 提取可人工审核的西语术语候选。

候选永不直接写入正式 term_dictionary.csv；它们先作为 TERM_CANDIDATE 证据
进入统一 Review Queue，由人工决定中文和适用类型。
"""
from __future__ import annotations

import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from .dictionary import PRODUCT_DICTIONARY_HEADERS, TERM_DICTIONARY_HEADERS, load_dictionary_rows
from .dictionary_enrichment import DictionaryEnrichmentError, _snapshot_for_run, _validate_formal_snapshot


TERM_CANDIDATE_HEADERS = [
    "term_es", "suggested_zh", "term_type", "occurrence_count", "sku_count",
    "cat1_distribution", "sample_contexts", "source_dates", "decision", "review_status",
]
_WORD_RE = re.compile(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]{3,}")
_STOP_WORDS = frozenset({
    "con", "sin", "para", "por", "del", "las", "los", "una", "uno", "unos", "unas", "que",
    "como", "sobre", "entre", "desde", "hasta", "todo", "todos", "toda", "todas", "más", "muy",
    "este", "esta", "estos", "estas", "también", "solo", "cada", "sus", "son", "the", "and",
})


class TermCandidateError(RuntimeError):
    """候选来源或阈值不满足可审计要求。"""


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _key(value: str) -> str:
    return " ".join(value.casefold().split())


def _terms_in_text(value: str) -> set[str]:
    words = [_key(word) for word in _WORD_RE.findall(value)]
    filtered = [word for word in words if word not in _STOP_WORDS]
    result = set(filtered)
    # 只保留由两个实词构成的短语，过滤“para el”等没有固定商品语义的搭配。
    result.update(f"{left} {right}" for left, right in zip(filtered, filtered[1:]))
    return result


def _write_csv(path: Path, headers: list[str], rows: Iterable[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def extract_term_candidates(cfg: dict[str, Any], *, run_id: str, min_sku_count: int = 2) -> dict[str, Any]:
    """从正式 run 的 Step 4 候选 SKU 抽取候选，完全离线、无词典写入。"""
    if min_sku_count < 1:
        raise TermCandidateError("MIN_SKU_COUNT_MUST_BE_POSITIVE")
    try:
        snapshot = _snapshot_for_run(cfg, run_id)
        _validate_formal_snapshot(snapshot, run_id)
    except DictionaryEnrichmentError as exc:
        raise TermCandidateError(str(exc)) from exc
    dictionary = Path(cfg["paths"]["dictionary"])
    selected_path = dictionary / "enrichment" / run_id / "selected_skus.csv"
    if not selected_path.exists():
        raise TermCandidateError(f"ENRICHMENT_EVIDENCE_REQUIRED: {run_id}")
    with selected_path.open("r", encoding="utf-8-sig", newline="") as fh:
        selected_skus = {_text(row.get("sku")) for row in csv.DictReader(fh) if _text(row.get("sku"))}
    products = {row["sku"]: row for row in load_dictionary_rows(
        dictionary / "product_dictionary.csv", headers=PRODUCT_DICTIONARY_HEADERS, key_fields=("sku",),
    )}
    terms = load_dictionary_rows(dictionary / "term_dictionary.csv", headers=TERM_DICTIONARY_HEADERS, key_fields=("term_es", "term_type"))
    known_terms = {_key(row["term_es"]) for row in terms if _text(row.get("term_es"))}
    source_date = run_id
    report_path = snapshot / "run_report.json"
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
        source_date = _text(report.get("run_date") or report.get("observation_date")) or run_id
    except (OSError, json.JSONDecodeError):
        # run_id remains an auditable source identifier when older snapshots lack a date.
        pass

    by_term: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "skus": set(), "occurrences": 0, "categories": Counter(), "examples": [], "zh_contexts": [],
    })
    scanned_skus = 0
    for sku in sorted(selected_skus):
        product = products.get(sku)
        if not product:
            continue
        scanned_skus += 1
        es_texts = [product.get("name_es_raw", ""), product.get("spec_es_raw", "")]
        zh_context = " ｜ ".join(part for part in (product.get("name_zh_standard", ""), product.get("spec_zh_standard", "")) if _text(part))
        category = _text(product.get("cat1_zh")) or "未映射类目"
        for text in es_texts:
            for term in _terms_in_text(_text(text)):
                # 已有正式词的拼接（如 ``diferentes variantes``）并不是新的
                # 可维护术语；避免把已有字典的组合重复送给人工审核。
                if term in known_terms or any(part in known_terms for part in term.split()):
                    continue
                entry = by_term[term]
                entry["occurrences"] += 1
                entry["skus"].add(sku)
                entry["categories"][category] += 1
                if _text(text) not in entry["examples"] and len(entry["examples"]) < 3:
                    entry["examples"].append(_text(text))
                if zh_context and zh_context not in entry["zh_contexts"] and len(entry["zh_contexts"]) < 3:
                    entry["zh_contexts"].append(zh_context)

    rows: list[dict[str, str]] = []
    for term, item in sorted(by_term.items(), key=lambda pair: (-len(pair[1]["skus"]), -pair[1]["occurrences"], pair[0])):
        if len(item["skus"]) < min_sku_count:
            continue
        rows.append({
            "term_es": term,
            # 不猜测中文或词性；人工可以在 review-queue decide 时确认。
            "suggested_zh": "",
            "term_type": "general",
            "occurrence_count": str(item["occurrences"]),
            "sku_count": str(len(item["skus"])),
            "cat1_distribution": json.dumps(dict(item["categories"].most_common()), ensure_ascii=False, sort_keys=True),
            "sample_contexts": " || ".join(item["examples"]),
            # 来源日期/运行证据保留为稳定字符串，不将其错误宣称为逐词译法。
            "source_dates": source_date,
            "decision": "PENDING",
            "review_status": "PENDING",
        })
    output = dictionary / "term_candidates" / run_id / "term_candidates.csv"
    _write_csv(output, TERM_CANDIDATE_HEADERS, rows)
    return {
        "run_id": run_id, "snapshot": str(snapshot), "selected_skus": len(selected_skus),
        "scanned_skus": scanned_skus, "candidate_terms": len(rows), "min_sku_count": min_sku_count,
        "output": str(output), "model_or_network_called": False, "term_dictionary_changed": False,
    }

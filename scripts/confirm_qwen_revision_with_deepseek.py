"""Second-pass confirmation of the 352 model-revised translation records.

This produces a review artifact only. It never writes the dictionary, Master,
or production localization tables.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
import urllib.request
from collections import Counter
from pathlib import Path

NUM = re.compile(r"\d+(?:[.,]\d+)?")
FIELDS = ("name", "cat1", "cat2", "spec", "description", "details")


def nums(value: object) -> list[str]:
    return sorted(x.replace(",", ".") for x in NUM.findall(str(value or "")))


SYSTEM = """你是 Action 西班牙站中文训练数据的第二位严格复核员。
只核对给定西语 source、原中文 original_target、以及修正版 corrected 的六个字段：
name、cat1、cat2、spec、description、details。
判断 corrected 是否忠实于 source：数字、单位、尺寸、数量、是/否、品牌和型号不能错误或臆造；普通西语应翻译；cat1 只能使用既定的 15 个一级类目；源没有的信息不能补充。
若修正版完全正确，verdict=CONFIRMED；若有明确错误但可据源修正，verdict=REJECTED 并给出完整六字段 corrected；若源字段本身冲突或无法判断，verdict=ESCALATE。
只返回 JSON：{"items":[{"sku":"","verdict":"CONFIRMED|REJECTED|ESCALATE","corrected":{六字段},"reason":""}]}，必须返回全部 SKU。"""


def call(key: str, batch: list[dict]) -> list[dict]:
    payload = {
        "model": "deepseek-chat",
        "temperature": 0.0,
        "max_tokens": 12000,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": "逐条进行第二次确认：\n" + json.dumps({"items": batch}, ensure_ascii=False)},
        ],
    }
    req = urllib.request.Request(
        "https://api.deepseek.com/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as response:
        body = json.loads(response.read().decode("utf-8"))
    return json.loads(body["choices"][0]["message"]["content"])["items"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="2026-09-08")
    ap.add_argument("--batch-size", type=int, default=8)
    ap.add_argument("--sleep", type=float, default=1.0)
    args = ap.parse_args()
    root = Path(__file__).resolve().parents[1]
    base = root / "runtime" / "training" / "qwen3_8b" / args.date.replace("-", "")
    source = [json.loads(x) for x in (base / "qwen_gold_review_1200.jsonl").open(encoding="utf-8") if x.strip()]
    source = [r for r in source if r.get("verdict") == "REVISE"]
    candidates = {
        str((r := json.loads(x))["metadata"]["sku"]): r["metadata"]
        for x in (base / "qwen_candidates_5000.jsonl").open(encoding="utf-8")
        if x.strip()
    }
    out = base / "qwen_revision_confirmation_352.jsonl"
    existing = [json.loads(x) for x in out.open(encoding="utf-8") if x.strip()] if out.exists() else []
    done = {str(r["sku"]) for r in existing}
    pending = [r for r in source if str(r["sku"]) not in done]
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        raise RuntimeError("DEEPSEEK_API_KEY_MISSING")
    with out.open("a", encoding="utf-8") as handle:
        for offset in range(0, len(pending), max(1, args.batch_size)):
            batch = pending[offset : offset + max(1, args.batch_size)]
            request_items = [
                {
                    "sku": r["sku"],
                    "source": r["source"],
                    "original_target": r["original_target"],
                    "corrected": r.get("corrected") or {},
                }
                for r in batch
            ]
            last_error = None
            for attempt in range(3):
                try:
                    answer = call(key, request_items)
                    by_sku = {str(r.get("sku")): r for r in answer}
                    if any(str(r["sku"]) not in by_sku for r in batch):
                        raise ValueError("DEEPSEEK_MISSING_SKU")
                    for r in batch:
                        v = by_sku[str(r["sku"])]
                        corrected = v.get("corrected") or r.get("corrected") or {}
                        flags = [f for f in FIELDS if nums(r["source"].get(f)) != nums(corrected.get(f))]
                        result = {
                            "sku": r["sku"], "verdict": v.get("verdict"), "corrected": corrected,
                            "reason": v.get("reason", ""), "numeric_flags": flags,
                            "source_hash": candidates.get(str(r["sku"]), {}).get("source_hash", ""),
                            "reviewer": "deepseek-chat-second-pass",
                        }
                        handle.write(json.dumps(result, ensure_ascii=False) + "\n")
                    handle.flush()
                    break
                except Exception as exc:  # network/API failures are resumable
                    last_error = exc
                    time.sleep(2**attempt)
            else:
                raise RuntimeError(f"BATCH_FAILED offset={offset}: {last_error}")
            print(f"confirmed {min(offset + len(batch), len(pending))}/{len(pending)}", flush=True)
            if args.sleep: time.sleep(args.sleep)
    all_rows = [json.loads(x) for x in out.open(encoding="utf-8") if x.strip()]
    summary = {
        "selected": len(source), "completed": len(all_rows),
        "verdicts": dict(Counter(r.get("verdict") for r in all_rows)),
        "numeric_flag_rows": sum(bool(r.get("numeric_flags")) for r in all_rows),
        "output": str(out),
    }
    (base / "revision_confirmation_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

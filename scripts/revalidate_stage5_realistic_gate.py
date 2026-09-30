"""Re-run Guard/classification on an existing realistic Stage 5 inference.

No model call and no production write: this is useful when only the reject-only
Guard changes, so expensive inference does not need to be repeated.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))
import qwen_offline_stage5 as stage5  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", type=Path, required=True)
    ap.add_argument("--candidates", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    rows = stage5._inference_rows(stage5._load_rows(args.fixture))
    old = stage5._load_rows(args.candidates)
    if len(rows) != len(old):
        raise ValueError("FIXTURE_CANDIDATE_ROW_COUNT_MISMATCH")
    from action_tracker.config import load_settings
    from action_tracker.exporting.dictionary_join import load_dictionary_context
    cfg = load_settings(ROOT / "config" / "settings.yaml")
    context = load_dictionary_context(cfg)
    closed = stage5.rule_resolutions(rows, context)
    allowed = stage5._brand_phrases(rows)
    candidates = []
    for row, previous, rule_fields in zip(rows, old, closed):
        prediction, repairs = stage5.repair_model_output(
            stage5._source_from_row(row), previous.get("prediction")
        )
        candidate = stage5.classify_candidate(
            row, prediction, allowed_brand_phrases=allowed.get(str((row.get("metadata") or {}).get("sku")), ()),
            rule_fields=rule_fields,
        )
        candidate["raw_model_output"] = previous.get("prediction")
        candidate["model_repairs"] = repairs
        candidates.append(candidate)
    accepted = sum(bool(row.get("accepted_by_guard")) for row in candidates)
    summary = {
        "rows": len(candidates),
        "accepted_by_guard": accepted,
        "review_required": len(candidates) - accepted,
        "production_writes": False,
        "guard_only_revalidation": True,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        for row in candidates:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    args.output.with_suffix(".manifest.json").write_text(json.dumps({
        "input_fixture": str(args.fixture.resolve()),
        "input_candidates": str(args.candidates.resolve()),
        "output": str(args.output.resolve()),
        "summary": summary,
        "model_called": False,
        "production_writes": False,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

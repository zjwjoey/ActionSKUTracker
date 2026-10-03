"""Create deterministic no-brand display repairs for a retranslation batch.

This is intentionally narrower than Qwen repair.  It only removes reviewed
BRAND/IP spans from Chinese display fields when the source field contains the
same span and the resulting candidate remains non-empty.  Brand-only names
are left unresolved for Owner review rather than replaced with an invented
product type.
"""
from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path

from action_tracker.localization.hashes import value_hash
from action_tracker.localization.policy import strip_forbidden_display_tokens


BRAND_IP = {
    "pepsi", "bic", "action", "spargo", "mars", "magnum", "milka", "trolli",
    "pattex", "ziki", "whiskas", "winston", "roschen", "aida", "tala",
    "disney", "pokemon", "marvel", "hello kitty", "barbie",
    "intex", "la cucina", "werckmann", "aloi", "buena comida", "tomado",
    "dumil", "spectrum", "spilbergen", "twix", "kleenex", "lotto", "welly nex",
    "ajax", "medisana", "bison kit", "home deco", "comfibeds", "pelifix",
    "alison & mae", "bref", "lion", "pairz", "kate", "sau'cee", "office essentials",
    "maoam", "crunch", "creall", "maom",
}


def _tokens(source: str, candidate: str) -> list[str]:
    source_lower = str(source or "").casefold()
    candidate_lower = str(candidate or "").casefold()
    return sorted(
        {token for token in BRAND_IP if token in source_lower and token in candidate_lower},
        key=len,
        reverse=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    batch = Path(args.batch_dir).resolve()
    output = Path(args.output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    rows = []
    unresolved = []
    with (batch / "retranslation_candidates.csv").open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            tokens = _tokens(row.get("source_es", ""), row.get("candidate_zh", ""))
            if not tokens:
                continue
            cleaned = strip_forbidden_display_tokens(row.get("candidate_zh", ""), tokens)
            if not cleaned:
                unresolved.append({
                    "sku": row.get("sku", ""), "field_name": row.get("field_name", ""),
                    "source_es": row.get("source_es", ""),
                    "candidate_zh": row.get("candidate_zh", ""),
                    "brand_tokens": ";".join(tokens),
                    "reason": "BRAND_ONLY_DISPLAY_REQUIRES_OWNER_PRODUCT_TYPE",
                })
                continue
            if cleaned == row.get("candidate_zh", ""):
                continue
            rows.append({
                "sku": row.get("sku", ""), "field_name": row.get("field_name", ""),
                "targeted_candidate_zh": cleaned,
                "status": "CANDIDATE_ONLY", "provider": "deterministic",
                "model": "display_policy", "source": "TOKEN_POLICY",
                "old_candidate_zh": row.get("candidate_zh", ""),
                "brand_tokens": ";".join(tokens),
                "candidate_hash": value_hash(cleaned),
            })
    fields = list(rows[0]) if rows else [
        "sku", "field_name", "targeted_candidate_zh", "status", "provider",
        "model", "source", "old_candidate_zh", "brand_tokens", "candidate_hash",
    ]
    with (output / "targeted_retranslation_repairs.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader(); writer.writerows(rows)
    unresolved_fields = list(unresolved[0]) if unresolved else ["sku", "field_name", "source_es", "candidate_zh", "brand_tokens", "reason"]
    with (output / "policy_repair_unresolved.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=unresolved_fields)
        writer.writeheader(); writer.writerows(unresolved)
    print({"repair_candidates": len(rows), "unresolved_brand_only": len(unresolved), "output_dir": str(output)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

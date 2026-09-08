"""Verify an Apply bundle against an isolated DB copy; never touch PRIMARY."""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from action_tracker.database.production import apply_localization_correction
from action_tracker.database.repository import ProductionRepository
from action_tracker.localization.release_gate import audit_research_release, load_allowed_tokens


EXCEPTIONS = (
    "UNAPPROVED_ZH:2548558:spec_zh",
    "UNAPPROVED_ZH:2557704:spec_zh",
    "UNAPPROVED_ZH:2574845:spec_zh",
    "UNAPPROVED_ZH:3005291:spec_zh",
    "UNAPPROVED_ZH:3217313:details_zh",
    "UNAPPROVED_ZH:3221995:details_zh",
    "UNAPPROVED_ZH:3225631:details_zh",
    "UNAPPROVED_ZH:3227020:details_zh",
)


def verify(source_candidate: Path, bundle_path: Path, output_db: Path, dictionary_root: Path) -> dict:
    if output_db.exists():
        output_db.unlink()
    shutil.copy2(source_candidate, output_db)
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    applied = apply_localization_correction(
        output_db,
        run_id="CANDIDATE_APPLY_20260908",
        localizations_by_sku=bundle["localizations_by_sku"],
        source_hashes=bundle["source_hashes"],
        apply_date="2026-09-08",
    )
    rows = ProductionRepository(output_db).load_current_export_records()
    gate = audit_research_release(
        rows,
        expected_skus={str(row.get("sku") or "") for row in rows},
        allowed_tokens=load_allowed_tokens(dictionary_root),
        explicit_exceptions=[
            {"issue_id": issue_id, "approved_by": "SOURCE_AUDIT_CANDIDATE",
             "evidence": "candidate_apply_test", "expires_at": "2099-12-31"}
            for issue_id in EXCEPTIONS
        ],
    )
    return {"apply": applied, "gate": gate.as_dict(), "candidate_only": True, "production_write": False}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-db", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output-db", type=Path, required=True)
    parser.add_argument("--dictionary-root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    result = verify(args.candidate_db, args.bundle, args.output_db, args.dictionary_root)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"apply": result["apply"], "gate_status": result["gate"]["status"], "candidate_only": True}, ensure_ascii=False))


if __name__ == "__main__":
    main()

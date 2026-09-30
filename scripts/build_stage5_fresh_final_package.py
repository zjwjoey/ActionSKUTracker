"""Build a single immutable handoff manifest for the Stage 5 fresh package."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    base = Path(r"F:\ActionSKUTracker\runtime\training\qwen3_8b\20260914\stage5_fresh_source_versions_current")
    owner = base / "owner_approved_20260914_v2"
    shadow = base / "shadow_training_20260914_41_v3"
    output = base / "final_package_20260914"
    output.mkdir(parents=True, exist_ok=True)
    refs = {
        "source_candidates": base / "stage5_fresh_source_candidates_20260914.jsonl",
        "owner_workbook": Path(r"D:\Users\Administrator\Downloads\STAGE5_FRESH_OWNER_REVIEW_20260914_AI_PREFILLED.xlsx"),
        "owner_decision_audit": owner / "stage5_fresh_owner_decision_audit_20260914.json",
        "gate_preflight": owner / "stage5_fresh_formal_gate_preflight_20260914.json",
        "train": owner / "stage5_fresh_train_20260914.jsonl",
        "validation": owner / "stage5_fresh_validation_20260914.jsonl",
        "shadow_acceptance": shadow / "STAGE5_SHADOW_TRAINING_ACCEPTANCE_20260914.json",
        "shadow_metrics": shadow / "validation_metrics.json",
        "shadow_adapter": shadow / "adapter/adapter_model.safetensors",
    }
    artifacts = {
        key: {"path": str(path), "sha256": sha256(path)}
        for key, path in refs.items()
        if path.exists()
    }
    owner_audit = json.loads(Path(refs["owner_decision_audit"]).read_text(encoding="utf-8"))
    gate = json.loads(Path(refs["gate_preflight"]).read_text(encoding="utf-8"))
    shadow_audit = json.loads(Path(refs["shadow_acceptance"]).read_text(encoding="utf-8"))
    manifest = {
        "artifact_type": "STAGE5_FRESH_FINAL_HANDOFF_PACKAGE",
        "artifact_version": "V1",
        "date": "2026-09-14",
        "owner_decisions_treated_as_final": True,
        "owner_decision_counts": owner_audit["decision_counts"],
        "owner_accepted_rows": owner_audit["counts"]["owner_accepted"],
        "guard_pass_rows": owner_audit["counts"]["guard_pass"],
        "guard_exception_rows": owner_audit["counts"]["guard_exceptions"],
        "ambiguous_rows": owner_audit["counts"]["owner_isolated"],
        "train_rows": gate["train_rows"],
        "validation_rows": gate["validation_rows"],
        "historical_sku_overlap": gate["historical_sku_overlap"],
        "historical_source_hash_overlap": gate["historical_source_hash_overlap"],
        "historical_family_overlap": gate["historical_family_overlap"],
        "train_validation_sku_overlap": gate["train_validation_sku_overlap"],
        "train_validation_family_overlap": gate["train_validation_family_overlap"],
        "shadow_training": {
            "status": shadow_audit["status"],
            "completed_steps": shadow_audit["completed_steps"],
            "train_loss": shadow_audit["train_loss"],
            "best_eval_loss": shadow_audit["best_eval_loss"],
            "validation_json_parse_rate": shadow_audit["validation"]["json_parse_rate"],
            "validation_numeric_preservation_rate": shadow_audit["validation"]["numeric_preservation_rate"],
            "validation_numeric_hallucination_rate": shadow_audit["validation"]["numeric_hallucination_rate"],
            "validation_spanish_residual_rate": shadow_audit["validation"]["spanish_residual_rate"],
        },
        "artifacts": artifacts,
        "production_writes": {"master": False, "dictionary": False, "sqlite": False},
        "formal_training_release": False,
        "full_stage4_release": False,
        "status": "SHADOW_COMPLETE_FORMAL_GATE_BLOCKED_BY_STAGE4_RELEASE_FALSE",
    }
    manifest_path = output / "STAGE5_FRESH_FINAL_HANDOFF_PACKAGE_20260914.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    readme = [
        "# Stage 5 Fresh Final Handoff Package",
        "",
        "This package consolidates source candidates, Owner decisions, Gate preflight, and the offline Shadow adapter.",
        "",
        f"- Owner accepted: {manifest['owner_accepted_rows']}",
        f"- Guard-passing training rows: {manifest['guard_pass_rows']}",
        f"- Train / validation: {manifest['train_rows']} / {manifest['validation_rows']}",
        f"- Shadow status: {manifest['shadow_training']['status']}",
        "- Production writes: 0",
        "- Formal training release: blocked while FULL_STAGE4_RELEASE=false",
        "",
        f"Machine-readable manifest: `{manifest_path}`",
    ]
    (output / "README.md").write_text("\n".join(readme) + "\n", encoding="utf-8")
    files = sorted(p for p in output.iterdir() if p.is_file() and p.name != "SHA256SUMS.txt")
    (output / "SHA256SUMS.txt").write_text("".join(f"{sha256(p)}  {p.name}\n" for p in files), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

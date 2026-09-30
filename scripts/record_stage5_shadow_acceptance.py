"""Record a reproducible, non-production acceptance manifest for Stage 5 shadow training."""
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
    shadow = Path(r"F:\ActionSKUTracker\runtime\training\qwen3_8b\20260914\stage5_fresh_source_versions_current\shadow_training_20260914_41_v3")
    training = json.loads((shadow / "training_manifest.json").read_text(encoding="utf-8"))
    metrics = json.loads((shadow / "smoke_metrics.json").read_text(encoding="utf-8"))
    validation = json.loads((shadow / "validation_metrics.json").read_text(encoding="utf-8"))
    state_files = sorted(shadow.rglob("trainer_state.json"))
    trainer_state = json.loads(state_files[0].read_text(encoding="utf-8")) if state_files else {}
    best_eval_loss = trainer_state.get("best_metric")
    manifest = {
        "artifact_type": "STAGE5_SHADOW_TRAINING_ACCEPTANCE",
        "artifact_version": "V1",
        "mode": "OFFLINE_SHADOW_ONLY",
        "model_path": training["model_path"],
        "adapter_path": str(shadow / "adapter"),
        "adapter_sha256": sha256(shadow / "adapter" / "adapter_model.safetensors"),
        "train_file": training["train_file"],
        "train_file_sha256": training["train_file_sha256"],
        "eval_file": training["eval_file"],
        "eval_file_sha256": training["eval_file_sha256"],
        "completed_steps": metrics["completed_steps"],
        "train_rows": metrics["train_rows"],
        "eval_rows": metrics["eval_rows"],
        "train_loss": metrics["train_loss"],
        "best_eval_loss": best_eval_loss,
        "validation": validation,
        "hardware": metrics["cuda_device"],
        "max_memory_gb": metrics["max_memory_gb"],
        "production_writes": {"master": False, "dictionary": False, "sqlite": False},
        "formal_training_release": False,
        "full_stage4_release": False,
        "status": "SHADOW_TRAINING_PASS_NOT_PRODUCTION_APPROVED",
    }
    (shadow / "STAGE5_SHADOW_TRAINING_ACCEPTANCE_20260914.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = [
        "# Stage 5 Shadow Training Acceptance",
        "",
        "- Mode: offline shadow only",
        f"- Completed steps: {metrics['completed_steps']}",
        f"- Train / eval rows: {metrics['train_rows']} / {metrics['eval_rows']}",
        f"- Train loss: {metrics['train_loss']:.6f}",
        f"- Best eval loss: {best_eval_loss:.6f}" if isinstance(best_eval_loss, (int, float)) else "- Best eval loss: unavailable",
        f"- Validation JSON/schema rate: {validation['json_parse_rate']:.1%} / {validation['field_schema_rate']:.1%}",
        f"- Validation numeric preservation: {validation['numeric_preservation_rate']:.1%}",
        f"- Validation numeric hallucination: {validation['numeric_hallucination_rate']:.1%}",
        f"- Validation Spanish residual: {validation['spanish_residual_rate']:.1%}",
        "- Production writes: 0",
        "- Production approval: no",
        "",
        "This adapter is diagnostic only and must not replace the production adapter.",
    ]
    (shadow / "STAGE5_SHADOW_TRAINING_ACCEPTANCE_20260914.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    files = sorted(p for p in shadow.iterdir() if p.is_file() and p.name != "SHA256SUMS.txt")
    (shadow / "SHA256SUMS.txt").write_text("".join(f"{sha256(p)}  {p.name}\n" for p in files), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

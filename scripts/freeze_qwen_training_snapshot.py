"""Freeze a reproducible, read-only Qwen training snapshot.

The snapshot records the exact inputs and adapter metadata used by an offline
evaluation.  It deliberately does not copy model weights or mutate Master,
SQLite, or the production dictionary.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_tree(path: Path) -> str:
    """Hash a directory deterministically, including relative file names."""

    if not path.is_dir():
        raise FileNotFoundError(path)
    digest = hashlib.sha256()
    files = sorted(file for file in path.rglob("*") if file.is_file())
    for file in files:
        relative = file.relative_to(path).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(bytes.fromhex(sha256_file(file)))
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _git_state(repo: Path) -> dict[str, Any]:
    def run(*args: str) -> str | None:
        try:
            result = subprocess.run(
                ["git", "-C", str(repo), *args],
                check=True,
                capture_output=True,
                text=True,
            )
        except (OSError, subprocess.CalledProcessError):
            return None
        return result.stdout.strip()

    status = run("status", "--porcelain")
    return {
        "commit": run("rev-parse", "HEAD"),
        "worktree_clean": status == "",
        "status_line_count": len(status.splitlines()) if status is not None else None,
    }


def freeze_snapshot(
    *,
    repo: Path,
    model_path: Path,
    training_output: Path,
    train_file: Path,
    validation_file: Path,
    test_file: Path,
    training_script: Path,
    evaluation_script: Path,
    snapshot_dir: Path,
    snapshot_id: str,
    evaluation_policy: str,
) -> Path:
    """Write a snapshot manifest only after a complete training output exists."""

    adapter = training_output / "adapter"
    metrics_path = training_output / "smoke_metrics.json"
    training_manifest_path = training_output / "training_manifest.json"
    required = [
        model_path / "config.json",
        adapter / "adapter_config.json",
        metrics_path,
        training_manifest_path,
        train_file,
        validation_file,
        test_file,
        training_script,
        evaluation_script,
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise FileNotFoundError("SNAPSHOT_INCOMPLETE: " + ", ".join(missing))
    if not adapter.is_dir():
        raise FileNotFoundError(f"SNAPSHOT_INCOMPLETE: adapter directory missing: {adapter}")

    metrics = _read_json(metrics_path)
    training_manifest = _read_json(training_manifest_path)
    adapter_config = _read_json(adapter / "adapter_config.json")
    tokenizer_files = [
        path
        for path in model_path.iterdir()
        if path.is_file() and ("token" in path.name.lower() or path.name in {"special_tokens_map.json"})
    ]
    tokenizer_hash = None
    if tokenizer_files:
        tokenizer_hash_digest = hashlib.sha256()
        for path in sorted(tokenizer_files):
            name = path.name.encode("utf-8")
            tokenizer_hash_digest.update(len(name).to_bytes(8, "big"))
            tokenizer_hash_digest.update(name)
            tokenizer_hash_digest.update(bytes.fromhex(sha256_file(path)))
        tokenizer_hash = tokenizer_hash_digest.hexdigest()

    manifest = {
        "snapshot_id": snapshot_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "evaluation_policy": evaluation_policy,
        "git": _git_state(repo),
        "base_model": {
            "path": str(model_path.resolve()),
            "config_sha256": sha256_file(model_path / "config.json"),
            "tokenizer_files": sorted(path.name for path in tokenizer_files),
            "tokenizer_sha256": tokenizer_hash,
        },
        "data": {
            "train": {"path": str(train_file.resolve()), "sha256": sha256_file(train_file)},
            "validation": {"path": str(validation_file.resolve()), "sha256": sha256_file(validation_file)},
            "test": {"path": str(test_file.resolve()), "sha256": sha256_file(test_file)},
        },
        "training": {
            "script": str(training_script.resolve()),
            "script_sha256": sha256_file(training_script),
            "manifest": training_manifest,
            "metrics": metrics,
            "adapter_config": adapter_config,
            "adapter_config_sha256": sha256_file(adapter / "adapter_config.json"),
            "adapter_tree_sha256": sha256_tree(adapter),
            "adapter_path": str(adapter.resolve()),
            "best_checkpoint": metrics.get("best_model_checkpoint")
            or training_manifest.get("best_model_checkpoint"),
            "completed_steps": metrics.get("completed_steps")
            or training_manifest.get("completed_steps"),
        },
        "evaluation": {
            "script": str(evaluation_script.resolve()),
            "script_sha256": sha256_file(evaluation_script),
            "test_rows_expected": sum(1 for _ in test_file.open("r", encoding="utf-8")),
        },
        "scope": "offline evaluation only; no Master/SQLite/dictionary mutation",
    }
    destination = snapshot_dir / snapshot_id
    destination.mkdir(parents=True, exist_ok=False)
    manifest_path = destination / "snapshot_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--training-output", type=Path, required=True)
    parser.add_argument("--train-file", type=Path, required=True)
    parser.add_argument("--validation-file", type=Path, required=True)
    parser.add_argument("--test-file", type=Path, required=True)
    parser.add_argument("--training-script", type=Path, default=Path(__file__).with_name("train_qwen_qlora.py"))
    parser.add_argument("--evaluation-script", type=Path, default=Path(__file__).with_name("compare_qwen_baselines.py"))
    parser.add_argument("--snapshot-dir", type=Path, required=True)
    parser.add_argument("--snapshot-id", required=True)
    parser.add_argument("--evaluation-policy", default="stage4_safety_first_v3_2026-09-11")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    path = freeze_snapshot(
        repo=args.repo,
        model_path=args.model_path,
        training_output=args.training_output,
        train_file=args.train_file,
        validation_file=args.validation_file,
        test_file=args.test_file,
        training_script=args.training_script,
        evaluation_script=args.evaluation_script,
        snapshot_dir=args.snapshot_dir,
        snapshot_id=args.snapshot_id,
        evaluation_policy=args.evaluation_policy,
    )
    print(path)


if __name__ == "__main__":
    main()

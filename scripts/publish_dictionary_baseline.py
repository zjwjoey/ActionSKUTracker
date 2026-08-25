"""发布经过审计的本地字典基线到 Git 可追踪目录。"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from action_tracker.config import load_settings  # noqa: E402
from action_tracker.dictionary import (  # noqa: E402
    BRAND_DICTIONARY_HEADERS,
    CATEGORY_DICTIONARY_HEADERS,
    DICTIONARY_BASELINE_FILENAMES,
    MODEL_TRANSLATION_HEADERS,
    OVERRIDE_HEADERS,
    PRODUCT_DICTIONARY_HEADERS,
    SOURCE_DAMAGE_HEADERS,
    TERM_DICTIONARY_HEADERS,
    load_dictionary_csv,
    load_dictionary_rows,
)


FILES = {
    "product_dictionary.csv": (PRODUCT_DICTIONARY_HEADERS, ("sku",)),
    "brand_dictionary.csv": (BRAND_DICTIONARY_HEADERS, ("brand_id",)),
    "category_dictionary.csv": (CATEGORY_DICTIONARY_HEADERS, ("cat1_es", "cat2_es")),
    "term_dictionary.csv": (TERM_DICTIONARY_HEADERS, ("term_es", "term_type")),
    "manual_overrides.csv": (OVERRIDE_HEADERS, ("scope", "key", "field")),
    "model_translation_overrides.csv": (MODEL_TRANSLATION_HEADERS, ("sku",)),
    "source_damage_report.csv": (SOURCE_DAMAGE_HEADERS, ("sku",)),
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _validate(path: Path, headers: list[str], keys: tuple[str, ...]) -> int:
    if not path.exists() or path.stat().st_size == 0:
        raise FileNotFoundError(f"DICTIONARY_INPUT_MISSING: {path}")
    if keys == ("sku",) and headers == PRODUCT_DICTIONARY_HEADERS:
        return len(load_dictionary_csv(path, key_field="sku"))
    return len(load_dictionary_rows(path, headers=headers, key_fields=keys))


def _atomic_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    staged = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    shutil.copy2(source, staged)
    os.replace(staged, target)


def _require_audit_pass(report_path: Path) -> dict:
    """发布门禁：只有明确存在且无 FAIL 的审计报告才允许复制。"""
    if not report_path.exists():
        raise RuntimeError(f"DICTIONARY_AUDIT_REPORT_MISSING: {report_path}")
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    summary = payload.get("summary") or {}
    if summary.get("fail") != 0:
        raise RuntimeError(f"DICTIONARY_AUDIT_FAILED: {summary}")
    return payload


def _run_audit(root: Path, runtime: Path) -> tuple[Path, dict]:
    """重新运行审计，避免发布脚本误用过期或手工伪造的报告。"""
    command = [sys.executable, str(root / "scripts" / "audit_dictionary.py")]
    result = subprocess.run(command, cwd=root, capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        raise RuntimeError(
            "DICTIONARY_AUDIT_COMMAND_FAILED: "
            + (result.stdout or result.stderr or "no audit output").strip()
        )
    reports = sorted(runtime.glob("audit_report_*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not reports:
        raise RuntimeError(f"DICTIONARY_AUDIT_REPORT_MISSING: {runtime}")
    report = reports[0]
    return report, _require_audit_pass(report)


def main() -> int:
    cfg = load_settings()
    root = cfg["project_root"]
    runtime = cfg["paths"]["dictionary"]
    baseline = cfg["paths"]["dictionary_baseline"]
    if set(FILES) != set(DICTIONARY_BASELINE_FILENAMES):
        raise RuntimeError("DICTIONARY_BASELINE_SCHEMA_MISMATCH")
    audit_report, audit_payload = _run_audit(root, runtime)
    rows: dict[str, int] = {}
    hashes: dict[str, str] = {}
    for filename, (headers, keys) in FILES.items():
        source = runtime / filename
        rows[filename] = _validate(source, headers, keys)
        _atomic_copy(source, baseline / filename)
        hashes[filename] = _sha256(baseline / filename)
    manifest = {
        "schema_version": 1,
        "published_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_directory": "runtime/dictionary",
        "files": {
            filename: {"rows": rows[filename], "sha256": hashes[filename]}
            for filename in FILES
        },
    }
    manifest_path = baseline / "baseline_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    staged = manifest_path.with_name(f".{manifest_path.name}.{uuid.uuid4().hex}.tmp")
    staged.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(staged, manifest_path)
    print(json.dumps({
        "baseline": str(baseline), "files": rows, "manifest": str(manifest_path),
        "audit_report": str(audit_report), "audit_summary": audit_payload["summary"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

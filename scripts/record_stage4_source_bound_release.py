"""Promote the verified Stage-4 source-bound closure into current reality."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--current-reality", required=True)
    ap.add_argument("--closure", required=True)
    ap.add_argument("--recovery-state", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()
    current_path = Path(args.current_reality).resolve()
    closure_path = Path(args.closure).resolve()
    recovery_path = Path(args.recovery_state).resolve()
    output = Path(args.output).resolve()
    current = json.loads(current_path.read_text(encoding="utf-8"))
    closure = json.loads(closure_path.read_text(encoding="utf-8"))
    recovery = json.loads(recovery_path.read_text(encoding="utf-8"))
    if closure.get("FULL_STAGE4_RELEASE") is not True or recovery.get("gate_results", {}).get("FULL_STAGE4_RELEASE") is not True:
        raise SystemExit("STAGE4_RELEASE_GATE_NOT_PASS")
    document = dict(current)
    document.update({"audit_version": "stage4-source-bound-release-v1", "audit_date": datetime.now(timezone.utc).date().isoformat(), "head": subprocess.run(["git", "-C", str(Path(__file__).resolve().parents[1]), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()})
    document["stage4"] = {
        "acceptance": str(closure_path),
        "recovery_state": recovery.get("state"),
        "full_release": True,
        "release_mode": closure.get("release_mode"),
        "training_authorized": False,
        "production_writes_authorized": False,
        "closure_sha256": sha(closure_path),
        "p0_count": closure.get("findings", {}).get("confirmed_model_p0_count"),
        "p1_count": closure.get("findings", {}).get("confirmed_model_p1_count"),
    }
    document["verdict"] = "STAGE4_RELEASED_SOURCE_BOUND_RESOLVER_ONLY"
    document["safety"] = {"master_changed": False, "sqlite_changed": False, "dictionary_changed": False, "training_authorized": False, "production_writes_authorized": False}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "full_release": True, "p0": document["stage4"]["p0_count"], "p1": document["stage4"]["p1_count"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

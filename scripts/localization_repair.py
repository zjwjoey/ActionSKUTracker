"""CLI for the unified localization repair contract.

The default command is read-only preview.  ``apply`` requires an explicit
``--commit`` and owner identity; without it the command remains a dry-run.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from action_tracker.localization.repair_service import (
    apply_preview_to_database,
    build_preview,
    policy_hash,
    rollback_database,
    verify_database_apply,
)


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    preview = sub.add_parser("preview")
    preview.add_argument("--records", required=True)
    preview.add_argument("--manifest", required=True)
    preview.add_argument("--policy", required=True, help="policy file whose SHA-256 binds the preview")
    preview.add_argument("--output", required=True)
    apply = sub.add_parser("apply")
    apply.add_argument("--db", required=True)
    apply.add_argument("--preview", required=True)
    apply.add_argument("--run-id", required=True)
    apply.add_argument("--actor", required=True)
    apply.add_argument("--policy-hash", required=True)
    apply.add_argument("--commit", action="store_true")
    verify = sub.add_parser("verify")
    verify.add_argument("--db", required=True)
    verify.add_argument("--preview", required=True)
    rollback = sub.add_parser("rollback")
    rollback.add_argument("--db", required=True)
    rollback.add_argument("--run-id", required=True)
    rollback.add_argument("--actor", required=True)
    args = parser.parse_args()

    if args.command == "preview":
        expected = policy_hash(Path(args.policy).read_text(encoding="utf-8"))
        rows = build_preview(_jsonl(Path(args.records)), _jsonl(Path(args.manifest)), expected_policy_hash=expected)
        _write_json(Path(args.output), {"policy_hash": expected, "rows": rows, "master_writes": 0, "production_apply": False})
        print(json.dumps({"rows": len(rows), "output": args.output, "master_writes": 0, "production_apply": False}, ensure_ascii=False))
        return 0
    if args.command == "apply":
        preview_payload = json.loads(Path(args.preview).read_text(encoding="utf-8"))
        result = apply_preview_to_database(Path(args.db), preview_payload["rows"], actor=args.actor, run_id=args.run_id, expected_policy_hash=args.policy_hash, dry_run=not args.commit)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if args.command == "verify":
        preview_payload = json.loads(Path(args.preview).read_text(encoding="utf-8"))
        print(json.dumps(verify_database_apply(Path(args.db), preview_payload["rows"]), ensure_ascii=False))
        return 0
    print(json.dumps(rollback_database(Path(args.db), args.run_id, actor=args.actor), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

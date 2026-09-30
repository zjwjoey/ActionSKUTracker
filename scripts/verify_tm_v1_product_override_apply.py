"""Verify the committed TM V1 manual override transaction."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preview", type=Path, required=True)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--rollback", type=Path, required=True)
    parser.add_argument("--source-db", type=Path, required=True)
    parser.add_argument("--dictionary-dir", type=Path, required=True)
    parser.add_argument("--pre-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    preview = read_csv(args.preview)
    before = read_csv(args.before)
    after = read_csv(args.after)
    expected = [row for row in preview if row.get("apply_action") == "WOULD_APPLY"]
    no_change = [row for row in preview if row.get("apply_action") == "NO_CHANGE"]
    before_map = {(r.get("scope", ""), r.get("key", ""), r.get("field", "")): r for r in before}
    after_map = {(r.get("scope", ""), r.get("key", ""), r.get("field", "")): r for r in after}
    expected_keys = {(r.get("scope", ""), r.get("key", ""), r.get("field", "")) for r in expected}
    verified = [r for r in expected if after_map.get((r["scope"], r["key"], r["field"]), {}).get("value") == r.get("value")]
    no_change_ok = [r for r in no_change if before_map.get((r["scope"], r["key"], r["field"]), {}).get("value") == after_map.get((r["scope"], r["key"], r["field"]), {}).get("value")]
    changed_keys = {key for key in set(before_map) | set(after_map) if before_map.get(key) != after_map.get(key)}
    pre_manifest = json.loads(args.pre_manifest.read_text(encoding="utf-8"))
    non_manual_changes = {}
    for name, old_hash in pre_manifest.get("dictionary_dir_sha256", {}).items():
        if name == "manual_overrides.csv":
            continue
        path = args.dictionary_dir / name
        if path.exists() and sha256_file(path) != old_hash:
            non_manual_changes[name] = {"before": old_hash, "after": sha256_file(path)}
    failures = [
        {"key": [r["scope"], r["key"], r["field"]], "expected": r.get("value"), "actual": after_map.get((r["scope"], r["key"], r["field"]), {}).get("value")}
        for r in expected
        if after_map.get((r["scope"], r["key"], r["field"]), {}).get("value") != r.get("value")
    ]
    report = {
        "artifact_type": "ACTION_TM_V1_PRODUCT_OVERRIDE_POST_APPLY_VERIFICATION",
        "preview_sha256": sha256_file(args.preview),
        "before_snapshot_sha256": sha256_file(args.before),
        "after_manual_overrides_sha256": sha256_file(args.after),
        "rollback_backup_sha256": sha256_file(args.rollback),
        "source_db_sha256": sha256_file(args.source_db),
        "expected_would_apply": len(expected),
        "verified_would_apply": len(verified),
        "expected_no_change": len(no_change),
        "no_change_unchanged": len(no_change_ok),
        "changed_key_count": len(changed_keys),
        "unexpected_writes": len(changed_keys - expected_keys),
        "verification_failures": failures,
        "non_manual_dictionary_changes": non_manual_changes,
        "production_write": True,
        "status": "PASS" if len(verified) == len(expected) and len(no_change_ok) == len(no_change) and changed_keys == expected_keys and not failures and not non_manual_changes else "FAIL",
    }
    (out / "tm_v1_product_override_post_apply_verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out / "TM_V1_PRODUCT_OVERRIDE_POST_APPLY_VERIFICATION.md").write_text("\n".join([
        "# TM V1 Product Override Post-Apply Verification", "",
        f"- WOULD_APPLY expected/verified: {len(expected)}/{len(verified)}",
        f"- NO_CHANGE unchanged: {len(no_change_ok)}/{len(no_change)}",
        f"- changed key count: {len(changed_keys)}",
        f"- unexpected writes: {len(changed_keys - expected_keys)}",
        f"- non-manual dictionary changes: {len(non_manual_changes)}",
        f"- status: {report['status']}", "",
    ]) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

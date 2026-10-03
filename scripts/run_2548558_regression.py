"""Validate the real 2548558 whole-SKU empty-source regression."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-dir", required=True)
    args = parser.parse_args()
    batch = Path(args.batch_dir)
    with (batch / "master_localization_pending_fields.csv").open(encoding="utf-8-sig", newline="") as handle:
        rows = {(r["sku"], r["field_name"]): r for r in csv.DictReader(handle)}
    name = rows[("2548558", "name")]
    spec = rows[("2548558", "spec")]
    desc = rows[("2548558", "description")]
    checks = {
        "name_identity_preserved": name["candidate_zh"] == "万圣节小灯笼",
        "spec_source_empty_no_synthesis": not spec["source_es"] and not spec["candidate_zh"] and spec["field_status"] == "NO_SOURCE",
        "description_source_empty_not_required": not desc["source_es"] and not desc["candidate_zh"] and desc["field_status"] == "NO_SOURCE" and desc["ready_for_master"] == "NOT_REQUIRED",
    }
    result = {
        "sku": "2548558",
        "checks": checks,
        "pass": all(checks.values()),
        "production_writes": False,
        "master_modified": False,
    }
    (batch / "sku_2548558_regression.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

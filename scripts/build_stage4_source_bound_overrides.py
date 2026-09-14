"""Build the frozen, owner-approved Stage-4 P0/P1 override manifest."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


TARGETS = {
    ("3219003", "name"): "品牌与40 Denier必须保留",
    ("3220894", "description"): "禁止把详情页适用年龄跨字段带入描述",
    ("3217699", "description"): "描述只能保留来源描述中的5秒事实，不得带入详情容量",
    ("3223271", "description"): "保留H7、P21/5W、W5W、C5W、PY21W技术型号",
    ("3221705", "description"): "保留来源描述中的250克",
    ("3221795", "description"): "保留来源描述中的14个灯泡",
    ("3201998", "description"): "保留来源描述中的每卷64张",
    ("3213267", "description"): "保留来源描述中的27件",
    ("3009588", "description"): "保留来源描述中的最多5升",
    ("3209605", "name"): "felpudo应译为门垫，不是拖鞋垫",
    ("3217469", "description"): "保留12+12并呈现总数24",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--predictions", required=True)
    ap.add_argument("--test-file", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()
    rows = [json.loads(line) for line in Path(args.predictions).read_text(encoding="utf-8").splitlines() if line.strip()]
    test_rows = [json.loads(line) for line in Path(args.test_file).read_text(encoding="utf-8").splitlines() if line.strip()]
    source_hash_by_sku = {str(row.get("metadata", {}).get("sku")): str(row.get("metadata", {}).get("source_hash", "")) for row in test_rows}
    result = []
    for row in rows:
        sku = str(row["sku"])
        for (target_sku, field), reason in TARGETS.items():
            if sku != target_sku:
                continue
            result.append({
                "sku": sku,
                "field": field,
                "source_hash": source_hash_by_sku.get(sku, ""),
                "source_value": str((row.get("source") or {}).get(field, "")),
                "reviewed_value": str((row.get("expected") or {}).get(field, "")),
                "reason": reason,
                "owner_approved": True,
                "approval_scope": "frozen_485_field_level_remediation",
            })
    keys = {(item["sku"], item["field"]) for item in result}
    if keys != set(TARGETS):
        raise SystemExit(f"TARGETS_NOT_FOUND: {sorted(set(TARGETS) - keys)}")
    if any(not item["source_hash"] for item in result):
        raise SystemExit("SOURCE_HASH_MISSING")
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"override_count": len(result), "output": str(out)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

import csv
import hashlib
import importlib.util
import json
from pathlib import Path

from action_tracker.translation.detail_rule_candidates import build_detail_rule_candidates


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/audit_detail_rule_review_package.py"
SPEC = importlib.util.spec_from_file_location("detail_package_audit", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_detail_review_package_audit_passes_integrity_but_not_semantic_review(tmp_path: Path):
    queue = tmp_path / "DETAIL_RULE_REVIEW.csv"
    template = tmp_path / "DETAIL_RULE_DECISIONS_TEMPLATE.csv"
    queue_manifest = tmp_path / "DETAIL_RULE_REVIEW.csv.manifest.json"
    template_manifest = tmp_path / "DETAIL_RULE_DECISIONS_TEMPLATE.csv.manifest.json"
    _write_csv(queue, [{
        "review_id": "detail-1", "review_kind": "SOURCE_VALUE",
        "review_reasons": "VALUE_RULE_UNCOVERED", "candidate_is_approved": "False",
    }])
    queue_manifest.write_text(json.dumps({
        "row_count": 1, "sha256": _sha(queue), "source_sha256": "s",
        "target_sha256": "t", "detail_rules_sha256": "r",
    }), encoding="utf-8")
    _write_csv(template, [{
        "review_id": "detail-1", "decision": "", "approved_target": "",
        "reviewer": "", "reviewed_at": "", "evidence_status": "",
        "source_sha256": "s", "target_sha256": "t", "detail_rules_sha256": "r",
    }])
    template_manifest.write_text(json.dumps({
        "row_count": 1, "queue_sha256": _sha(queue),
        "queue_manifest_sha256": _sha(queue_manifest),
    }), encoding="utf-8")
    report = AUDIT.audit_package(queue, queue_manifest, template, template_manifest)
    assert report["package_valid"] is True
    assert report["semantic_review_state"] == "NOT_STARTED"
    assert report["status"] == "READY_FOR_DESCRIPTION_DETAILS_HUMAN_AUDIT"
    assert report["facts"]["master_writes"] == 0


def test_detail_review_package_audit_rejects_hash_drift(tmp_path: Path):
    queue = tmp_path / "q.csv"
    template = tmp_path / "t.csv"
    queue_manifest = tmp_path / "q.json"
    template_manifest = tmp_path / "t.json"
    _write_csv(queue, [{"review_id": "detail-1", "review_kind": "SOURCE_KEY", "review_reasons": "", "candidate_is_approved": "False"}])
    queue_manifest.write_text(json.dumps({"row_count": 1, "sha256": _sha(queue), "source_sha256": "s", "target_sha256": "t", "detail_rules_sha256": "r"}), encoding="utf-8")
    _write_csv(template, [{"review_id": "detail-1", "decision": "", "approved_target": "", "reviewer": "", "reviewed_at": "", "evidence_status": "", "source_sha256": "stale", "target_sha256": "t", "detail_rules_sha256": "r"}])
    template_manifest.write_text(json.dumps({"row_count": 1, "queue_sha256": _sha(queue), "queue_manifest_sha256": _sha(queue_manifest)}), encoding="utf-8")
    report = AUDIT.audit_package(queue, queue_manifest, template, template_manifest)
    assert report["package_valid"] is False
    assert report["checks"]["template_source_hashes_match"] is False


def test_detail_review_package_audit_supports_long_cells_and_aggregate_candidates(tmp_path: Path):
    queue = tmp_path / "DETAIL_RULE_REVIEW.csv"
    template = tmp_path / "DETAIL_RULE_DECISIONS_TEMPLATE.csv"
    queue_manifest = tmp_path / "DETAIL_RULE_REVIEW.csv.manifest.json"
    template_manifest = tmp_path / "DETAIL_RULE_DECISIONS_TEMPLATE.csv.manifest.json"
    context = '[{"name_es":"Producto", "cat1_es":"Hogar"}]'
    rows = []
    for sku, review_id in (("1001", "detail-1"), ("1002", "detail-2")):
        rows.append({
            "review_id": review_id, "review_kind": "SOURCE_VALUE",
            "source_key_normalized": "tipo de bateria", "source_value_normalized": "alcalina",
            "review_reasons": "VALUE_RULE_UNCOVERED", "occurrences": "1",
            "rule_covered_occurrences": "0", "uncovered_occurrences": "1",
            "sku_examples": json.dumps([sku]),
            "observed_target_candidates": '[{"target_value":"碱性电池"}]',
            "context_scope": context, "candidate_is_approved": "False",
            "source_details": "x" * 200_000,
        })
    _write_csv(queue, rows)
    queue_manifest.write_text(json.dumps({
        "row_count": 2, "sha256": _sha(queue), "source_sha256": "s",
        "target_sha256": "t", "detail_rules_sha256": "r",
    }), encoding="utf-8")
    candidate = build_detail_rule_candidates(rows, source_sha256="s", target_sha256="t", rules_sha256="r")[0]
    _write_csv(template, [{
        "review_id": candidate["candidate_id"], "decision": "", "approved_target": "",
        "reviewer": "", "reviewed_at": "", "evidence_status": "",
        "source_sha256": "s", "target_sha256": "t", "detail_rules_sha256": "r",
    }])
    template_manifest.write_text(json.dumps({
        "row_count": 1, "queue_sha256": _sha(queue),
        "queue_manifest_sha256": _sha(queue_manifest),
    }), encoding="utf-8")
    report = AUDIT.audit_package(queue, queue_manifest, template, template_manifest)
    assert report["package_valid"] is True
    assert report["semantic_review_state"] == "NOT_STARTED"
    assert report["counts"]["candidate_rows"] == 1
    assert report["review_scope"] == "candidate_id"
    assert report["template_uses_aggregate_candidates"] is True
    assert report["checks"]["candidate_scope_context_bound"] is True

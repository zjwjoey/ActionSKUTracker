import csv
import hashlib
import importlib.util
import json
from pathlib import Path


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

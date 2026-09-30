"""Sidecar QA log for export-only localization and release findings."""
from __future__ import annotations

import csv
import hashlib
import io
import json
from collections import Counter
from typing import Any, Mapping


QA_LOG_COLUMNS = [
    "qa_id", "occurrence", "sku", "language", "field", "issue_code", "disposition",
    "review_only", "evidence_json",
]

_FALLBACK_FIELDS = {
    "中文品名待审核": "name",
    "中文分类1待审核": "cat1",
    "中文分类2待审核": "cat2",
    "中文规格待审核": "spec",
    "中文单价待审核": "unit_price",
    "中文描述待审核": "description",
    "中文产品详情待审核": "details",
    "中文品名无来源": "name",
    "中文分类1无来源": "cat1",
    "中文分类2无来源": "cat2",
    "中文规格无来源": "spec",
    "中文描述无来源": "description",
    "中文产品详情无来源": "details",
}


def build_export_qa_log(
    release_gates: Mapping[str, Mapping[str, Any]],
    fallback_counts: Mapping[str, int] | None = None,
) -> list[dict[str, str]]:
    """Build a stable per-SKU QA log without mixing process notes into remarks."""
    findings: list[dict[str, str]] = []
    for language, gate in sorted(release_gates.items()):
        for issue in gate.get("issues") or ():
            if not isinstance(issue, Mapping):
                continue
            review_only = bool(issue.get("review_only"))
            evidence = json.dumps(dict(issue), ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
            findings.append({
                "sku": str(issue.get("sku") or ""), "language": language,
                "field": str(issue.get("field") or ""),
                "issue_code": str(issue.get("code") or "UNCLASSIFIED"),
                "disposition": "REVIEW" if review_only else "BLOCKING",
                "review_only": str(review_only).lower(), "evidence_json": evidence,
            })
    for label, count in sorted((fallback_counts or {}).items()):
        if int(count) <= 0:
            continue
        findings.append({
            "sku": "", "language": "zh", "field": _FALLBACK_FIELDS.get(label, "unclassified"),
            "issue_code": "LOCALIZATION_FALLBACK_SUMMARY", "disposition": "REVIEW",
            "review_only": "true",
            "evidence_json": json.dumps({"fallback": label, "count": int(count)}, ensure_ascii=False, sort_keys=True),
        })

    findings.sort(key=lambda row: (
        row["language"], row["sku"], row["field"], row["issue_code"],
        row["disposition"], row["review_only"], row["evidence_json"],
    ))
    occurrences: Counter[tuple[str, ...]] = Counter()
    rows: list[dict[str, str]] = []
    for finding in findings:
        key = tuple(finding[column] for column in (
            "sku", "language", "field", "issue_code", "disposition", "review_only", "evidence_json",
        ))
        occurrences[key] += 1
        rows.append(_qa_row(
            sku=finding["sku"], language=finding["language"], field=finding["field"],
            issue_code=finding["issue_code"], disposition=finding["disposition"],
            review_only=finding["review_only"] == "true", evidence_json=finding["evidence_json"],
            occurrence=occurrences[key],
        ))
    return rows


def _qa_row(
    *, sku: str, language: str, field: str, issue_code: str,
    disposition: str, review_only: bool, evidence_json: str, occurrence: int,
) -> dict[str, str]:
    identity = {
        "occurrence": occurrence, "sku": sku, "language": language, "field": field,
        "issue_code": issue_code, "disposition": disposition,
        "review_only": review_only, "evidence_json": evidence_json,
    }
    qa_id = hashlib.sha256(
        ("EXPORT_QA_LOG_V1|" + json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))).encode("utf-8")
    ).hexdigest()
    return {"qa_id": qa_id, **{key: str(value) for key, value in identity.items()}}


def render_export_qa_log(rows: list[dict[str, str]]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=QA_LOG_COLUMNS, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return b"\xef\xbb\xbf" + stream.getvalue().encode("utf-8")

"""Data Quality & Integrity V1.

The package is deliberately read-first: audits and quality gates inspect a
SQLite PRIMARY (or a caller supplied fixture), while repair candidates are
kept separate from formal facts until an explicitly approved correction path
is used.
"""

from .contracts import (
    COLLECTION_STATES,
    ISSUE_SEVERITIES,
    ISSUE_STATUSES,
    ISSUE_TYPES,
    IssueScope,
    DataQualityIssue,
    issue_id,
)

__all__ = [
    "COLLECTION_STATES",
    "ISSUE_SEVERITIES",
    "ISSUE_STATUSES",
    "ISSUE_TYPES",
    "IssueScope",
    "DataQualityIssue",
    "issue_id",
]

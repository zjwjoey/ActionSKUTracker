import sqlite3
from pathlib import Path

import pytest

from action_tracker.database.category_backlog import (
    CategoryBacklogError,
    decide_category_backlog,
    enqueue_category_missing,
)
from action_tracker.database.schema import migrate_v2


def _db(tmp_path: Path) -> Path:
    path = tmp_path / "db.sqlite"
    migrate_v2(path)
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO products(canonical_id,official_sku,status) VALUES('c1','1001','CURRENT')")
    return path


def test_category_backlog_requires_official_evidence_to_close(tmp_path):
    path = _db(tmp_path)
    enqueue_category_missing(
        path, queue_id="q1", official_sku="1001", cat1_es="Cuidado personal", cat2_es="Maquillaje",
        suggested_cat2_zh="彩妆", source_hash="h1",
    )
    with pytest.raises(CategoryBacklogError, match="CATEGORY_OFFICIAL_EVIDENCE_REQUIRED"):
        decide_category_backlog(path, queue_id="q1", decision="APPROVED", value="彩妆", actor="reviewer", evidence_url="")
    assert decide_category_backlog(
        path, queue_id="q1", decision="APPROVED", value="彩妆", actor="reviewer",
        evidence_url="https://www.action.com/es-es/p/1001/",
    ) == "APPROVED"
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT status,decision_value FROM category_backlog WHERE queue_id='q1'").fetchone() == ("APPROVED", "彩妆")


def test_category_backlog_rejects_approval_without_value(tmp_path):
    path = _db(tmp_path)
    enqueue_category_missing(
        path, queue_id="q1", official_sku="1001", cat1_es="Hogar", cat2_es="Muebles", source_hash="h1",
    )
    with pytest.raises(CategoryBacklogError, match="CATEGORY_APPROVAL_VALUE_MISSING"):
        decide_category_backlog(path, queue_id="q1", decision="APPROVED", actor="reviewer", evidence_url="https://example.test/1001")

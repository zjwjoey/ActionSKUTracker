"""CI_SAFE: generated SQLite facts only; no production, network or model calls."""
import copy
import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from action_tracker.database.production import CommitBundle, ProductionWriter, ProductionDatabaseError
from action_tracker.data_quality.historical.variance import (
    CONTRACT, EFFECTS, FactVarianceError, typed_hash, source_descriptor,
    seal_ack, build_ack_bundle, match_ack,
)


@pytest.fixture(params=["legacy", "active"])
def fixture(request, tmp_path):
    path = tmp_path / "generated.sqlite3"
    writer = ProductionWriter(path, role="PRIMARY")
    if request.param == "active":
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE product_fact_versions(fact_id TEXT PRIMARY KEY, official_sku TEXT, run_id TEXT, raw_fact_json TEXT, normalized_fact_json TEXT, raw_fact_hash TEXT, normalized_fact_hash TEXT, raw_fact_available INTEGER, normalization_version TEXT, created_at TEXT)")
    head = writer.commit(CommitBundle(run_id="seed", observation_date="2026-10-01", qa_state="PASS",
        current_products=({"sku": "123", "canonical_id": "ACT123", "status": "HISTORICAL"},),
        localization_updates=({"sku": "123", "language": "es", "spec": "500 ml"},),
        source_fact_versions=tuple({"sku": "123", "run_id": "source-" + str(i),
            "source_name": "OFFICIAL_ARCHIVED_V1", "observed_at": "2026-09-01",
            "facts": {"spec": {"raw": value, "normalized": value}}} for i, value in enumerate(("500 ml", "450 ml")))))
    table = "product_fact_versions" if request.param == "active" else "source_fact_versions"
    with sqlite3.connect(path) as db:
        ids = [row[0] for row in db.execute(f"SELECT fact_id FROM {table} ORDER BY run_id")]
        sources = [source_descriptor(db, sku="123", fact_id=key, field="spec") for key in ids]
    now = datetime.now(timezone.utc)
    ack = seal_ack({"contract": CONTRACT, "hash_version": "TYPED_JSON_SHA256_V1", "sku": "123",
        "canonical_id": "ACT123", "field": "spec", "action": "ACK_SOURCE_VARIANCE",
        "policy": "KEEP_BOTH", "status": "CONFIRMED", "effects": EFFECTS,
        "base_commit_id": head, "current_value_hash": typed_hash("500 ml"), "sources": sources,
        "review": {"reviewed_by": "human:fixture-reviewer", "reviewed_at": (now - timedelta(minutes=1)).isoformat(),
            "expires_at": (now + timedelta(days=1)).isoformat(), "reason": "Two archived official versions differ",
            "evidence_refs": ["synthetic-fixture-review-only"]}})
    return path, writer, head, ack, table


def _bundle(fixture, ack=None, **kw):
    path, _, head, original, _ = fixture
    return build_ack_bundle(path, ack or original, run_id="ack", run_date="2026-10-30", base_commit_id=head, **kw)


def _business(path):
    with sqlite3.connect(path) as db:
        return {name: db.execute(f"SELECT * FROM {name} ORDER BY 1,2").fetchall() for name in (
            "products", "product_localizations", "product_observations", "price_history", "event_history",
            "localization_fields", "localization_field_provenance", "translations")}


def test_ack_preserves_both_sources_all_business_facts_and_resume(fixture):
    path, writer, _, ack, _ = fixture
    before = _business(path)
    bundle = _bundle(fixture)
    head = writer.commit(bundle)
    assert writer.commit(bundle) == head
    assert _business(path) == before
    with sqlite3.connect(path) as db:
        assert match_ack(db, ack_id=ack["ack_id"], sku="123", field="spec", sources=ack["sources"], current_value="500 ml") == ack
        assert db.execute("SELECT COUNT(*) FROM run_evidence WHERE run_id='ack'").fetchone()[0] == 1
        payload = json.loads(db.execute("SELECT evidence_json FROM run_evidence WHERE run_id='ack'").fetchone()[0])
        assert payload["fact_variance_ack"]["effects"] == EFFECTS
        assert ack["sources"][0]["observed_precision"] == ("UNKNOWN" if fixture[4] == "product_fact_versions" else "DATE")


def _changed_ack(fixture, change):
    ack = copy.deepcopy(fixture[3])
    if change == "sku": ack["sku"] = "456"
    elif change == "field": ack["field"] = "details"
    elif change == "target": ack["current_value_hash"] = typed_hash("450 ml")
    elif change == "version": ack["sources"][0]["source_version_id"] = "missing"
    elif change == "raw": ack["sources"][0]["raw_value"] = "1 ml"
    elif change == "normalized": ack["sources"][0]["normalized_hash"] = typed_hash("1 ml")
    elif change == "asset": ack["sources"][0]["asset_sha256"] = "0" * 64
    elif change == "normalization": ack["sources"][0]["normalization_version"] = "NEW"
    elif change == "no_actor": ack["review"]["reviewed_by"] = ""
    elif change == "service": ack["review"]["reviewed_by"] = "service:untrusted"
    elif change == "expired": ack["review"]["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    elif change == "future": ack["review"]["reviewed_at"] = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    elif change == "naive": ack["review"]["reviewed_at"] = "2026-10-01T00:00:00"
    elif change == "grant": ack["effects"]["does_not_waive_qa"] = False
    elif change == "select_winner": ack["policy"] = "PREFER_A"
    elif change == "same_source": ack["sources"][1] = copy.deepcopy(ack["sources"][0])
    elif change == "bad_digest": ack["ack_id"] = "0" * 64
    if change != "bad_digest": ack = seal_ack(ack)
    return ack


def test_exact_ack_rejects_changed_or_unauthorized_payload(fixture):
    # Reuse one immutable generated DB for payload-only negative cases; every
    # assertion still executes the full read-only source/review validator.
    for change in ("sku", "field", "target", "version", "raw", "normalized", "asset", "normalization", "no_actor", "service", "expired", "future", "naive", "grant", "select_winner", "same_source", "bad_digest"):
        with pytest.raises(ValueError):
            _bundle(fixture, _changed_ack(fixture, change))


def test_typed_value_digest_preserves_legacy_falsey_distinctions():
    assert len({typed_hash(v) for v in (None, "", 0, False, "0", 0.0)}) == 6
    with pytest.raises(ValueError): typed_hash(float("nan"))


def test_committed_ack_never_waives_other_field_or_new_source(fixture):
    path, writer, _, ack, _ = fixture
    writer.commit(_bundle(fixture))
    altered = copy.deepcopy(ack["sources"])
    altered[1]["source_version_id"] = "third-version"
    with sqlite3.connect(path) as db:
        for sku, field, sources, current in (("456", "spec", ack["sources"], "500 ml"),
                ("123", "details", ack["sources"], "500 ml"), ("123", "spec", altered, "500 ml"),
                ("123", "spec", ack["sources"], "450 ml")):
            assert match_ack(db, ack_id=ack["ack_id"], sku=sku, field=field, sources=sources, current_value=current) is None


def test_revocation_append_only_and_cannot_reconfirm_same_ack(fixture):
    path, writer, _, ack, _ = fixture
    head = writer.commit(_bundle(fixture))
    revoke = {"contract": CONTRACT, "ack_id": ack["ack_id"], "reviewed_by": "human:fixture-reviewer",
        "revoked_at": datetime.now(timezone.utc).isoformat(), "reason": "Review withdrawn", "evidence_refs": ["fixture-review"]}
    bundle = build_ack_bundle(path, revoke, run_id="revoke", run_date="2026-10-30", base_commit_id=head, revoke=True)
    new_head = writer.commit(bundle)
    assert writer.commit(bundle) == new_head
    with sqlite3.connect(path) as db:
        assert match_ack(db, ack_id=ack["ack_id"], sku="123", field="spec", sources=ack["sources"], current_value="500 ml") is None
        assert db.execute("SELECT COUNT(*) FROM run_evidence WHERE run_id IN ('ack','revoke')").fetchone()[0] == 2
    with pytest.raises(FactVarianceError, match="REVOKED"):
        build_ack_bundle(path, ack, run_id="ack-again", run_date="2026-10-30", base_commit_id=new_head)


def test_transaction_rechecks_source_target_and_no_business_write_scope(fixture):
    path, writer, head, ack, _ = fixture
    bundle = _bundle(fixture)
    for key in ("current_products", "localization_updates", "observations", "source_fact_versions", "price_events", "event_events", "lifecycle_updates", "review_rows"):
        with pytest.raises(FactVarianceError, match="METADATA_ONLY"):
            writer.commit(replace(bundle, **{key: ({"sku": "123"},)}))
    with pytest.raises(ProductionDatabaseError, match="QA_NOT_PASS"):
        writer.commit(replace(bundle, qa_state="FAIL"))
    with sqlite3.connect(path) as db:
        db.execute("UPDATE product_localizations SET spec='later value' WHERE official_sku='123' AND language='es'")
    with pytest.raises(FactVarianceError, match="TARGET_CHANGED"):
        writer.commit(bundle)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM run_evidence WHERE run_id='ack'").fetchone()[0] == 0


def test_expiry_and_mutated_canonical_source_invalidate_recorded_ack(fixture):
    path, writer, _, ack, table = fixture
    writer.commit(_bundle(fixture))
    with sqlite3.connect(path) as db:
        assert match_ack(db, ack_id=ack["ack_id"], sku="123", field="spec", sources=ack["sources"], current_value="500 ml", now=datetime.now(timezone.utc) + timedelta(days=2)) is None
        db.execute(f"UPDATE {table} SET created_at='changed' WHERE fact_id=?", (ack["sources"][0]["source_version_id"],))
        assert match_ack(db, ack_id=ack["ack_id"], sku="123", field="spec", sources=ack["sources"], current_value="500 ml") is None


def test_stale_execution_head_rejected_even_if_review_pair_unchanged(fixture):
    path, writer, head, ack, _ = fixture
    bundle = _bundle(fixture)
    writer.commit(CommitBundle(run_id="other", observation_date="2026-10-30", qa_state="PASS", base_commit_id=head))
    with pytest.raises(ProductionDatabaseError, match="BASELINE_CHANGED"):
        writer.commit(bundle)


def test_third_source_version_invalidates_only_its_exact_field(fixture):
    path, writer, _, ack, _ = fixture
    head = writer.commit(_bundle(fixture))
    # Another field on the same SKU must not invalidate this spec ACK.
    head = writer.commit(CommitBundle(run_id="other-field", observation_date="2026-10-30", qa_state="PASS", base_commit_id=head,
        source_fact_versions=({"sku": "123", "run_id": "other-field", "facts": {"details": {"raw": "Algodón", "normalized": "Algodón"}}},)))
    with sqlite3.connect(path) as db:
        assert match_ack(db, ack_id=ack["ack_id"], sku="123", field="spec", sources=ack["sources"], current_value="500 ml")
    writer.commit(CommitBundle(run_id="third-source", observation_date="2026-10-30", qa_state="PASS", base_commit_id=head,
        source_fact_versions=({"sku": "123", "run_id": "third-source", "facts": {"spec": {"raw": "400 ml", "normalized": "400 ml"}}},)))
    with sqlite3.connect(path) as db:
        assert match_ack(db, ack_id=ack["ack_id"], sku="123", field="spec", sources=ack["sources"], current_value="500 ml") is None

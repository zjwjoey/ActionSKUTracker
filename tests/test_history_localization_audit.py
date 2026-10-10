import sqlite3

import pytest

from action_tracker.localization.history_audit import (
    add_record, choose_evidence, verified_backup, verified_archive_identity, write_detail_reports,
)


def evidence(text, rank=1, reference="snapshot", date="2026-01-01"):
    return {"text": text, "rank": rank, "reference": reference, "observed_at": date}


def test_unavailable_is_not_empty():
    assert choose_evidence([])["status"] == "SOURCE_UNAVAILABLE"
    assert choose_evidence([evidence("")])["status"] == "EXPLICIT_EMPTY_EVIDENCE"


def test_conflicting_versions_require_review():
    result = choose_evidence([evidence("100 g"), evidence("200 g", date="2026-02-01")])
    assert result["status"] == "SOURCE_VERSION_CONFLICT"
    assert result["selected"] is None


def test_snapshot_precedes_master_but_keeps_versions():
    result = choose_evidence([evidence("100 g"), evidence("200 g", rank=3)])
    assert result["selected"]["text"] == "100 g"
    assert len(result["versions"]) == 2


def test_chinese_in_spanish_source_cannot_pass():
    assert choose_evidence([evidence("苍蝇拍")])["status"] == "SOURCE_LANGUAGE_REVIEW_REQUIRED"
    result = choose_evidence([evidence("苍蝇拍"), evidence("Matamoscas", rank=2)])
    assert result["selected"]["text"] == "Matamoscas"
    assert len(result["versions"]) == 2


@pytest.mark.parametrize("text", ["Añadir a tus favoritos", "Leer más", "<button>Descripción</button>"])
def test_transport_residue_is_not_trustworthy_source(text):
    # SKU 2546793, spec batch 09: snapshot button text was incorrectly
    # offered as a product specification. No target is asserted or guessed.
    result = choose_evidence([evidence(text)])
    assert result["selected"] is None
    assert result["status"] == "SOURCE_LANGUAGE_REVIEW_REQUIRED"
    assert result["versions"][0]["text"] == text


def test_clean_historical_source_can_replace_button_evidence():
    result = choose_evidence([evidence("Añadir a tus favoritos"), evidence("100 g", rank=3)])
    assert result["selected"]["text"] == "100 g"
    assert len(result["versions"]) == 2


def test_missing_fields_do_not_create_empty_units():
    from collections import defaultdict
    index = defaultdict(list)
    add_record(index, {"sku": "123", "name_es": "Producto"},
               skus={"123"}, rank=3, reference="master")
    assert list(index) == [("123", "name")]
    assert choose_evidence(index[("123", "details")])["status"] == "SOURCE_UNAVAILABLE"


def test_current_and_unknown_skus_excluded():
    from collections import defaultdict
    index = defaultdict(list)
    add_record(index, {"sku": "999", "name_es": "Producto"},
               skus={"123"}, rank=1, reference="snapshot")
    assert not index


@pytest.mark.parametrize("url,status,expected", [
    ("https://www.action.com/es-es/p/123/product/", "历史官方资料已确认", True),
    ("https://www.action.com/es-es/p/1234/product/", "历史官方资料已确认", False),
    ("https://www.action.com.evil/es-es/p/123/", "历史官方资料已确认", False),
    ("https://www.action.com/es-es/p/123/", "待人工核验", False),
])
def test_archive_identity_is_not_guessed(url, status, expected):
    assert verified_archive_identity({"编号": "123", "商品链接": url, "核验结论": status}, {"123"}) is expected


def test_pilot_repeat_is_deterministic_and_does_not_approve(tmp_path):
    rows = [{"sku": str(sku), "field": field, "action": "RESTORE_SOURCE", "freshness": "NO_LOCALIZATION",
             "qa_findings": [], "recoverable_chinese": "", "chinese": ""}
            for sku in range(120) for field in ("name", "spec")]
    first = write_detail_reports(rows, tmp_path)
    content = (tmp_path / "pilot_100.jsonl").read_bytes()
    second = write_detail_reports(rows, tmp_path)
    assert first == second
    assert first["pilot_100"] == {"skus": 100, "fields": 200}
    assert content == (tmp_path / "pilot_100.jsonl").read_bytes()


def test_backup_restores_committed_wal_and_refuses_overwrite(tmp_path):
    path = tmp_path / "primary.db"
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE facts(sku TEXT PRIMARY KEY, source TEXT)")
        db.execute("INSERT INTO facts VALUES('123', 'hecho')")
        db.commit()
        result = verified_backup(path, tmp_path / "backup.db")
    assert result["logical_parity"]
    assert result["integrity"] == "ok"
    assert result["foreign_key_violations"] == []
    with pytest.raises(ValueError, match="BACKUP_ALREADY_EXISTS"):
        verified_backup(path, tmp_path / "backup.db")


def test_source_recovery_bundle_preserves_existing_fields_and_missing_evidence(tmp_path):
    from action_tracker.database.production import CommitBundle, ProductionWriter
    from action_tracker.localization.history_recovery import build_historical_source_bundle
    path=tmp_path/"primary.db"
    writer=ProductionWriter(path,role="PRIMARY")
    head=writer.commit(CommitBundle(run_id="seed",observation_date="2026-10-09",qa_state="PASS",
        current_products=({"sku":"123","status":"HISTORICAL","name_es":"Original"},),
        localization_updates=({"sku":"123","language":"es","name":"Original"},)))
    artifact=tmp_path/"source.csv"; artifact.write_text("2 unidades",encoding="utf-8")
    import hashlib
    selected={"text":"2 unidades","reference":str(artifact),"file_hash":hashlib.sha256(artifact.read_bytes()).hexdigest()}
    rows=[{"sku":"123","field":"spec","evidence":{"status":"EVIDENCE_AVAILABLE","selected":selected}},
          {"sku":"123","field":"description","evidence":{"status":"SOURCE_UNAVAILABLE","selected":None}}]
    bundle,report=build_historical_source_bundle(path,rows,run_id="restore",base_commit_id=head,run_date="2026-10-09")
    assert report["recovered_fields"]==1
    assert bundle.localization_updates[0]["name"]=="Original"
    assert bundle.localization_updates[0]["description"] is None
    assert not bundle.current_products and not bundle.observations and not bundle.event_events and not bundle.price_events
    restored=writer.commit(bundle)
    assert writer.commit(bundle)==restored


def test_normalization_correction_only_reprojects_recorded_recovery(tmp_path):
    from action_tracker.database.production import CommitBundle,ProductionWriter
    from action_tracker.localization.history_recovery import build_historical_normalization_correction_bundle
    path=tmp_path/"primary.db";writer=ProductionWriter(path,role="PRIMARY")
    raw="Material estructura: Plástico; Número del artículo: 123"
    artifact=tmp_path/"source.txt";artifact.write_text(raw,encoding="utf-8")
    import hashlib
    source={"reference":str(artifact),"file_hash":hashlib.sha256(artifact.read_bytes()).hexdigest()}
    bad="Material: estructura: Plástico; Número del artículo: 123"
    head=writer.commit(CommitBundle(run_id="restore",observation_date="2026-10-09",qa_state="PASS",
        current_products=({"sku":"123","status":"HISTORICAL","name_es":"Original"},),
        localization_updates=({"sku":"123","language":"es","name":"Original","details":bad},),
        run_record={"operation":"HISTORICAL_SOURCE_RECOVERY","field_evidence":[{"sku":"123","field":"details","status":"VERIFIED","raw":raw,"normalized":bad,"source":source}]}))
    bundle,report=build_historical_normalization_correction_bundle(path,run_id="fix",base_commit_id=head,run_date="2026-10-09")
    assert report["corrected_fields"]==1
    assert bundle.localization_updates[0]["details"]==raw
    assert not bundle.current_products and not bundle.event_events and not bundle.price_events
    writer.commit(bundle)
    _,report=build_historical_normalization_correction_bundle(path,run_id="retry",base_commit_id=None,run_date="2026-10-09")
    assert report["corrected_fields"]==0
    with sqlite3.connect(path) as db:db.execute("UPDATE product_localizations SET details='Later fact' WHERE language='es'")
    with pytest.raises(ValueError,match="SOURCE_CHANGED_REVIEW"):
        build_historical_normalization_correction_bundle(path,run_id="late",base_commit_id=None,run_date="2026-10-09")

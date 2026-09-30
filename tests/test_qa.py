"""规范 §60 测试 18-19：QA Gate。"""
import pytest

from action_tracker.qa.validator import run_qa


def _cfg():
    return {
        "qa": {
            "max_active_drop_percent": 15,
            "max_active_increase_percent": 20,
            "max_new_sku_percent": 5,
            "max_missing_percent": 5,
            "max_sitemap_listing_gap_percent": 5,
            "price_min": 0.01,
            "price_max": 1000.0,
            "max_anomaly_count": 20,
        }
    }


def _products(n, **kw):
    return [{"sku": str(i), "canonical_id": f"ACT{i:07d}", "product_url": "u", "current_price": 1.0, "cat1_es": "c", "raw_tags": ""} for i in range(n)]


# ---- 测试 18：大量 SKU 消失 -> QA FAIL ----
def test_t18_mass_drop_qa_fail():
    qa = run_qa(_cfg(), yesterday_total=5537, today_total=1800, sitemap_count=1800, listing_count=1800,
                new_count=0, missing_count=3737, price_up=0, price_down=0, anomaly_count=0, products=_products(1800))
    assert qa.passed is False
    assert qa.state == "FAIL"
    assert qa.checks["total_change"][0] is False


# ---- 测试 18b：正常波动 -> QA PASS ----
def test_normal_day_qa_pass():
    qa = run_qa(_cfg(), yesterday_total=5537, today_total=5500, sitemap_count=5500, listing_count=5480,
                new_count=5, missing_count=20, price_up=30, price_down=40, anomaly_count=0, products=_products(5500))
    assert qa.passed is True
    assert qa.state == "PASS"


# ---- 测试 19：QA FAIL -> Master 完全不变（写 Master 被拒）----
def test_t19_qa_fail_prevents_master_write():
    from action_tracker.excel.writer import write_master
    qa = run_qa(_cfg(), yesterday_total=5537, today_total=1800, sitemap_count=1800, listing_count=1800,
                new_count=0, missing_count=3737, price_up=0, price_down=0, anomaly_count=0, products=_products(1800))
    assert qa.passed is False
    # dry-run / FAIL 状态一律禁止写 Master
    with pytest.raises(RuntimeError):
        write_master({}, updated_records={}, price_events=[], event_events=[], dry_run=True)


# ---- 测试 19b：BLOCKED 直接 FAIL ----
def test_blocked_qa_fail():
    qa = run_qa(_cfg(), yesterday_total=5537, today_total=0, sitemap_count=0, listing_count=0,
                new_count=0, missing_count=0, price_up=0, price_down=0, anomaly_count=0, products=[], blocked=True)
    assert qa.state == "BLOCKED"
    assert qa.passed is False


def test_incomplete_observation_fails_qa_before_lifecycle_commit():
    qa = run_qa(_cfg(), yesterday_total=10, today_total=10, sitemap_count=10, listing_count=10,
                new_count=0, missing_count=0, price_up=0, price_down=0, anomaly_count=0,
                products=_products(10), observation_valid=False, category_coverage={"Hogar": False})
    assert qa.state == "FAIL"
    assert qa.checks["observation_valid"][0] is False


@pytest.mark.parametrize("access_state", ["COOLDOWN", "PROBE", "DEGRADED"])
def test_non_normal_access_state_fails_even_with_complete_coverage(access_state):
    qa = run_qa(_cfg(), yesterday_total=10, today_total=10, sitemap_count=10, listing_count=10,
                new_count=0, missing_count=0, price_up=0, price_down=0, anomaly_count=0,
                products=_products(10), access_state=access_state)
    assert qa.passed is False and qa.state == "FAIL"
    assert qa.checks["access_state_complete"][0] is False


@pytest.mark.parametrize("detail_state", ["COOLDOWN", "PROBE", "BLOCKED", "DEGRADED"])
def test_detail_access_state_does_not_invalidate_complete_presence(detail_state):
    qa = run_qa(_cfg(), yesterday_total=10, today_total=10, sitemap_count=10, listing_count=10,
                new_count=0, missing_count=0, price_up=0, price_down=0, anomaly_count=0,
                products=_products(10), access_state="NORMAL", detail_access_state=detail_state)
    assert qa.passed is True and qa.state == "PASS"
    assert detail_state in qa.checks["detail_access_non_authoritative"][1]


def test_sitemap_only_pending_fields_do_not_fail_presence_qa():
    products = _products(99)
    for product in products:
        product["listing_fields_source"] = "LISTING_CURRENT_RUN"
    products.append({"sku": "100", "canonical_id": "ACT0000100", "listing_fields_source": "BASELINE",
                     "presence_source": "SITEMAP_ONLY", "detail_status": "ACCESS_INTERRUPTED"})
    qa = run_qa(_cfg(), yesterday_total=100, today_total=100, sitemap_count=100, listing_count=99,
                new_count=1, missing_count=0, price_up=0, price_down=0, anomaly_count=0,
                products=products, access_state="NORMAL", detail_access_state="COOLDOWN")
    assert qa.passed is True
    assert qa.checks["listing_field_completeness"][0] is True
    assert "待补充1" in qa.checks["listing_field_completeness"][1]


def test_valid_sitemap_can_commit_presence_when_listing_is_restricted():
    qa = run_qa(_cfg(), yesterday_total=100, today_total=100, sitemap_count=100, listing_count=40,
                new_count=0, missing_count=0, price_up=0, price_down=0, anomaly_count=0,
                products=_products(100), blocked=False, observation_valid=True,
                category_coverage={"Hogar": False}, access_state="BLOCKED",
                presence_mode="SITEMAP_FALLBACK")
    assert qa.passed is True
    assert qa.state == "PASS_PRESENCE_ONLY"
    assert qa.checks["sitemap_listing_gap"][0] is True


def test_sitemap_fallback_still_rejects_anomalous_presence_data():
    qa = run_qa(_cfg(), yesterday_total=100, today_total=10, sitemap_count=10, listing_count=0,
                new_count=0, missing_count=90, price_up=0, price_down=0, anomaly_count=0,
                products=_products(10), observation_valid=True, access_state="COOLDOWN",
                presence_mode="SITEMAP_FALLBACK")
    assert qa.passed is False
    assert qa.state == "FAIL"


def test_missing_fields_on_listing_observation_fail_qa():
    products = _products(10)
    for product in products:
        product["listing_fields_source"] = "LISTING_CURRENT_RUN"
    products[0]["current_price"] = None
    qa = run_qa(_cfg(), yesterday_total=10, today_total=10, sitemap_count=10, listing_count=10,
                new_count=0, missing_count=0, price_up=0, price_down=0, anomaly_count=0,
                products=products)
    assert qa.passed is False
    assert qa.checks["listing_field_completeness"][0] is False


def test_completed_detail_without_cat2_fails_but_deferred_detail_is_pending():
    products = _products(1)
    products[0]["cat2_es"] = ""
    products[0]["detail_status"] = "COMPLETE"
    qa = run_qa(_cfg(), yesterday_total=1, today_total=1, sitemap_count=1, listing_count=1,
                new_count=0, missing_count=0, price_up=0, price_down=0, anomaly_count=0,
                products=products)
    assert qa.passed is False
    assert qa.checks["detail_category_completeness"][0] is False

    products[0]["detail_status"] = "PENDING"
    qa = run_qa(_cfg(), yesterday_total=1, today_total=1, sitemap_count=1, listing_count=1,
                new_count=0, missing_count=0, price_up=0, price_down=0, anomaly_count=0,
                products=products)
    assert qa.passed is True
    assert qa.checks["detail_category_completeness"][0] is True


@pytest.mark.parametrize("polluted_spec", ["Añadir a tus favoritos", "Todo de C&C", "undefined"])
def test_nonempty_listing_ui_or_placeholder_field_fails_content_legality(polluted_spec):
    products = _products(1)
    products[0]["listing_fields_source"] = "LISTING_CURRENT_RUN"
    products[0]["spec_es"] = polluted_spec
    qa = run_qa(_cfg(), yesterday_total=1, today_total=1, sitemap_count=1, listing_count=1,
                new_count=0, missing_count=0, price_up=0, price_down=0, anomaly_count=0,
                products=products)
    assert qa.passed is False
    assert qa.checks["field_content_legality"][0] is False
    assert qa.counts["illegal_field_content"] == 1


def test_completed_detail_html_or_broken_separator_fails_content_legality():
    products = _products(1)
    products[0].update({
        "detail_status": "COMPLETE",
        "details_es": "Material:: Plástico; Color: Azul",
        "desc_es": "Descripción\nProducto resistente",
    })
    qa = run_qa(_cfg(), yesterday_total=1, today_total=1, sitemap_count=1, listing_count=1,
                new_count=0, missing_count=0, price_up=0, price_down=0, anomaly_count=0,
                products=products)
    assert qa.passed is False
    assert qa.checks["field_content_legality"][0] is False
    assert qa.counts["illegal_field_content"] == 2


def test_pending_detail_content_is_reported_later_not_a_presence_gate():
    products = _products(1)
    products[0].update({"detail_status": "ACCESS_INTERRUPTED", "details_es": "Material:: Plástico"})
    qa = run_qa(_cfg(), yesterday_total=1, today_total=1, sitemap_count=1, listing_count=1,
                new_count=0, missing_count=0, price_up=0, price_down=0, anomaly_count=0,
                products=products)
    assert qa.passed is True
    assert qa.checks["field_content_legality"][0] is True


def test_source_anomaly_is_review_only_and_persisted_with_full_provenance(tmp_path):
    import csv
    import hashlib
    import json

    from action_tracker.snapshot import write_snapshot
    from action_tracker.stage5.source_candidate_v2 import source_hash

    product = {
        "sku": "2513777",
        "canonical_id": "ACT2513777",
        "product_url": "https://example.invalid/2513777",
        "current_price": 1.0,
        "cat1_es": "Hogar",
        "raw_tags": "",
        "name_es": "Producto de limpieza",
        "cat2_es": "Limpieza",
        "spec_es": "500 ml",
        "desc_es": "Producto para el hogar.",
        "details_es": "Sustancia: Válido; Sustancia: Válido; Número del artículo: 2513777",
    }
    qa = run_qa(
        _cfg(), yesterday_total=1, today_total=1, sitemap_count=1, listing_count=1,
        new_count=0, missing_count=0, price_up=0, price_down=0, anomaly_count=0,
        products=[product],
    )

    # A source-field anomaly is retained for research but must not rewrite the
    # source value or independently fail otherwise valid Presence QA.
    assert qa.passed is True
    assert qa.state == "PASS"
    assert qa.counts["source_consistency_findings"] == 1
    finding = qa.findings[0]
    assert finding["sku"] == "2513777"
    assert finding["flags"] == ["SOURCE_ANOMALY_SUSTANCIA_VALIDO"]
    assert finding["source_hash"] == source_hash({
        "name": product["name_es"], "cat1": product["cat1_es"],
        "cat2": product["cat2_es"], "spec": product["spec_es"],
        "description": product["desc_es"], "details": product["details_es"],
    })
    assert finding["source_fields"] == {
        key: product[key] for key in
        ("name_es", "cat1_es", "cat2_es", "spec_es", "desc_es", "details_es")
    }
    assert finding["evidence"][0]["source_key"] == "Sustancia"
    assert finding["evidence"][0]["source_value"] == "Válido"
    assert finding["source_consistency_rules"]["sha256"]
    assert finding["status"] == "OPEN_REVIEW"
    assert finding["action"] == "REVIEW_ONLY_SOURCE_UNCHANGED"

    snapshot = write_snapshot(
        {"paths": {"snapshots": tmp_path / "snapshots"}},
        "2026-09-29",
        {"run_report": {"run_id": "source-anomaly-test"}, "qa_report": qa.to_dict()},
    )
    persisted = json.loads((snapshot / "qa_report.json").read_text(encoding="utf-8"))
    assert persisted["findings"] == qa.findings
    assert persisted["counts"]["source_consistency_findings"] == 1
    with (snapshot / "SOURCE_ANOMALY.csv").open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == [
            "qa_id", "sku", "evidence_index", "anomaly_code", "source_hash", "source_field", "source_key",
            "source_value", "source_fields_json", "rules_manifest_json", "status", "action",
        ]
        anomaly_rows = list(reader)
    assert len(anomaly_rows) == 2
    assert {row["evidence_index"] for row in anomaly_rows} == {"1", "2"}
    assert len({row["qa_id"] for row in anomaly_rows}) == 2
    anomaly = anomaly_rows[0]
    assert anomaly["sku"] == "2513777"
    assert anomaly["evidence_index"] == "1"
    assert anomaly["anomaly_code"] == "SOURCE_ANOMALY_SUSTANCIA_VALIDO"
    assert anomaly["source_field"] == "details"
    assert anomaly["source_key"] == "Sustancia"
    assert anomaly["source_value"] == "Válido"
    assert anomaly["source_hash"] == finding["source_hash"]
    assert json.loads(anomaly["source_fields_json"]) == finding["source_fields"]
    assert anomaly["status"] == "OPEN_REVIEW"
    assert anomaly["action"] == "REVIEW_ONLY_SOURCE_UNCHANGED"
    manifest = json.loads((snapshot / "SOURCE_ANOMALY.manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "source-anomaly-v1"
    assert manifest["artifact"] == "SOURCE_ANOMALY.csv"
    assert manifest["row_count"] == 2
    assert manifest["sha256"] == hashlib.sha256((snapshot / "SOURCE_ANOMALY.csv").read_bytes()).hexdigest()
    assert manifest["qa_report_sha256"] == hashlib.sha256(
        (snapshot / "qa_report.json").read_bytes()
    ).hexdigest()


def test_snapshot_writes_empty_source_anomaly_ledger_when_no_findings(tmp_path):
    import csv
    import json

    from action_tracker.snapshot import verify_source_anomaly_manifest, write_snapshot

    qa = run_qa(
        _cfg(), yesterday_total=1, today_total=1, sitemap_count=1, listing_count=1,
        new_count=0, missing_count=0, price_up=0, price_down=0, anomaly_count=0,
        products=_products(1),
    )
    snapshot = write_snapshot(
        {"paths": {"snapshots": tmp_path / "snapshots"}},
        "2026-09-29",
        {"run_report": {"run_id": "no-source-anomaly-test"}, "qa_report": qa.to_dict()},
    )

    with (snapshot / "SOURCE_ANOMALY.csv").open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == [
            "qa_id", "sku", "evidence_index", "anomaly_code", "source_hash", "source_field", "source_key",
            "source_value", "source_fields_json", "rules_manifest_json", "status", "action",
        ]
        assert list(reader) == []
    manifest = json.loads((snapshot / "SOURCE_ANOMALY.manifest.json").read_text(encoding="utf-8"))
    assert manifest["row_count"] == 0
    assert manifest["sha256"]
    assert verify_source_anomaly_manifest(snapshot)["passed"] is True


def test_source_anomaly_manifest_verifier_rejects_artifact_tampering(tmp_path):
    from action_tracker.snapshot import verify_source_anomaly_manifest, write_snapshot

    qa = run_qa(
        _cfg(), yesterday_total=1, today_total=1, sitemap_count=1, listing_count=1,
        new_count=0, missing_count=0, price_up=0, price_down=0, anomaly_count=0,
        products=_products(1),
    )
    snapshot = write_snapshot(
        {"paths": {"snapshots": tmp_path / "snapshots"}},
        "2026-09-29",
        {"run_report": {"run_id": "source-anomaly-integrity-test"}, "qa_report": qa.to_dict()},
    )
    anomaly_path = snapshot / "SOURCE_ANOMALY.csv"
    qa_report_path = snapshot / "qa_report.json"
    original_anomaly = anomaly_path.read_bytes()
    original_qa = qa_report_path.read_bytes()

    anomaly_path.write_bytes(original_anomaly + b"tampered")
    report = verify_source_anomaly_manifest(snapshot)
    assert report["passed"] is False
    assert "SOURCE_ANOMALY_HASH_MISMATCH" in report["issues"]

    anomaly_path.write_bytes(original_anomaly)
    qa_report_path.write_bytes(original_qa + b" ")
    report = verify_source_anomaly_manifest(snapshot)
    assert report["passed"] is False
    assert "SOURCE_ANOMALY_QA_REPORT_HASH_MISMATCH" in report["issues"]


@pytest.mark.parametrize(
    ("blocked", "observation_valid", "expected_state"),
    [(True, True, "BLOCKED"), (False, False, "FAIL")],
)
def test_source_anomaly_summary_survives_early_qa_failure(blocked, observation_valid, expected_state):
    product = _products(1)[0]
    product.update({
        "sku": "2513777",
        "details_es": "Sustancia: Válido; Número del artículo: 2513777",
    })
    qa = run_qa(
        _cfg(), yesterday_total=1, today_total=1, sitemap_count=1, listing_count=1,
        new_count=0, missing_count=0, price_up=0, price_down=0, anomaly_count=0,
        products=[product], blocked=blocked, observation_valid=observation_valid,
    )

    assert qa.state == expected_state
    assert qa.passed is False
    assert qa.counts["source_consistency_findings"] == 1
    assert qa.checks["source_consistency_review"][0] is True
    assert "待审核=1" in qa.checks["source_consistency_review"][1]
    assert qa.findings[0]["flags"] == ["SOURCE_ANOMALY_SUSTANCIA_VALIDO"]

from action_tracker.localization.release_gate import audit_research_release
from action_tracker.services.hashing import localization_source_hash
from action_tracker.exporting.service import ExportValidationError
from action_tracker.cli import build_parser


def _row(**overrides):
    row = {
        "sku": "1001",
        "name_es": "Caja",
        "cat1_es": "Hogar",
        "cat2_es": "Almacenamiento",
        "spec_es": "2 unidades",
        "desc_es": "Caja útil",
        "details_es": "Número del artículo: 1001",
        "name_zh": "收纳盒",
        "cat1_zh": "家居布置",
        "cat2_zh": "收纳用品",
        "spec_zh": "2件",
        "desc_zh": "实用收纳盒",
        "details_zh": "商品编号：1001",
        "zh_review_status": "VERIFIED",
        "zh_freshness_status": "CURRENT",
        "zh_source_hash": "",
    }
    row.update(overrides)
    row["zh_source_hash"] = row.get("zh_source_hash") or localization_source_hash(row)
    return row


def test_research_release_passes_a_complete_projection():
    result = audit_research_release([_row()], expected_skus={"1001"})
    assert result.ok
    assert result.counts["SOURCE_HASH_MISMATCH"] == 0


def test_release_blocks_missing_field_and_unapproved_status():
    result = audit_research_release([_row(cat2_zh="", zh_review_status="PENDING")])
    assert not result.ok
    assert result.counts["UNAPPROVED_ZH"] >= 2
    assert "UNAPPROVED_ZH:1001:cat2_zh" in result.issues


def test_release_blocks_stale_and_source_hash_change():
    result = audit_research_release([_row(name_es="Caja nueva", zh_freshness_status="STALE", zh_source_hash="old")])
    assert not result.ok
    assert result.counts["STALE_ZH"] == 1
    assert result.counts["SOURCE_HASH_MISMATCH"] == 1


def test_release_blocks_spanish_residual():
    result = audit_research_release([_row(name_zh="Caja 收纳盒")])
    assert not result.ok
    assert result.counts["SPANISH_RESIDUAL"] == 1


def test_release_compares_export_identity_and_fact_projection():
    row = _row(current_price=1.99, product_url="https://example/1001")
    result = audit_research_release(
        [row],
        exported_rows=[{"sku": "1001", "折后价": 2.99, "商品链接": "https://example/1001"}],
    )
    assert not result.ok
    assert result.counts["FACT_MISMATCH"] == 1


def test_release_blocks_undeclared_display_mismatch():
    result = audit_research_release([_row()], display_mismatches=["1001:详情"])
    assert not result.ok
    assert result.counts["UNDECLARED_DISPLAY_MISMATCH"] == 1


def test_field_level_review_status_overrides_global_status():
    row = _row(review_status="VERIFIED")
    row["zh_field_provenance"] = {
        field: {"review_status": "VERIFIED", "freshness_status": "CURRENT"}
        for field in ("name", "cat1", "cat2", "spec", "description", "details")
    }
    row["zh_field_provenance"]["description"]["review_status"] = "PENDING"
    result = audit_research_release([row])
    assert not result.ok
    assert "UNAPPROVED_ZH:1001:desc_zh:status=PENDING" in result.issues


def test_research_release_is_chinese_only_contract():
    assert str(ExportValidationError("RESEARCH_RELEASE_ZH_ONLY")) == "RESEARCH_RELEASE_ZH_ONLY"


def test_export_cli_exposes_explicit_research_release_mode():
    args = build_parser().parse_args([
        "export", "--lang", "zh", "--no-images", "--date", "2026-09-08", "--research-release",
    ])
    assert args.research_release is True

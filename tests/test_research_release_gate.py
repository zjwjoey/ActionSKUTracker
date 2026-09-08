from action_tracker.localization.release_gate import audit_research_release, load_allowed_tokens, load_explicit_exceptions
from action_tracker.services.hashing import localization_source_hash
from action_tracker.exporting.service import ExportValidationError
from action_tracker.exporting import service as export_service
from action_tracker.cli import build_parser
from types import SimpleNamespace
import pytest


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


def test_formal_export_blocks_non_sqlite_source_in_research_release(monkeypatch, tmp_path):
    profile = SimpleNamespace(language="zh", profile_id="full_zh_no_images")
    source = SimpleNamespace(kind="FORMAL_SNAPSHOT", source_commit_id=None, run_id="r1", records=tuple(),
                             export_date="2026-09-08")
    monkeypatch.setattr(export_service, "load_profile", lambda *args, **kwargs: profile)
    monkeypatch.setattr(export_service, "resolve_formal_source", lambda *args, **kwargs: source)
    with pytest.raises(ExportValidationError, match="RESEARCH_RELEASE_REQUIRES_SQLITE_APPLIED_SOURCE"):
        export_service.export_catalog(
            {"project_root": tmp_path, "paths": {"exports": tmp_path}},
            language="zh", export_date="2026-09-08", no_images=True, research_release=True,
        )


def test_allowed_tokens_load_only_reviewed_dictionary_values(tmp_path):
    (tmp_path / "brand_dictionary.csv").write_text(
        "canonical_name,aliases_es,review_status\nBrand Uno,Brand Uno|Uno,HUMAN_REVIEWED\nBad,Bad,UNREVIEWED\n",
        encoding="utf-8",
    )
    assert "Brand Uno" in load_allowed_tokens(tmp_path)
    assert "Uno" in load_allowed_tokens(tmp_path)
    assert "Bad" not in load_allowed_tokens(tmp_path)


def test_release_residual_check_allows_series_but_blocks_known_spanish():
    assert audit_research_release([_row(name_zh="Spidey 收纳盒")]).ok
    result = audit_research_release([_row(name_zh="Hogar 收纳盒")])
    assert not result.ok
    assert result.counts["SPANISH_RESIDUAL"] == 1


def test_explicit_exception_requires_audit_fields_and_can_close_one_issue():
    result = audit_research_release(
        [_row(cat2_zh="")],
        explicit_exceptions=[{
            "issue_id": "UNAPPROVED_ZH:1001:cat2_zh",
            "approved_by": "reviewer",
            "evidence": "official page has no category",
            "expires_at": "2099-12-31",
        }],
    )
    assert result.ok
    assert "UNAPPROVED_ZH:1001:cat2_zh" not in result.issues
    assert result.counts["EXPLICIT_EXCEPTION"] == 1


def test_release_exception_file_is_explicit_and_structured(tmp_path):
    path = tmp_path / "exceptions.json"
    path.write_text(
        '{"exceptions":[{"issue_id":"UNAPPROVED_ZH:1:spec_zh",'
        '"approved_by":"reviewer","evidence":"source absent",'
        '"expires_at":"2099-12-31"}]}',
        encoding="utf-8",
    )
    assert load_explicit_exceptions(path)[0]["issue_id"] == "UNAPPROVED_ZH:1:spec_zh"

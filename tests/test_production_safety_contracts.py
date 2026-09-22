from pathlib import Path

from action_tracker.cli import build_parser, resolve_daily_run_dry_run
from action_tracker.config import load_settings
from action_tracker.services.category_consistency import load_primary_category_map


def test_daily_run_is_dry_run_until_explicit_apply():
    parser = build_parser()
    assert resolve_daily_run_dry_run(parser.parse_args(["daily-run"]).dry_run) is True
    assert resolve_daily_run_dry_run(parser.parse_args(["daily-run", "--apply"]).dry_run) is False


def test_zero_detail_limit_is_disable_not_unlimited():
    from action_tracker.orchestrator.daily import _select_detail_plans

    plans = [{"sku": "1", "need_detail": True, "reason": "NEW"}]
    selected, deferred = _select_detail_plans(plans, 0)
    assert selected == [] and deferred == plans


def test_repository_config_has_no_developer_absolute_cookie_or_reference_path():
    cfg = load_settings()
    assert cfg["browser"]["cookies_path"].is_relative_to(Path(cfg["project_root"]))
    text = (Path(cfg["project_root"]) / "config" / "settings.yaml").read_text(encoding="utf-8")
    assert "F:\\" not in text and "D:\\Users" not in text


def test_review_only_category_mapping_is_fail_closed(tmp_path):
    path = tmp_path / "mapping.csv"
    path.write_text(
        "cat2_es,primary_cat1_es,evidence_source\n"
        "Pintura,Hobby,review-workbook\n"
        "Cocina,Cocina,OFFICIAL_BREADCRUMB\n",
        encoding="utf-8",
    )
    result = load_primary_category_map(path)
    assert "pintura" not in result
    assert result["cocina"] == "Cocina"

"""Presence evidence protects lifecycle state from incomplete collection runs."""
from action_tracker.monitor.sku_monitor import run_sku_monitor
from action_tracker import state as st


def _known(sku: str, category: str, missing: str = "0") -> dict:
    return {sku: {
        "official_sku": sku, "canonical_id": f"ACT{sku.zfill(7)}",
        "first_seen_date": "2026-08-01", "last_seen_date": "2026-08-09",
        "last_status": "ACTIVE", "missing_count": missing, "cat1_es": category,
    }}


def test_invalid_observation_never_advances_missing_count():
    known = _known("1001", "Hogar", "1")
    statuses, _ = run_sku_monitor([], {}, {"1001": {"sku": "1001", "cat1_es": "Hogar"}}, known,
                                  sitemap_valid=False, category_coverage={"Hogar": False})
    assert statuses["1001"].status == "UNKNOWN"
    assert statuses["1001"].missing_count == 1
    assert st.apply_state_transition(known, statuses, "2026-08-10", "R1", 3)["known"]["1001"]["missing_count"] == "1"


def test_complete_relevant_category_can_confirm_absence_without_sitemap():
    known = _known("1001", "Hogar")
    statuses, _ = run_sku_monitor([], {}, {"1001": {"sku": "1001", "cat1_es": "Hogar"}}, known,
                                  sitemap_valid=False, category_coverage={"Hogar": True, "Moda": False})
    assert statuses["1001"].status == "MISSING_FIRST"


def test_auxiliary_badge_is_presence_evidence_not_reappearance():
    statuses, today = run_sku_monitor([], {}, {}, {}, nuevo_skus={"1001"})
    assert today == {"1001"}
    assert statuses["1001"].status == "NEW"
    assert statuses["1001"].source_flag == "AUXILIARY_ONLY"


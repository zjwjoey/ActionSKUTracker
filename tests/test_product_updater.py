from types import SimpleNamespace

from action_tracker.products.updater import plan_updates


def _status(sku: str, lifecycle: str, source_flag: str):
    return SimpleNamespace(
        sku=sku,
        canonical_id=f"ACT{sku.zfill(7)}",
        status=lifecycle,
        source_flag=source_flag,
    )


def test_listing_only_new_sku_stays_out_of_detail_plan():
    plans = plan_updates(
        {"1001": _status("1001", "NEW", "LISTING_ONLY")},
        baseline={},
        today_light={"1001": {"current_price": 1.0, "product_url": "https://example/1001"}},
    )

    assert plans == [{
        "sku": "1001",
        "canonical_id": "ACT0001001",
        "reason": "NEW",
        "need_detail": False,
        "light": {"current_price": 1.0, "product_url": "https://example/1001"},
    }]


def test_both_source_new_sku_is_eligible_for_detail():
    plans = plan_updates(
        {"1001": _status("1001", "NEW", "BOTH")},
        baseline={},
        today_light={"1001": {"current_price": 1.0}},
    )

    assert plans[0]["need_detail"] is True


def test_listing_only_reappeared_sku_stays_out_of_detail_plan():
    plans = plan_updates(
        {"1001": _status("1001", "REAPPEARED", "LISTING_ONLY")},
        baseline={"1001": {"current_price": 1.0}},
        today_light={"1001": {"current_price": 1.0}},
    )

    assert plans[0]["reason"] == "REAPPEARED"
    assert plans[0]["need_detail"] is False


def test_both_source_reappeared_sku_is_eligible_for_detail():
    plans = plan_updates(
        {"1001": _status("1001", "REAPPEARED", "BOTH")},
        baseline={"1001": {"current_price": 1.0}},
        today_light={"1001": {"current_price": 1.0}},
    )

    assert plans[0]["need_detail"] is True


def test_listing_spec_does_not_hide_missing_detail_page_content():
    plans = plan_updates(
        {"1001": _status("1001", "ACTIVE", "BOTH")},
        baseline={"1001": {
            "current_price": 1.0,
            "cat2_es": "Limpieza",
            "spec_es": "78 gramos",
            "desc_es": "",
            "details_es": None,
        }},
        today_light={"1001": {"current_price": 1.0, "spec_es": "78 gramos"}},
    )

    assert plans[0]["reason"] == "MISSING_FIELD"
    assert plans[0]["need_detail"] is True


def test_description_or_details_is_enough_to_avoid_missing_detail_plan():
    plans = plan_updates(
        {"1001": _status("1001", "ACTIVE", "BOTH")},
        baseline={"1001": {
            "current_price": 1.0,
            "cat2_es": "Limpieza",
            "spec_es": "78 gramos",
            "desc_es": "Descripci\u00f3n oficial",
            "details_es": None,
        }},
        today_light={"1001": {"current_price": 1.0}},
    )

    assert plans == []


def test_active_product_with_missing_cat2_is_requeued_for_detail():
    plans = plan_updates(
        {"1001": _status("1001", "ACTIVE", "BOTH")},
        baseline={"1001": {
            "current_price": 1.0,
            "cat2_es": "",
            "desc_es": "Descripción oficial",
            "details_es": "Número del artículo: 1001",
        }},
        today_light={"1001": {"current_price": 1.0}},
    )

    assert plans[0]["reason"] == "CATEGORY_MISSING"
    assert plans[0]["need_detail"] is True


def test_listing_only_product_with_missing_cat2_is_not_detail_requested():
    plans = plan_updates(
        {"1001": _status("1001", "ACTIVE", "LISTING_ONLY")},
        baseline={"1001": {
            "current_price": 1.0,
            "cat2_es": "",
            "desc_es": "Descripción oficial",
            "details_es": "Número del artículo: 1001",
        }},
        today_light={"1001": {"current_price": 1.0}},
    )

    assert plans[0]["reason"] == "CATEGORY_MISSING"
    assert plans[0]["need_detail"] is False

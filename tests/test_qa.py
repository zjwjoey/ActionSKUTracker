"""规范 §60 测试 18-19：QA Gate。"""
import json
import pytest

from action_tracker.qa.validator import run_qa, trusted_field_scope
from action_tracker.localization.hashes import value_hash


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


@pytest.mark.parametrize('access_blocked', [False, True])
def test_incomplete_presence_still_checks_field_and_identity_evidence(access_blocked):
    product = {'sku': '1001', 'canonical_id': '', 'product_url': 'u',
               'current_price': 9999.0, 'cat1_es': 'Hogar'}
    qa = run_qa(_cfg(), yesterday_total=10, today_total=1,
                sitemap_count=0, listing_count=1, new_count=0, missing_count=0,
                price_up=0, price_down=0, anomaly_count=0, products=[product],
                observation_valid=False, blocked=access_blocked)
    assert not qa.passed
    assert qa.state == ('BLOCKED' if access_blocked else 'FAIL')
    assert qa.checks['observation_valid'][0] is False
    assert any(i['reason'] == 'CANONICAL_ID_MISSING' and i['level'] == 'HARD_BLOCK'
               for i in qa.issues)
    assert any(i['reason'] == 'PRICE_INVALID_OR_OUT_OF_RANGE' and i['level'] == 'FIELD_REVIEW'
               for i in qa.issues)


def test_trusted_field_scope_does_not_waive_presence_or_price_review():
    product = {'sku': '1001', 'canonical_id': 'ACT0001001',
               'product_url': 'https://www.action.com/es-es/p/1001/producto/',
               'presence_source': 'LISTING_ONLY', 'name_es': 'Producto',
               'current_price': 9999.0, 'cat1_es': 'Hogar', 'desc_es': 'Old',
               'fact_field_provenance': {
                   'name_es': {'state': 'CURRENT_VERIFIED', 'source': 'LISTING_CURRENT_RUN'},
                   'current_price': {'state': 'CURRENT_VERIFIED', 'source': 'LISTING_CURRENT_RUN'},
                   'desc_es': {'state': 'HISTORY_RETAINED', 'source': 'BASELINE'}}}
    qa = run_qa(_cfg(), yesterday_total=10, today_total=1, sitemap_count=0, listing_count=1,
                new_count=0, missing_count=0, price_up=0, price_down=0, anomaly_count=0,
                products=[product], observation_valid=False)
    scope = trusted_field_scope([product], qa, run_id='partial', business_date='2026-10-30')
    assert not qa.passed and qa.state == 'FAIL'
    assert scope['presence_authority'] is False
    assert scope['rows'][0]['fields'] == {
        'name_es': {'value_hash': value_hash('Producto'), 'source': 'LISTING_CURRENT_RUN'}}
    assert scope == trusted_field_scope([product], qa, run_id='partial', business_date='2026-10-30')
    assert trusted_field_scope([product, product], qa, run_id='partial', business_date='2026-10-30')['rows'] == []
    assert trusted_field_scope([{**product, 'product_url': 'https://example.com/es-es/p/1001/'}],
                               qa, run_id='partial', business_date='2026-10-30')['rows'] == []


def test_retained_nonempty_price_is_reviewed_without_blocking_other_facts():
    product = _products(1)[0]
    product['fact_field_provenance'] = {'current_price': {'state': 'HISTORY_RETAINED', 'source': 'BASELINE'}}
    qa = run_qa(_cfg(), yesterday_total=1, today_total=1, sitemap_count=1, listing_count=1,
                new_count=0, missing_count=0, price_up=0, price_down=0, anomaly_count=0, products=[product])
    assert qa.passed and qa.state == 'PASS'
    reviews = [i for i in qa.issues if i['reason'] == 'PRICE_NOT_OBSERVED_THIS_RUN']
    assert len(reviews) == 1
    assert reviews[0]['source_hash'] == value_hash(1.0)


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
    assert {(i['sku'], i['field_name']) for i in qa.issues if i['level'] == 'FIELD_REVIEW'} == {
        ('100', 'current_price'), ('100', 'cat1_es')}


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


def test_missing_price_on_verified_listing_is_field_review_not_batch_failure():
    products = _products(10)
    for product in products:
        product["listing_fields_source"] = "LISTING_CURRENT_RUN"
    products[0]["current_price"] = None
    qa = run_qa(_cfg(), yesterday_total=10, today_total=10, sitemap_count=10, listing_count=10,
                new_count=0, missing_count=0, price_up=0, price_down=0, anomaly_count=0,
                products=products)
    assert qa.passed is True
    assert qa.checks["listing_field_completeness"][0] is False
    assert qa.issues == [{"level": "FIELD_REVIEW", "sku": "0", "field_name": "current_price",
                          "reason": "PRICE_SOURCE_MISSING", "source_value": None,
                          "source_hash": value_hash(None)}]


def _normal_qa(products, **overrides):
    args = dict(yesterday_total=len(products), today_total=len(products), sitemap_count=len(products),
                listing_count=len(products), new_count=0, missing_count=0, price_up=0, price_down=0,
                anomaly_count=0, products=products)
    args.update(overrides)
    return run_qa(_cfg(), **args)


@pytest.mark.parametrize('value', [-1, 0, 5000, float('nan'), float('inf'), 'invalid', True])
def test_bad_price_is_bound_to_only_its_sku_and_does_not_approve_the_value(value):
    rows = _products(2)
    rows[0]['current_price'] = value
    qa = _normal_qa(rows)
    assert qa.passed and qa.state == 'PASS'
    assert qa.checks['price_range'][0] is False
    assert [(i['level'], i['sku'], i['field_name']) for i in qa.issues] == [('FIELD_REVIEW', '0', 'current_price')]
    assert qa.issues[0]['source_hash'] == value_hash(value)
    assert qa.to_dict()['issues'] == qa.issues
    assert json.dumps(qa.to_dict(), allow_nan=False)
    assert rows[0]['current_price'] is value  # QA diagnoses; it does not repair sources.


def test_missing_category_and_unparsed_tag_have_distinct_scopes():
    rows = _products(2)
    rows[0]['cat1_es'] = None
    rows[1]['raw_tags'] = 'unknown transport badge'
    qa = _normal_qa(rows)
    assert qa.passed
    assert {(i['level'], i['sku'], i['field_name']) for i in qa.issues} == {
        ('FIELD_REVIEW', '0', 'cat1_es'), ('WARNING', '1', 'raw_tags')}
    assert not qa.checks['listing_field_completeness'][0]
    assert not qa.checks['tag_parse_rate'][0]


def test_reported_price_anomalies_are_field_scoped_even_below_aggregate_limit():
    qa = _normal_qa(_products(2), anomaly_count=1,
                    price_anomalies=[{'sku': '1', 'problem': 'PRICE_ANOMALY'}])
    assert qa.passed
    assert [(i['level'], i['sku'], i['field_name'], i['source_value']) for i in qa.issues] == [
        ('FIELD_REVIEW', '1', 'current_price', 1.0)]


def test_aggregate_anomaly_threshold_is_a_warning_not_unidentified_field_permission():
    qa = _normal_qa(_products(2), anomaly_count=21)
    assert qa.passed and not qa.checks['anomaly_count'][0]
    assert qa.issues[0]['level'] == 'WARNING' and qa.issues[0]['reason'] == 'PRICE_ANOMALY_COUNT'


@pytest.mark.parametrize('field,value', [('sku', ''), ('canonical_id', ''), ('product_url', '')])
def test_missing_identity_or_identity_url_remains_hard_block(field, value):
    rows = _products(2)
    rows[0][field] = value
    qa = _normal_qa(rows)
    assert not qa.passed
    assert any(i['level'] == 'HARD_BLOCK' and i['field_name'] == field for i in qa.issues)


def test_duplicate_identity_does_not_become_a_field_review():
    rows = _products(2)
    rows[1]['sku'] = rows[0]['sku']
    rows[1]['canonical_id'] = rows[0]['canonical_id']
    qa = _normal_qa(rows)
    assert not qa.passed
    assert {i['reason'] for i in qa.issues} >= {'DUPLICATE_SKU', 'DUPLICATE_CANONICAL_ID'}


def test_invalid_observation_retains_explicit_hard_block_issue():
    qa = _normal_qa(_products(2), observation_valid=False)
    assert not qa.passed and qa.issues[0]['level'] == 'HARD_BLOCK'
    assert qa.issues[0]['reason'] == 'OBSERVATION_INVALID'

from action_tracker.exporting.release_gate import evaluate_release_gate, ReleaseGateError


def _record():
    return {
        "sku": "1001", "current_price": 2.5, "original_price": 3.0,
        "product_url": "https://example.test/p/1001", "image_url": "https://example.test/i/1001.jpg",
        "source_hash": "a" * 64,
        "_localization_provenance": {
            field: {"review_status": "HUMAN_REVIEWED", "source_hash": "a" * 64}
            for field in ("name", "cat1", "cat2", "spec", "description", "details")
        },
    }


def _zh_row():
    return {
        "编号": "1001", "折后价": 2.5, "原价": 3.0,
        "图片链接": "https://example.test/i/1001.jpg", "商品链接": "https://example.test/p/1001",
        "标题": "商品", "分类1": "家居", "分类2": "收纳", "规格": "1件",
        "描述": "中文描述", "产品详情": "中文详情",
    }


def test_release_gate_requires_field_level_approval_and_hash():
    record = _record()
    record["_localization_provenance"]["details"] = {"review_status": "PENDING", "source_hash": "stale"}
    result = evaluate_release_gate([record], [_zh_row()], language="zh", strict=False)
    assert result["passed"] is False
    assert result["counts"]["UNAPPROVED_ZH"] == 1
    assert result["counts"]["SOURCE_HASH_MISMATCH"] == 1
    assert result["counts"]["STALE_ZH"] == 1


def test_release_gate_strict_blocks_and_passes_after_field_approval():
    record = _record()
    try:
        evaluate_release_gate([record], [{**_zh_row(), "商品链接": "https://bad.test/1001"}], language="zh", strict=True)
    except ReleaseGateError as exc:
        assert "FACT_MISMATCH" in str(exc)
    else:
        raise AssertionError("strict gate unexpectedly passed")
    result = evaluate_release_gate([record], [_zh_row()], language="zh", strict=True)
    assert result["passed"] is True
    assert result["counts"]["FACT_MISMATCH"] == 0

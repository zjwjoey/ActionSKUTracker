from action_tracker.exporting.release_gate import evaluate_release_gate, ReleaseGateError
from action_tracker.exporting.dictionary_join import _zh_remarks
from action_tracker.services.hashing import localization_field_source_hash


def _record():
    record = {
        "sku": "1001", "current_price": 2.5, "original_price": 3.0,
        "product_url": "https://example.test/p/1001", "image_url": "https://example.test/i/1001.jpg",
        "name_es": "Caja de almacenamiento", "cat1_es": "Hogar", "cat2_es": "Almacenaje",
        "spec_es": "1 unidad", "desc_es": "Caja para guardar objetos", "details_es": "Material: plástico",
        "source_hash": "a" * 64,
    }
    record["_localization_provenance"] = {
        field: {
            "review_status": "HUMAN_REVIEWED",
            "source_hash": localization_field_source_hash(record, field),
            "approved_by": "test-owner",
            "approved_at": "2026-09-29T10:00:00+00:00",
            "freshness_status": "CURRENT",
        }
        for field in ("name", "cat1", "cat2", "spec", "description", "details")
    }
    return record


def _zh_row():
    return {
        "编号": "1001", "折后价": 2.5, "原价": 3.0,
        "图片链接": "https://example.test/i/1001.jpg", "商品链接": "https://example.test/p/1001",
        "标题": "收纳盒", "分类1": "家居布置", "分类2": "收纳", "规格": "1件",
        "描述": "用于收纳物品的盒子", "产品详情": "材质：塑料",
    }


def test_release_gate_requires_field_level_approval_and_hash():
    record = _record()
    record["_localization_provenance"]["details"] = {"review_status": "PENDING", "source_hash": "stale"}
    result = evaluate_release_gate([record], [_zh_row()], language="zh", strict=False)
    assert result["passed"] is False
    assert result["counts"]["UNAPPROVED_ZH"] == 1
    assert result["counts"]["SOURCE_HASH_MISMATCH"] == 1
    assert result["counts"]["STALE_ZH"] == 1


def test_release_gate_requires_approval_actor_time_and_current_freshness():
    record = _record()
    record["_localization_provenance"]["details"].update({
        "approved_by": "", "approved_at": "not-a-timestamp", "freshness_status": "STALE",
    })

    result = evaluate_release_gate([record], [_zh_row()], language="zh", strict=False)

    assert result["passed"] is False
    assert result["counts"]["APPROVAL_PROVENANCE_MISSING"] == 1
    assert result["counts"]["ZH_FRESHNESS_INVALID"] == 1
    try:
        evaluate_release_gate([record], [_zh_row()], language="zh", strict=True)
    except ReleaseGateError as exc:
        assert "APPROVAL_PROVENANCE_MISSING=1" in str(exc)
        assert "ZH_FRESHNESS_INVALID=1" in str(exc)
    else:
        raise AssertionError("strict gate unexpectedly accepted incomplete approval provenance")


def test_release_gate_rejects_timezone_naive_approval_timestamp():
    record = _record()
    record["_localization_provenance"]["name"]["approved_at"] = "2026-09-29T10:00:00"

    result = evaluate_release_gate([record], [_zh_row()], language="zh", strict=False)

    assert result["passed"] is False
    assert result["counts"]["APPROVAL_PROVENANCE_MISSING"] == 1


def test_release_gate_accepts_current_and_fresh_source_bound_approval():
    for freshness in ("CURRENT", "FRESH"):
        record = _record()
        record["_localization_provenance"]["name"]["freshness_status"] = freshness
        result = evaluate_release_gate([record], [_zh_row()], language="zh", strict=True)
        assert result["passed"] is True
        assert result["counts"]["ZH_FRESHNESS_INVALID"] == 0


def test_release_gate_requires_verbatim_identity_for_unknown_official_label():
    record = _record()
    record["raw_tags"] = "Nuevo | Etiqueta piloto | -21%"
    row = {
        **_zh_row(),
        "备注": "在售状态：在售；官网官方标签：Nuevo（官网新品标签） | Etiqueta piloto；折扣：21",
    }

    passing = evaluate_release_gate([record], [row], language="zh", strict=False)
    assert passing["counts"]["OFFICIAL_LABEL_DROPPED"] == 0
    assert passing["counts"]["OFFICIAL_LABEL_MISMATCH"] == 0

    row["备注"] = "在售状态：在售；官网官方标签：Nuevo（官网新品标签）；折扣：21"
    failing = evaluate_release_gate([record], [row], language="zh", strict=False)
    finding = next(issue for issue in failing["issues"] if issue["code"] == "OFFICIAL_LABEL_MISMATCH")
    assert finding["sku"] == "1001"
    assert finding["source_labels"] == ["Nuevo", "Etiqueta piloto"]
    assert finding["target_labels"] == ["Nuevo"]


def test_current_chinese_export_remarks_preserve_known_official_source_labels():
    record = _record()
    record.update({
        "raw_tags": "Nuevo | Promoción semanal | Una opción más sostenible | -21%",
        "discount": "21%",
    })
    row = {**_zh_row(), "备注": _zh_remarks(record)}

    assert "Nuevo（官网新品标签）" in row["备注"]
    assert "Promoción semanal（官网每周促销标签）" in row["备注"]
    assert "Una opción más sostenible（官网可持续选择标签）" in row["备注"]
    assert "-21%" not in row["备注"]

    result = evaluate_release_gate([record], [row], language="zh", strict=False)
    assert result["counts"].get("OFFICIAL_LABEL_DROPPED", 0) == 0
    assert result["counts"].get("OFFICIAL_LABEL_MISMATCH", 0) == 0
    assert result["counts"].get("OFFICIAL_LABEL_HALLUCINATED", 0) == 0


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
    assert result["quality_status"] == "RELEASE_PASS_NOT_GOLD"
    assert result["gold_status"] == "NOT_CERTIFIED"
    assert result["gold_eligible"] is False
    assert result["counts"]["FACT_MISMATCH"] == 0


def test_release_gate_routes_unresolved_chinese_brand_marker_to_review():
    result = evaluate_release_gate(
        [_record()], [{**_zh_row(), "标题": "迪士尼牌收纳盒"}],
        language="zh", strict=False,
        confirmed_brand_phrases_by_sku={"1001": ("Disney",)},
    )
    assert result["passed"] is False
    assert result["counts"]["UNRESOLVED_CHINESE_BRAND_MARKER"] == 1


def test_release_gate_blocks_explicitly_forbidden_same_field_term():
    record = _record()
    record["desc_es"] = "Caja de madera para guardar objetos"
    record["_localization_provenance"]["description"]["source_hash"] = localization_field_source_hash(record, "description")
    row = {**_zh_row(), "描述": "木材收纳盒，用于存放物品"}
    result = evaluate_release_gate(
        [record], [row], language="zh", strict=False,
        approved_terms=({
            "term_es": "madera", "term_zh": "木质", "term_type": "material",
            "forbidden_zh": "木材", "review_status": "APPROVED",
        },),
    )
    assert result["passed"] is False
    assert result["counts"]["TERM_FORBIDDEN_TRANSLATION"] == 1


def test_release_gate_reports_canonical_synonym_as_nonblocking_review():
    record = _record()
    record["desc_es"] = "Caja de madera para guardar objetos"
    record["_localization_provenance"]["description"]["source_hash"] = localization_field_source_hash(record, "description")
    row = {**_zh_row(), "描述": "木制收纳盒，用于存放物品"}
    result = evaluate_release_gate(
        [record], [row], language="zh", strict=True,
        approved_terms=({
            "term_es": "madera", "term_zh": "木质", "term_type": "material",
            "forbidden_zh": "", "review_status": "APPROVED",
        },),
    )
    assert result["passed"] is True
    assert result["quality_status"] == "RELEASE_PASS_WITH_REVIEW_NOT_GOLD"
    assert result["gold_status"] == "NOT_CERTIFIED"
    assert result["counts"]["APPROVED_TERM_CANONICAL_ABSENT_REVIEW"] == 1
    assert result["review_findings"][0]["canonical_absence_is_not_proof_of_error"] is True
    assert len(result["approved_term_checker_sha256"]) == 64


def test_release_gate_reports_cross_field_term_movement_as_nonblocking_review():
    record = _record()
    record["details_es"] = "Material: madera"
    record["_localization_provenance"]["details"]["source_hash"] = localization_field_source_hash(record, "details")
    row = {
        **_zh_row(), "描述": "木质收纳盒，用于存放物品", "产品详情": "材质：木质",
    }

    result = evaluate_release_gate(
        [record], [row], language="zh", strict=True,
        approved_terms=({
            "term_es": "madera", "term_zh": "木质", "term_type": "material",
            "forbidden_zh": "", "review_status": "APPROVED",
        },),
    )

    assert result["passed"] is True
    assert result["counts"]["APPROVED_TERM_CROSS_FIELD_REVIEW"] == 1
    finding = next(
        item for item in result["review_findings"]
        if item["code"] == "APPROVED_TERM_CROSS_FIELD_REVIEW"
    )
    assert finding["source_field"] == "details"
    assert finding["field"] == "description"


def test_release_gate_reports_glossary_target_without_source_term_as_review():
    record = _record()
    row = {**_zh_row(), "描述": "木质收纳盒，用于存放物品"}

    result = evaluate_release_gate(
        [record], [row], language="zh", strict=True,
        approved_terms=({
            "term_es": "madera", "term_zh": "木质", "term_type": "material",
            "forbidden_zh": "", "review_status": "APPROVED",
        },),
    )

    assert result["passed"] is True
    assert result["counts"]["APPROVED_TERM_UNSUPPORTED_TARGET_REVIEW"] == 1
    finding = next(
        item for item in result["review_findings"]
        if item["code"] == "APPROVED_TERM_UNSUPPORTED_TARGET_REVIEW"
    )
    assert finding["field"] == "description"
    assert finding["source_term_absent_from_all_localized_fields"] is True


def test_release_gate_blocks_certification_and_negation_loss_or_invention():
    cases = (
        (
            "Papel FSC® certificado, sin BPA",
            "纸张",
            {"CERTIFICATION_DROPPED", "NEGATION_DROPPED"},
        ),
        (
            "Papel para manualidades",
            "纸张，FSC认证，不含BPA",
            {"CERTIFICATION_HALLUCINATED", "NEGATION_HALLUCINATED"},
        ),
        (
            "Fórmula vegana",
            "纯素配方，无硫酸盐基底",
            {"UNSUPPORTED_NEGATIVE_ATTRIBUTE"},
        ),
    )

    for source_description, target_description, expected in cases:
        record = _record()
        record["desc_es"] = source_description
        record["_localization_provenance"]["description"]["source_hash"] = localization_field_source_hash(record, "description")
        result = evaluate_release_gate(
            [record], [{**_zh_row(), "描述": target_description}], language="zh", strict=False,
        )

        assert result["passed"] is False
        assert all(result["counts"][code] > 0 for code in expected)


def test_release_gate_blocks_unmapped_closed_enum_detail_value():
    record = _record()
    record["details_es"] = "Tipo de batería: Alcalina"
    record["_localization_provenance"]["details"]["source_hash"] = localization_field_source_hash(record, "details")
    row = {**_zh_row(), "产品详情": "电池类型：碱性电池"}

    result = evaluate_release_gate([record], [row], language="zh", strict=False)

    assert result["passed"] is False
    assert result["counts"]["DETAIL_TERMINOLOGY_REVIEW"] == 1
    finding = next(item for item in result["issues"] if item["code"] == "DETAIL_TERMINOLOGY_REVIEW")
    assert "DETAIL_SOURCE_VALUE_UNMAPPED_REVIEW:0" in finding["flags"]

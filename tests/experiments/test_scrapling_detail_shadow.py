from __future__ import annotations

import csv
from pathlib import Path
import shutil

import pytest

from action_tracker.experiments.scrapling_detail.comparator import compare_fields
from action_tracker.experiments.scrapling_detail.parser import parse_detail_html
from action_tracker.experiments.scrapling_detail.sampling import select_sample
from action_tracker.experiments.scrapling_detail.validator import validate_detail
from action_tracker.experiments.scrapling_detail.runner import (
    RESULT_FIELDS,
    _is_normal_page_result,
    _is_parser_failure,
    _warm_detail_session,
    collect_one,
)
from action_tracker.services.access import AccessController, AccessState


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures" / "scrapling_detail"
URL = "https://www.action.com/es-es/p/3220414/colcha-deluxe/"
SKU = "3220414"


@pytest.fixture
def normal_html() -> str:
    return (FIXTURES / "normal.html").read_text(encoding="utf-8")


def test_normal_detail_extracts_and_validates_all_fields(tmp_path, normal_html):
    row = parse_detail_html(normal_html, URL, SKU, tmp_path / "adaptive.sqlite3")
    validation = validate_detail(row, target_sku=SKU, url=URL, html=normal_html)
    assert validation.valid, validation.reasons
    assert row["sku"] == SKU
    assert row["name_es"] == "Colcha Deluxe"
    assert row["cat1_es"] == "Vivienda"
    assert row["cat2_es"] == "Mantas de viaje y mantas"
    assert row["spec_es"] == "220 x 240 cm | varios colores"
    assert row["desc_es"] == "Colcha suave para una cama amplia. Fácil de lavar."
    assert "Especificaciones" not in row["desc_es"]
    assert "Productos relacionados" not in row["desc_es"]
    assert row["details_es"] == "Color: Beige; Material: Poliéster; Número del artículo: 3220414"
    assert row["image_url"] == "https://www.action.com/images/3220414.jpg"
    assert set(row["field_sources"]) == {
        "sku", "name_es", "cat1_es", "cat2_es", "spec_es", "desc_es",
        "details_es", "product_url", "image_url",
    }


def test_live_breadcrumbs_and_description_boundary(tmp_path):
    html = """
    <html><head><title>Producto | Action</title></head><body>
      <nav data-testid="breadcrumbs" aria-label="Breadcrumbs">
        <button><span>Atrás</span></button>
        <a href="/es-es/c/vivienda/"><span data-testid="breadcrumb-label">Vivienda</span></a>
        <a href="/es-es/c/vivienda/accesorios/"><span data-testid="breadcrumb-label">Decoración</span></a>
        <div data-testid="breadcrumb-item"><p data-testid="breadcrumb-label">Producto</p></div>
      </nav>
      <section><h2 data-testid="product-description-title">Descripción</h2>
        <div data-testid="product-description"><ul><li>Descripción breve</li></ul><div>Texto completo</div></div>
      </section>
      <aside><span data-testid="product-tag">Una opción más sostenible</span><h3>Contenido sostenible</h3></aside>
      <section><h2>Especificaciones</h2><table data-testid="productions-specifications-table"><tr><td>Número del artículo</td><td>3220414</td></tr></table></section>
    </body></html>
    """
    row = parse_detail_html(
        html,
        "https://www.action.com/es-es/p/3220414/producto/",
        "3220414",
        tmp_path / "adaptive.sqlite3",
    )
    assert row["cat1_es"] == "Vivienda"
    assert row["cat2_es"] == "Decoración"
    assert row["desc_es"] == "Descripción breve Texto completo"
    assert "sostenible" not in row["desc_es"]
    assert all(source in {"PRIMARY", "ADAPTIVE", "NOT_FOUND", "INVALID"}
               for source in row["field_sources"].values())


def test_description_and_specification_may_be_missing_without_fabrication(tmp_path):
    html = (FIXTURES / "missing_optional.html").read_text(encoding="utf-8")
    url = "https://www.action.com/es-es/p/3224001/jarra-de-vidrio/"
    row = parse_detail_html(html, url, "3224001", tmp_path / "adaptive.sqlite3")
    validation = validate_detail(row, target_sku="3224001", url=url, html=html)
    assert validation.valid, validation.reasons
    assert row["desc_es"] == ""
    assert row["spec_es"] == ""
    assert row["details_es"] == ""
    assert row["field_sources"]["desc_es"] == "NOT_FOUND"
    assert row["field_sources"]["spec_es"] == "NOT_FOUND"
    assert row["field_sources"]["details_es"] == "NOT_FOUND"


def test_renamed_semantic_container_uses_adaptive_locator(tmp_path, normal_html):
    storage = tmp_path / "adaptive.sqlite3"
    first = parse_detail_html(normal_html, URL, SKU, storage)
    changed = normal_html.replace(
        'data-testid="productions-specifications-table"', 'data-testid="specification-grid"'
    ).replace('data-testid="main-image"', 'data-testid="hero-photo"')
    second = parse_detail_html(changed, URL, SKU, storage)
    assert first["details_es"]
    assert second["details_es"] == first["details_es"]
    assert second["field_sources"]["details_es"] == "ADAPTIVE"
    assert second["field_sources"]["image_url"] == "ADAPTIVE"
    assert any(item["adaptive_used"] for item in second["adaptive_usage"])


def test_sku_mismatch_is_invalid_and_never_looks_successful(tmp_path, normal_html):
    bad = normal_html.replace("3220414</td>", "9999999</td>")
    row = parse_detail_html(bad, URL, SKU, tmp_path / "adaptive.sqlite3")
    validation = validate_detail(row, target_sku=SKU, url=URL, html=bad)
    assert not validation.valid
    assert "DETAIL_SKU_MISMATCH" in validation.reasons
    assert row["sku"] == "9999999"


def test_challenge_html_is_not_extracted_or_saved_as_product_data(tmp_path):
    html = (FIXTURES / "challenge.html").read_text(encoding="utf-8")
    row = parse_detail_html(html, URL, SKU, tmp_path / "adaptive.sqlite3")
    validation = validate_detail(row, target_sku=SKU, url=URL, html=html)
    assert row["challenge_detected"] is True
    assert row["name_es"] == ""
    assert not validation.valid
    assert "CHALLENGE_PAGE" in validation.reasons


def test_description_stops_before_next_major_section(tmp_path, normal_html):
    row = parse_detail_html(normal_html, URL, SKU, tmp_path / "adaptive.sqlite3")
    assert "Colcha suave" in row["desc_es"]
    assert "Especificaciones" not in row["desc_es"]
    assert "Color: Beige" not in row["desc_es"]
    assert "Productos relacionados" not in row["desc_es"]


def test_specs_are_deterministic_key_value_pairs(tmp_path, normal_html):
    row = parse_detail_html(normal_html, URL, SKU, tmp_path / "adaptive.sqlite3")
    pieces = row["details_es"].split("; ")
    assert pieces == [
        "Color: Beige", "Material: Poliéster", "Número del artículo: 3220414",
    ]


def test_comparator_marks_normalized_match_and_invalid_without_copying_values():
    left = {"name_es": "Manta  Azul", "sku": "1"}
    right = {"name_es": "manta azul", "sku": "2"}
    compared = compare_fields(
        left, right, legacy_valid=True, scrapling_valid=False,
        scrapling_invalid_fields={"sku"}, global_invalid=False,
    )
    assert compared[0]["legacy_value"] == "1"
    assert compared[0]["scrapling_value"] == "2"
    assert compared[0]["result"] == "INVALID"
    assert compared[1]["result"] == "NORMALIZED_MATCH"


def test_sampling_is_reproducible_and_stratified():
    categories = ["A", "B", "C"]
    rows = [
        {"sku": str(index * 10 + i + 1), "cat1_es": cat,
         "product_url": f"https://www.action.com/es-es/p/{index * 10 + i + 1}/x/",
         "name_es": "Product", "desc_es": "Description", "spec_es": "10 cm", "details_es": "Color: Azul"}
        for index, cat in enumerate(categories) for i in range(5)
    ]
    first, meta1 = select_sample(rows, categories=categories, limit=6, seed=20260924)
    second, meta2 = select_sample(rows, categories=categories, limit=6, seed=20260924)
    assert [row["sku"] for row in first] == [row["sku"] for row in second]
    assert meta1["category_counts"] == {"A": 2, "B": 2, "C": 2}
    assert meta1 == meta2


def test_sampling_diagnostics_retain_excluded_sku_evidence():
    rows = [
        {"sku": "1001", "cat1_es": "A", "product_url": "https://www.action.com/es-es/p/1001/item/"},
        {"sku": "1002", "cat1_es": "A", "product_url": "https://www.action.com/es-es/"},
        {"sku": "1003", "cat1_es": "", "product_url": "https://www.action.com/es-es/p/1003/item/"},
    ]
    selected, diagnostics = select_sample(rows, categories=["A"], limit=1, seed=20260924)
    assert [row["sku"] for row in selected] == ["1001"]
    assert diagnostics["excluded"] == {"invalid_product_url": 1, "unknown_or_blank_category": 1}
    assert {row["sku"] for row in diagnostics["excluded_rows"]} == {"1002", "1003"}


class FakePage:
    url = URL

    def __init__(self, html):
        self.html = html
        self.main_frame = object()
        self.handlers = []
        self.content_calls = 0
        self.eval_calls = 0
        self.wait_calls = 0

    def on(self, name, fn):
        self.handlers.append((name, fn))

    def remove_listener(self, name, fn):
        self.handlers = [(n, f) for n, f in self.handlers if f is not fn]

    def title(self):
        return "Colcha Deluxe | Action"

    def locator(self, selector):
        class Body:
            def inner_text(self, timeout=None): return ""
        return Body()

    def wait_for_selector(self, selector, timeout=None):
        self.wait_calls += 1

    def evaluate(self, js, url):
        self.eval_calls += 1
        return {
            "sku": SKU, "name_es": "Colcha Deluxe", "cat1_es": "Vivienda",
            "cat2_es": "Mantas de viaje y mantas", "spec_es": "220 x 240 cm | varios colores",
            "current_price": "19,95 €", "original_price": "", "discount": "",
            "desc_es": "Colcha suave para una cama amplia. Fácil de lavar.",
            "details_es": "Color: Beige; Material: Poliéster; Número del artículo: 3220414",
            "product_url": url, "image_url": "https://www.action.com/images/3220414.jpg", "raw_tags": "",
        }

    def content(self):
        self.content_calls += 1
        return self.html


class FakeBrowser:
    def __init__(self, page, access, response_statuses=()):
        self.page = page
        self.access_controller = access
        self.goto_calls = []
        self.sleep_calls = 0
        self.response_statuses = response_statuses

    def goto(self, url, detail_mode=False):
        self.goto_calls.append((url, detail_mode))
        for status in self.response_statuses:
            request = type("Request", (), {"is_navigation_request": lambda self: True})()
            response = type("Response", (), {
                "request": request,
                "frame": self.page.main_frame,
                "url": url,
                "status": status,
            })()
            for _, handler in list(self.page.handlers):
                handler(response)
        return True

    def sleep(self):
        self.sleep_calls += 1


def test_detail_session_warmup_uses_the_same_page_and_records_success(normal_html):
    page = FakePage(normal_html)
    access = AccessController()
    browser = FakeBrowser(page, access)
    event = _warm_detail_session(browser, {"site": {"base_url": "https://www.action.com/es-es"}})
    assert event["attempted"] is True
    assert event["navigation_calls"] == 1
    assert event["navigation_ok"] is True
    assert event["access_state_before"] == "NORMAL"
    assert event["access_state_after"] == "NORMAL"
    assert browser.goto_calls == [("https://www.action.com/es-es", False)]


def test_detail_session_warmup_fails_closed_without_url(normal_html):
    page = FakePage(normal_html)
    access = AccessController()
    browser = FakeBrowser(page, access)
    event = _warm_detail_session(browser, {"site": {}})
    assert event["attempted"] is False
    assert event["navigation_calls"] == 0
    assert event["error_type"] == "SESSION_WARMUP_URL_MISSING"
    assert browser.goto_calls == []


def test_shadow_uses_exactly_one_navigation_and_one_content_snapshot(tmp_path, normal_html):
    page = FakePage(normal_html)
    access = AccessController()
    browser = FakeBrowser(page, access)
    sample = {"sku": SKU, "cat1_es": "Vivienda", "product_url": URL}
    result, _, _, event, stop = collect_one(
        browser, access, sample,
        adaptive_db=tmp_path / "adaptive.sqlite3", html_dir=tmp_path / "html",
    )
    assert not stop
    assert result["navigation_ok"] is True
    assert result["scrapling_valid"] is True
    assert len(browser.goto_calls) == 1
    assert browser.goto_calls[0] == (URL, True)
    assert page.content_calls == 1
    assert page.eval_calls == 1
    assert event["navigation_calls"] == 1


def test_intermediate_http_403_followed_by_200_is_retained_as_evidence(tmp_path, normal_html):
    page = FakePage(normal_html)
    access = AccessController()
    browser = FakeBrowser(page, access, response_statuses=(403, 200))
    sample = {"sku": SKU, "cat1_es": "Vivienda", "product_url": URL}
    result, comparisons, adaptive, event, stop = collect_one(
        browser, access, sample,
        adaptive_db=tmp_path / "adaptive.sqlite3", html_dir=tmp_path / "html",
    )
    assert stop is False
    assert result["error_type"] == ""
    assert result["http_status"] == 200
    assert result["final_http_status"] == 200
    assert result["intermediate_http_statuses"] == "403"
    assert result["intermediate_restriction_detected"] is True
    assert result["access_state"] == "NORMAL"
    assert browser.goto_calls == [(URL, True)]
    assert page.eval_calls == 1
    assert page.content_calls == 1
    assert comparisons
    assert adaptive
    assert [entry["status"] for entry in event["main_frame_responses"]] == [403, 200]


def test_terminal_http_403_stops_shadow_before_parsing(tmp_path, normal_html):
    page = FakePage(normal_html)
    access = AccessController()
    browser = FakeBrowser(page, access, response_statuses=(403,))
    sample = {"sku": SKU, "cat1_es": "Vivienda", "product_url": URL}
    result, comparisons, adaptive, event, stop = collect_one(
        browser, access, sample,
        adaptive_db=tmp_path / "adaptive.sqlite3", html_dir=tmp_path / "html",
    )
    assert stop is True
    assert result["error_type"] == "HTTP_403"
    assert result["http_status"] == 403
    assert result["final_http_status"] == 403
    assert result["access_state"] == "COOLDOWN"
    assert result["intermediate_http_statuses"] == ""
    assert browser.goto_calls == [(URL, True)]
    assert page.eval_calls == 0
    assert page.content_calls == 0
    assert comparisons == []
    assert adaptive == []
    assert event["terminal_restriction_status"] == 403


def test_sample_result_contract_has_required_columns():
    required = {
        "sku", "cat1_es", "product_url", "navigation_ok", "challenge_detected", "access_state",
        "legacy_valid", "scrapling_valid", "legacy_name_es", "scrapling_name_es",
        "legacy_cat1_es", "scrapling_cat1_es", "cat1_result", "cat1_source",
        "legacy_cat2_es", "scrapling_cat2_es", "cat2_result", "cat2_source", "name_result",
        "name_source", "legacy_spec_es", "scrapling_spec_es", "spec_result", "spec_source",
        "legacy_desc_es", "scrapling_desc_es", "desc_result", "desc_source", "legacy_details_es",
        "scrapling_details_es", "details_result", "details_source", "sku_validation", "adaptive_used",
        "html_saved", "final_verdict", "error_type",
    }
    assert required.issubset(set(RESULT_FIELDS))


def test_restricted_navigation_is_not_counted_as_normal_or_parser_failure():
    result = {
        "navigation_ok": True,
        "challenge_detected": False,
        "http_status": "200",
        "access_state": "COOLDOWN",
        "error_type": "HTTP_403",
        "scrapling_valid": False,
    }
    assert not _is_normal_page_result(result)
    assert not _is_parser_failure(result)


def test_normal_page_with_invalid_scrapling_result_is_parser_failure():
    result = {
        "navigation_ok": True,
        "challenge_detected": False,
        "http_status": "200",
        "access_state": "NORMAL",
        "error_type": "DESCRIPTION_CONTAMINATION",
        "scrapling_valid": False,
    }
    assert _is_normal_page_result(result)
    assert _is_parser_failure(result)


def test_intermediate_restriction_does_not_invalidate_final_normal_page():
    result = {
        "navigation_ok": True,
        "challenge_detected": False,
        "http_status": "200",
        "final_http_status": "200",
        "intermediate_http_statuses": "403,200",
        "intermediate_restriction_detected": True,
        "access_state": "NORMAL",
        "error_type": "",
        "scrapling_valid": True,
    }
    assert _is_normal_page_result(result)
    assert not _is_parser_failure(result)

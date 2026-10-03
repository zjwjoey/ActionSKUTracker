"""Headful, single-page, one-navigation-per-SKU parser shadow runner.

Every network request goes through the existing BrowserSession and
AccessController. All run outputs, adaptive data, and optional HTML snapshots
are restricted to this worktree's runtime/experiments directory.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import importlib.metadata as metadata
import json
import math
from pathlib import Path
import re
import sys
import time
from typing import Any

from ...config import load_settings
from ...monitor.listing import CATEGORY_LABELS, BADGE_ENTRY_KEYS
from ...products.parser import _EXTRACT_JS, _normalize_detail, is_bad_title
from ...services.access import AccessController, AccessState, CollectionBlocked
from ...services.browser import BrowserSession, is_challenge
from .comparator import COMPARE_FIELDS, compare_fields, final_verdict
from .parser import FIELD_NAMES, parse_detail_html
from .sampling import select_sample
from .validator import validate_detail


_PRODUCT_URL_SKU = re.compile(r"/p/(\d+)(?:/|$)", re.I)
_CHALLENGE_TEXT = re.compile(
    r"cf-challenge|cf-turnstile|captcha|verify you are human|checking your browser|"
    r"just a moment|un momento",
    re.I,
)

RESULT_FIELDS = (
    "sku", "cat1_es", "product_url", "navigation_ok", "challenge_detected", "http_status",
    "final_http_status", "intermediate_http_statuses", "intermediate_restriction_detected",
    "access_state", "legacy_valid", "scrapling_valid", "legacy_name_es", "scrapling_name_es",
    "legacy_cat1_es", "scrapling_cat1_es", "cat1_result", "cat1_source",
    "legacy_cat2_es", "scrapling_cat2_es", "cat2_result", "cat2_source",
    "name_result", "name_source", "legacy_spec_es", "scrapling_spec_es", "spec_result", "spec_source",
    "legacy_desc_es", "scrapling_desc_es", "desc_result", "desc_source", "legacy_details_es",
    "scrapling_details_es", "details_result", "details_source", "sku_validation", "adaptive_used",
    "html_saved", "final_verdict", "error_type",
)
_RESTRICTED_STATUSES = {"401", "403", "429"}
_ACCESS_FAILURES = {
    "HTTP_401", "HTTP_403", "HTTP_429", "CF_CHALLENGE",
    "DETAIL_ACCESS_INTERRUPTED", "NAVIGATION_FAILED",
}


def _is_normal_page_result(row: dict[str, Any]) -> bool:
    """Count only a normally loaded page with a non-restricted final response.

    A transient restricted response earlier in the same main-frame navigation
    is retained as evidence but is not enough to invalidate the final page.
    """
    return bool(
        row.get("navigation_ok")
        and not row.get("challenge_detected")
        and str(row.get("access_state") or "") == AccessState.NORMAL.value
        and str(row.get("http_status") or "") not in _RESTRICTED_STATUSES
        and str(row.get("error_type") or "") not in _ACCESS_FAILURES
    )


def _is_parser_failure(row: dict[str, Any]) -> bool:
    """Exclude access/navigation failures from Parser QA denominators and files."""
    if not _is_normal_page_result(row):
        return False
    return bool(row.get("error_type") or not row.get("scrapling_valid"))


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def load_current_source(source: Path, target_date: str) -> tuple[list[dict[str, str]], dict[str, Any], dict[str, Any]]:
    """Accept only a dated, QA-PASS, fully observed Presence snapshot."""
    report_path = source / "run_report.json"
    qa_path = source / "qa_report.json"
    coverage_path = source / "coverage.json"
    products_path = source / "products_normalized.csv"
    presence_path = source / "presence_evidence.csv"
    for path in (report_path, qa_path, coverage_path, products_path, presence_path):
        if not path.is_file():
            raise FileNotFoundError(f"CURRENT_SOURCE_FILE_MISSING: {path}")

    report = json.loads(report_path.read_text(encoding="utf-8"))
    qa = json.loads(qa_path.read_text(encoding="utf-8"))
    coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
    if report.get("run_date") != target_date:
        raise ValueError(f"SOURCE_DATE_MISMATCH: {report.get('run_date')} != {target_date}")
    if report.get("qa_state") != "PASS" or qa.get("state") != "PASS" or not qa.get("passed"):
        raise ValueError("SOURCE_QA_NOT_PASS")
    if not report.get("observation_complete") or report.get("presence_mode") != "FULL":
        raise ValueError("SOURCE_PRESENCE_NOT_FULL")
    if report.get("presence_access_state") != "NORMAL":
        raise ValueError("SOURCE_PRESENCE_ACCESS_NOT_NORMAL")
    if report.get("commit_status") not in {"FULL_COMMIT", "DRY_RUN"}:
        raise ValueError(f"SOURCE_COMMIT_STATE_INVALID: {report.get('commit_status')}")
    category_coverage = report.get("category_coverage") or {}
    expected_categories = [
        label for key, label in CATEGORY_LABELS.items() if key not in BADGE_ENTRY_KEYS
    ]
    missing = [label for label in expected_categories if category_coverage.get(label) is not True]
    if missing:
        raise ValueError(f"SOURCE_CATEGORY_COVERAGE_INCOMPLETE: {missing}")
    if coverage != category_coverage:
        raise ValueError("SOURCE_COVERAGE_REPORT_MISMATCH")

    rows = _read_csv(products_path)
    current = [row for row in rows if str(row.get("status") or "").upper() == "CURRENT"]
    skus = [str(row.get("sku") or "").strip() for row in current]
    if len(skus) != int(report.get("today_sku") or -1):
        raise ValueError(f"SOURCE_CURRENT_COUNT_MISMATCH: rows={len(skus)} report={report.get('today_sku')}")
    if not all(skus) or len(set(skus)) != len(skus):
        raise ValueError("SOURCE_CURRENT_SKU_IDENTITY_INVALID")
    presence_rows = _read_csv(presence_path)
    presence_skus = [str(row.get("sku") or "").strip() for row in presence_rows]
    if len(presence_skus) != len(set(presence_skus)):
        raise ValueError("SOURCE_PRESENCE_SKU_DUPLICATES")
    return current, report, qa


def _read_presence_events(source: Path) -> dict[str, str]:
    path = source / "sku_delta.csv"
    if not path.exists():
        return {}
    return {
        str(row.get("sku") or "").strip(): str(row.get("status") or row.get("event") or "").strip().upper()
        for row in _read_csv(path)
        if row.get("sku")
    }


def _safe_write_json(path: Path, data: Any) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    temp.replace(path)


def _write_csv(path: Path, rows: list[dict[str, Any]], headers: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = headers or (list(rows[0]) if rows else [])
    if not fieldnames:
        fieldnames = ["sku", "error_type"]
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temp.replace(path)


def _load_progress(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            value = json.loads(line)
            if isinstance(value, dict) and value.get("sku"):
                rows.append(value)
        except json.JSONDecodeError:
            continue
    return rows


def _load_csv_records(path: Path, *, bool_fields: set[str] | None = None) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    bool_fields = bool_fields or set()
    rows = _read_csv(path)
    for row in rows:
        for field in bool_fields & row.keys():
            row[field] = str(row[field]).strip().lower() in {"1", "true", "yes"}
    return rows


def _append_progress(path: Path, row: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        handle.flush()


def _warm_detail_session(browser: BrowserSession, cfg: dict[str, Any]) -> dict[str, Any]:
    """Establish the same site session used by the production collector.

    This is one bounded homepage navigation on the same persistent page that
    will later visit the sampled detail URLs. It is deliberately recorded
    separately from SKU results so a warm-up restriction cannot be mistaken
    for a product Parser failure.
    """
    site = cfg.get("site") or {}
    url = str(site.get("base_url") or "").strip()
    event: dict[str, Any] = {
        "url": url,
        "attempted": bool(url),
        "navigation_calls": 0,
        "navigation_ok": False,
        "challenge_detected": False,
        "error_type": "",
        "access_state_before": getattr(browser.access_controller, "state", AccessState.NORMAL).value,
    }
    if not url:
        event.update({"error_type": "SESSION_WARMUP_URL_MISSING", "access_state_after": event["access_state_before"]})
        return event
    event["navigation_calls"] = 1
    try:
        event["navigation_ok"] = bool(browser.goto(url, detail_mode=False))
    except CollectionBlocked as error:
        event["error_type"] = type(error).__name__
    except Exception as error:  # noqa: BLE001
        event["error_type"] = type(error).__name__
    try:
        event["page_title"] = browser.page.title()
        event["final_url"] = getattr(browser.page, "url", "")
    except Exception:
        event["page_title"] = ""
        event["final_url"] = ""
    event["challenge_detected"] = is_challenge(str(event.get("page_title") or ""))
    event["access_state_after"] = getattr(browser.access_controller, "state", AccessState.NORMAL).value
    if event["challenge_detected"] and not event["error_type"]:
        event["error_type"] = "CF_CHALLENGE"
    if not event["navigation_ok"] and not event["error_type"]:
        event["error_type"] = "SESSION_WARMUP_FAILED"
    return event


def _capture_legacy(browser: BrowserSession, url: str) -> dict[str, Any]:
    """Run only the existing extraction JS on the page already navigated to."""
    raw = browser.page.evaluate(_EXTRACT_JS, url)
    return _normalize_detail(raw or {}, url)


def _page_challenge(browser: BrowserSession, title: str) -> bool:
    if is_challenge(title):
        return True
    try:
        body = browser.page.locator("body").inner_text(timeout=3000)
    except Exception:
        body = ""
    return bool(_CHALLENGE_TEXT.search(body or ""))


def _empty_result(sku: str, category: str, url: str) -> dict[str, Any]:
    row = {field: "" for field in RESULT_FIELDS}
    row.update({
        "sku": sku,
        "cat1_es": category,
        "product_url": url,
        "navigation_ok": False,
        "challenge_detected": False,
        "access_state": "NORMAL",
        "legacy_valid": False,
        "scrapling_valid": False,
        "adaptive_used": False,
        "html_saved": False,
        "final_verdict": "INVALID",
    })
    return row


def _response_listener(page: Any, target_url: str) -> tuple[list[dict[str, Any]], Any]:
    responses: list[dict[str, Any]] = []

    def on_response(response: Any) -> None:
        try:
            request = response.request
            frame = response.frame
            if request.is_navigation_request() and frame == page.main_frame:
                responses.append({"url": response.url, "status": response.status})
        except Exception:
            return

    page.on("response", on_response)
    return responses, on_response


def collect_one(
    browser: BrowserSession,
    access: AccessController,
    sample: dict[str, Any],
    *,
    adaptive_db: Path,
    html_dir: Path,
    save_html: bool = True,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], bool]:
    """Collect one sample. Returns result, field rows, adaptive rows, event, stop."""
    sku = str(sample["sku"])
    category = str(sample.get("cat1_es") or "")
    url = str(sample.get("product_url") or "")
    result = _empty_result(sku, category, url)
    event: dict[str, Any] = {
        "sku": sku,
        "url": url,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "access_state_before": access.state.value,
        "controller_events_before": list(access.events),
        "navigation_calls": 0,
        "main_frame_responses": [],
    }
    if access.state not in (AccessState.NORMAL, AccessState.PROBE):
        result.update({"error_type": "DETAIL_ACCESS_INTERRUPTED", "access_state": access.state.value})
        event.update({"error_type": "DETAIL_ACCESS_INTERRUPTED", "access_state_after": access.state.value})
        return result, [], [], event, True

    page = browser.page
    responses, callback = _response_listener(page, url)
    navigation_ok = False
    nav_error = ""
    event["navigation_calls"] = 1
    try:
        navigation_ok = bool(browser.goto(url, detail_mode=True))
    except CollectionBlocked as error:
        nav_error = type(error).__name__
    except Exception as error:  # No navigation retry in this shadow runner.
        nav_error = "TIMEOUT" if "timeout" in type(error).__name__.lower() else type(error).__name__
    finally:
        try:
            page.remove_listener("response", callback)
        except Exception:
            pass

    title = ""
    try:
        title = page.title()
    except Exception:
        pass
    event["main_frame_responses"] = responses
    event["page_title"] = title
    event["navigation_ok"] = navigation_ok
    event["navigation_error"] = nav_error
    event["final_url"] = getattr(page, "url", "")
    # Playwright may report more than one main-frame navigation response for a
    # single goto (for example 403 -> 200 -> 200 while the site completes a
    # redirect/challenge transition). The last response is the terminal page
    # status. Earlier restricted responses remain evidence, but do not turn a
    # successfully loaded final page into an access failure.
    final_status = responses[-1]["status"] if responses else None
    intermediate_statuses = [r.get("status") for r in responses[:-1]]
    intermediate_restrictions = [
        status for status in intermediate_statuses if status in {401, 403, 429}
    ]
    status = final_status
    challenge = _page_challenge(browser, title) if navigation_ok else is_challenge(title)
    restricted_code = final_status if final_status in {401, 403, 429} else None
    restricted_status = restricted_code is not None

    if challenge and access.state == AccessState.NORMAL:
        access.record(challenge=True)
    if restricted_status and access.state == AccessState.NORMAL:
        access.record(status=restricted_code)
    result.update({
        "navigation_ok": navigation_ok,
        "challenge_detected": challenge,
        "http_status": status or "",
        "final_http_status": status or "",
        "intermediate_http_statuses": ",".join(str(value) for value in intermediate_statuses if value is not None),
        "intermediate_restriction_detected": bool(intermediate_restrictions),
        "access_state": access.state.value,
    })
    event.update({
        "final_http_status": final_status,
        "intermediate_http_statuses": intermediate_statuses,
        "intermediate_restriction_statuses": intermediate_restrictions,
        "terminal_restriction_status": restricted_code,
    })

    if nav_error:
        result["error_type"] = nav_error
    if challenge or restricted_status or access.state != AccessState.NORMAL or not navigation_ok:
        error_type = "CF_CHALLENGE" if challenge else ("HTTP_429" if restricted_code == 429
                                                        else "HTTP_403" if restricted_code == 403
                                                        else "HTTP_401" if restricted_code == 401
                                                        else nav_error or "NAVIGATION_FAILED")
        result["error_type"] = error_type
        event.update({
            "error_type": error_type,
            "challenge_detected": challenge,
            "access_state_after": access.state.value,
            "controller_events_after": list(access.events),
            "finished_at": datetime.now(timezone.utc).isoformat(),
        })
        return result, [], [], event, access.state != AccessState.NORMAL or restricted_status or challenge

    wait_error = ""
    try:
        page.wait_for_selector("h1", timeout=15000)
    except Exception as error:
        wait_error = type(error).__name__
    # A normal-page check precedes the only page.content() call. Challenge pages
    # are never parsed or saved as product HTML.
    if _page_challenge(browser, title):
        if access.state == AccessState.NORMAL:
            access.record(challenge=True)
        result.update({"challenge_detected": True, "access_state": access.state.value, "error_type": "CF_CHALLENGE"})
        event.update({"error_type": "CF_CHALLENGE", "challenge_detected": True,
                      "access_state_after": access.state.value, "finished_at": datetime.now(timezone.utc).isoformat()})
        return result, [], [], event, True

    normal_page = (
        bool(_PRODUCT_URL_SKU.search(str(getattr(page, "url", "") or url)))
        and not is_challenge(title)
        and not is_bad_title(title)
        and not restricted_status
    )
    legacy: dict[str, Any] = {}
    parsed: dict[str, Any] = {}
    legacy_validation = None
    scrapling_validation = None
    comparison_rows: list[dict[str, Any]] = []
    adaptive_rows: list[dict[str, Any]] = []
    html = ""
    parser_error = ""
    try:
        legacy = _capture_legacy(browser, url)
        html = page.content()
        parsed = parse_detail_html(html, url, sku, adaptive_db)
        legacy_sources = {
            field: ("PRIMARY" if legacy.get(field) not in (None, "") else "NOT_FOUND")
            for field in FIELD_NAMES
        }
        legacy_validation = validate_detail(
            legacy, target_sku=sku, url=url, html=html,
            field_sources=legacy_sources, page_title=title,
        )
        scrapling_validation = validate_detail(
            parsed, target_sku=sku, url=url, html=html,
            field_sources=parsed.get("field_sources"), page_title=title,
        )
        identity_invalid = any(
            reason in {"URL_SKU_MISMATCH", "SKU_MISMATCH", "DETAIL_SKU_MISMATCH", "CHALLENGE_PAGE"}
            for reason in scrapling_validation.reasons
        )
        comparison_rows = compare_fields(
            legacy, parsed,
            legacy_valid=legacy_validation.valid,
            scrapling_valid=scrapling_validation.valid,
            legacy_invalid_fields=set(legacy_validation.invalid_fields),
            scrapling_invalid_fields=set(scrapling_validation.invalid_fields),
            global_invalid=identity_invalid,
        )
        for comparison in comparison_rows:
            comparison.update({"sku": sku, "cat1_es": category,
                               "scrapling_source": (parsed.get("field_sources") or {}).get(comparison["field"], "NOT_FOUND")})
        adaptive_rows = [dict(item, sku=sku, cat1_es=category) for item in parsed.get("adaptive_usage", [])]
    except Exception as error:
        parser_error = f"{type(error).__name__}: {error}"
        result["error_type"] = "PARSER_ERROR"

    if parser_error:
        scraps_valid = False
        legacy_valid = bool(legacy_validation and legacy_validation.valid)
        verdict = "INVALID"
        field_results = {field: "INVALID" for field in COMPARE_FIELDS}
        field_sources = parsed.get("field_sources") or {}
        adaptive_rows = [dict(item, sku=sku, cat1_es=category) for item in parsed.get("adaptive_usage", [])]
        if html and normal_page:
            comparison_rows = [{
                "sku": sku, "cat1_es": category, "field": field,
                "legacy_value": legacy.get(field, ""), "scrapling_value": "",
                "result": "INVALID", "legacy_valid": legacy_valid,
                "scrapling_valid": False, "scrapling_source": field_sources.get(field, "NOT_FOUND"),
            } for field in COMPARE_FIELDS]
    else:
        legacy_valid = bool(legacy_validation and legacy_validation.valid)
        scraps_valid = bool(scrapling_validation and scrapling_validation.valid)
        verdict = final_verdict(comparison_rows, legacy_valid=legacy_valid, scrapling_valid=scraps_valid)
        field_results = {row["field"]: row["result"] for row in comparison_rows}
        result["error_type"] = ";".join(scrapling_validation.reasons) if scrapling_validation and scrapling_validation.reasons else wait_error

    sources = parsed.get("field_sources") or {}
    for base, field in (
        ("name", "name_es"), ("cat1", "cat1_es"), ("cat2", "cat2_es"),
        ("spec", "spec_es"), ("desc", "desc_es"), ("details", "details_es"),
    ):
        result[f"legacy_{field}"] = legacy.get(field, "")
        result[f"scrapling_{field}"] = parsed.get(field, "")
        result[f"{base}_result"] = field_results.get(field, "INVALID")
        result[f"{base}_source"] = sources.get(field, "NOT_FOUND")
    result.update({
        "legacy_valid": legacy_valid,
        "scrapling_valid": scraps_valid,
        "legacy_name_es": legacy.get("name_es", ""),
        "scrapling_name_es": parsed.get("name_es", ""),
        "legacy_cat1_es": legacy.get("cat1_es", ""),
        "scrapling_cat1_es": parsed.get("cat1_es", ""),
        "legacy_cat2_es": legacy.get("cat2_es", ""),
        "scrapling_cat2_es": parsed.get("cat2_es", ""),
        "legacy_spec_es": legacy.get("spec_es", ""),
        "scrapling_spec_es": parsed.get("spec_es", ""),
        "legacy_desc_es": legacy.get("desc_es", ""),
        "scrapling_desc_es": parsed.get("desc_es", ""),
        "legacy_details_es": legacy.get("details_es", ""),
        "scrapling_details_es": parsed.get("details_es", ""),
        "name_source": sources.get("name_es", "NOT_FOUND"),
        "spec_source": sources.get("spec_es", "NOT_FOUND"),
        "desc_source": sources.get("desc_es", "NOT_FOUND"),
        "details_source": sources.get("details_es", "NOT_FOUND"),
        "sku_validation": "PASS" if scraps_valid and parsed.get("sku") == sku else "FAIL",
        "adaptive_used": any(item.get("adaptive_used") for item in adaptive_rows),
        "final_verdict": verdict,
    })
    identity_errors = {"URL_SKU_MISMATCH", "SKU_MISMATCH", "DETAIL_SKU_MISMATCH"}
    validation_reasons = set((scrapling_validation.reasons if scrapling_validation else []))
    missing_field = any(not str(parsed.get(field) or "").strip() for field in FIELD_NAMES if field not in {"sku", "product_url"})
    needs_html = bool(
        result["adaptive_used"]
        or verdict != "EXACT_MATCH"
        or (scrapling_validation and not scrapling_validation.valid)
        or missing_field
        or parser_error
    )
    current_url = str(getattr(page, "url", "") or url)
    current_url_sku = _PRODUCT_URL_SKU.search(current_url)
    normal_page = normal_page and bool(current_url_sku and current_url_sku.group(1) == sku)
    saved_html = False
    if save_html and needs_html and normal_page and html:
        html_dir.mkdir(parents=True, exist_ok=True)
        html_path = html_dir / f"{sku}.html"
        html_path.write_text(html, encoding="utf-8")
        saved_html = True
    result["html_saved"] = saved_html
    result["navigation_ok"] = True
    result["challenge_detected"] = False
    result["http_status"] = status or ""
    result["access_state"] = access.state.value
    event.update({
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "access_state_after": access.state.value,
        "controller_events_after": list(access.events),
        "legacy_valid": legacy_valid,
        "scrapling_valid": scraps_valid,
        "validation_reasons": scrapling_validation.reasons if scrapling_validation else ["PARSER_ERROR"],
        "field_results": field_results,
        "adaptive_used": result["adaptive_used"],
        "html_saved": saved_html,
        "final_verdict": verdict,
        "parser_error": parser_error,
        "wait_for_h1_error": wait_error,
    })
    if access.state != AccessState.NORMAL:
        result["error_type"] = result.get("error_type") or "DETAIL_ACCESS_INTERRUPTED"
        return result, comparison_rows, adaptive_rows, event, True
    # Honor the configured, production-equivalent low-frequency pause.
    browser.sleep()
    return result, comparison_rows, adaptive_rows, event, False


def _valid_100_extension(previous_run: Path, source_run_id: str) -> None:
    report_path = previous_run / "run_report.json"
    if not report_path.is_file():
        raise ValueError("100_SKU_REQUIRES_A_COMPLETED_30_SKU_RUN")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("source_run_id") != source_run_id or report.get("planned_skus") != 30:
        raise ValueError("100_SKU_PREVIOUS_RUN_SOURCE_OR_SAMPLE_MISMATCH")
    if report.get("run_status") != "COMPLETED" or report.get("final_access_state") != "NORMAL":
        raise ValueError("100_SKU_PREVIOUS_RUN_NOT_COMPLETE_OR_ACCESS_INTERRUPTED")
    if report.get("cf_challenge_count") or report.get("http_403_count") or report.get("http_429_count"):
        raise ValueError("100_SKU_PREVIOUS_RUN_HAS_ACCESS_RESTRICTIONS")
    normal = int(report.get("normal_page_count") or 0)
    valid = int(report.get("scrapling_valid_count") or 0)
    if normal <= 0 or valid / normal < 0.95:
        raise ValueError("100_SKU_PREVIOUS_RUN_SCRAPLING_VALID_RATE_BELOW_95_PERCENT")
    if int(report.get("sku_mismatch_count") or 0) or int(report.get("field_pollution_count") or 0):
        raise ValueError("100_SKU_PREVIOUS_RUN_HAS_SKU_MISMATCH_OR_FIELD_POLLUTION")
    if report.get("production_write_detected") is not False:
        raise ValueError("100_SKU_PREVIOUS_RUN_PRODUCTION_WRITE_NOT_CLEARED")


def _decorate_presence(rows: list[dict[str, str]], source: Path) -> list[dict[str, str]]:
    events = _read_presence_events(source)
    result = []
    for row in rows:
        item = dict(row)
        item["presence_event"] = events.get(str(row.get("sku") or ""), "")
        result.append(item)
    return result


def _write_sample(path: Path, sample: list[dict[str, Any]]) -> None:
    fields = [
        "sku", "cat1_es", "product_url", "name_es", "presence_event", "presence_source",
        "detail_status", "detail_fields_source", "raw_tags", "spec_es", "desc_es", "details_es", "sample_strata",
    ]
    _write_csv(path, sample, fields)


def run_shadow(
    *,
    source: Path,
    target_date: str,
    limit: int,
    seed: int,
    runtime_root: Path,
    browser: BrowserSession | None = None,
    save_html: bool = True,
    requested_skus: set[str] | None = None,
    category: str | None = None,
    run_id: str | None = None,
    resume: bool = False,
    previous_run: Path | None = None,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rows, source_report, source_qa = load_current_source(source, target_date)
    rows = _decorate_presence(rows, source)
    categories = [label for key, label in CATEGORY_LABELS.items() if key not in BADGE_ENTRY_KEYS]
    sample, sample_diagnostics = select_sample(
        rows, categories=categories, limit=limit, seed=seed,
        requested_skus=requested_skus, requested_category=category,
    )
    if len(sample) < limit and not requested_skus:
        raise ValueError(f"INSUFFICIENT_SAMPLING_COVERAGE: {len(sample)} of {limit}")
    if limit > 30:
        if not previous_run:
            raise ValueError("100_SKU_REQUIRES_PREVIOUS_30_SKU_GATE")
        _valid_100_extension(previous_run, str(source_report.get("run_id") or ""))

    run_id = run_id or f"shadow_{target_date.replace('-', '')}_{datetime.now(timezone.utc).strftime('%H%M%S')}_{seed}"
    run_dir = runtime_root / "runs" / run_id
    if resume:
        if not run_dir.is_dir():
            raise FileNotFoundError(f"RESUME_RUN_NOT_FOUND: {run_dir}")
        prior_report_path = run_dir / "run_report.json"
        if prior_report_path.is_file():
            prior_report = json.loads(prior_report_path.read_text(encoding="utf-8"))
            if prior_report.get("stop_reason") == "DETAIL_ACCESS_INTERRUPTED" or prior_report.get("run_status") == "DETAIL_ACCESS_INTERRUPTED":
                raise ValueError("RESUME_AFTER_ACCESS_INTERRUPTION_FORBIDDEN")
        prior_sample_path = run_dir / "sample_skus.csv"
        if prior_sample_path.is_file():
            old_skus = [str(row.get("sku") or "") for row in _read_csv(prior_sample_path)]
            new_skus = [str(row.get("sku") or "") for row in sample]
            if old_skus != new_skus:
                raise ValueError("RESUME_SAMPLE_MISMATCH")
    else:
        run_dir.mkdir(parents=True, exist_ok=False)
    html_dir = run_dir / "html"
    html_dir.mkdir(parents=True, exist_ok=True)
    adaptive_db = runtime_root / "adaptive" / "adaptive_storage.sqlite3"
    progress_path = run_dir / "detail_shadow_progress.jsonl"
    _write_sample(run_dir / "sample_skus.csv", sample)
    _write_csv(run_dir / "sample_exclusions.csv", sample_diagnostics.get("excluded_rows", []),
               ["sku", "cat1_es", "product_url", "reason"])
    if not resume:
        for source_name, output_name in (
            ("run_report.json", "source_run_report.json"),
            ("qa_report.json", "source_qa_report.json"),
            ("coverage.json", "source_coverage.json"),
        ):
            (run_dir / output_name).write_bytes((source / source_name).read_bytes())
    _write_csv(run_dir / "sample_presence_evidence.csv", [
        row for row in _read_csv(source / "presence_evidence.csv")
        if str(row.get("sku") or "") in {str(item.get("sku") or "") for item in sample}
    ])

    existing = _load_progress(progress_path) if resume else []
    done = {str(row.get("sku") or "") for row in existing}
    results = list(existing)
    all_comparisons = _load_csv_records(run_dir / "field_comparison.csv") if resume else []
    all_adaptive = _load_csv_records(
        run_dir / "adaptive_usage.csv",
        bool_fields={"primary_found", "adaptive_used", "found"},
    ) if resume else []
    events_path = run_dir / "access_events.json"
    access_events = json.loads(events_path.read_text(encoding="utf-8")) if resume and events_path.is_file() else []
    warmup_path = run_dir / "session_warmup.json"
    session_warmup = json.loads(warmup_path.read_text(encoding="utf-8")) if resume and warmup_path.is_file() else None
    if not browser:
        if cfg is None:
            cfg = load_settings()
        exp_profile = runtime_root / "browser_profile" / "action_es"
        cookies_copy = runtime_root / "browser_profile" / "cookies.json"
        profile_manifest = runtime_root / "browser_profile" / "profile_copy_manifest.json"
        if not exp_profile.is_dir() or not profile_manifest.is_file():
            raise RuntimeError("EXPERIMENT_BROWSER_PROFILE_COPY_MISSING")
        cfg["browser"] = dict(cfg["browser"])
        cfg["browser"]["headless"] = False
        cfg["browser"]["profile_dir"] = exp_profile
        cfg["browser"]["cookies_path"] = cookies_copy if cookies_copy.exists() else None
        access = AccessController(
            cooldown_seconds=cfg["browser"].get("cooldown_seconds", 60),
            degraded_recovery_successes=cfg["browser"].get("degraded_recovery_successes", 3),
        )
        browser_context = BrowserSession(cfg["browser"], cfg["browser"].get("cookies_path"),
                                         access_controller=access, keep_open=False)
    else:
        cfg = cfg or load_settings()
        access = browser.access_controller or AccessController(
            cooldown_seconds=cfg["browser"].get("cooldown_seconds", 60),
            degraded_recovery_successes=cfg["browser"].get("degraded_recovery_successes", 3),
        )
        browser.access_controller = access
        browser_context = browser

    initial_access = access.state.value
    own_context = browser is None
    active_browser = browser_context
    started = datetime.now(timezone.utc).isoformat()
    stop_reason = ""
    try:
        if own_context:
            active_browser.start()
        if session_warmup is None:
            session_warmup = _warm_detail_session(active_browser, cfg)
            _safe_write_json(run_dir / "session_warmup.json", session_warmup)
        if not session_warmup.get("navigation_ok"):
            stop_reason = (
                "DETAIL_ACCESS_INTERRUPTED"
                if access.state != AccessState.NORMAL or session_warmup.get("challenge_detected")
                else "SESSION_WARMUP_FAILED"
            )
        for item in sample:
            if stop_reason:
                break
            sku = str(item["sku"])
            if sku in done:
                continue
            # A single AccessController recovery probe is permitted after its
            # configured cooldown. The caller must supply PROBE state explicitly;
            # any restricted response during this navigation transitions to
            # BLOCKED and stops the run before another SKU can be requested.
            if access.state not in (AccessState.NORMAL, AccessState.PROBE):
                stop_reason = "DETAIL_ACCESS_INTERRUPTED"
                break
            result, comparisons, adaptive_rows, event, stop = collect_one(
                active_browser, access, item, adaptive_db=adaptive_db,
                html_dir=html_dir, save_html=save_html,
            )
            event.update({"access_state_after": access.state.value,
                          "controller_events_after": list(access.events)})
            results.append(result)
            all_comparisons.extend(comparisons)
            all_adaptive.extend(adaptive_rows)
            access_events.append(event)
            _append_progress(progress_path, result)
            # Refresh evidence after every SKU so a headed browser interruption
            # still leaves a complete audit trail.
            _write_csv(run_dir / "detail_shadow_results.csv", results, list(RESULT_FIELDS))
            _write_csv(run_dir / "field_comparison.csv", all_comparisons, [
                "sku", "cat1_es", "field", "legacy_value", "scrapling_value", "result",
                "legacy_valid", "scrapling_valid", "scrapling_source",
            ])
            _write_csv(run_dir / "adaptive_usage.csv", all_adaptive, [
                "sku", "cat1_es", "field", "semantic_id", "selector", "source",
                "primary_found", "adaptive_used", "found",
            ])
            _safe_write_json(run_dir / "access_events.json", access_events)
            if stop:
                stop_reason = "DETAIL_ACCESS_INTERRUPTED" if access.state != AccessState.NORMAL or result.get("challenge_detected") else "NAVIGATION_STOPPED"
                break
    except KeyboardInterrupt:
        stop_reason = "DETAIL_ACCESS_INTERRUPTED" if access.state != AccessState.NORMAL else "OPERATOR_INTERRUPTED"
    finally:
        if own_context:
            active_browser.close()

    # Include already persisted rows if this invocation resumed a run.
    all_rows = results
    normal_count = sum(_is_normal_page_result(row) for row in all_rows)
    scrapling_valid_count = sum(bool(row.get("scrapling_valid")) for row in all_rows)
    challenge_count = sum(bool(row.get("challenge_detected")) or row.get("error_type") == "CF_CHALLENGE" for row in all_rows)
    http_403_count = sum(str(row.get("final_http_status") or row.get("http_status")) == "403" or row.get("error_type") == "HTTP_403" for row in all_rows)
    http_429_count = sum(str(row.get("final_http_status") or row.get("http_status")) == "429" or row.get("error_type") == "HTTP_429" for row in all_rows)
    intermediate_403_count = sum(
        403 in {int(value) for value in str(row.get("intermediate_http_statuses") or "").split(",") if value.strip().isdigit()}
        for row in all_rows
    )
    validation_errors = [reason for event in access_events for reason in event.get("validation_reasons", [])]
    sku_mismatch_count = sum("SKU_MISMATCH" in reason for reason in validation_errors)
    pollution_tokens = ("SPEC_CONTAMINATION", "DESCRIPTION_CONTAMINATION", "DETAILS_NOT_KEY_VALUE")
    pollution_count = sum(any(token in reason for token in pollution_tokens) for reason in validation_errors)
    if not stop_reason and len({str(row.get("sku")) for row in all_rows}) >= len(sample):
        run_status = "COMPLETED"
    elif stop_reason == "DETAIL_ACCESS_INTERRUPTED":
        run_status = "DETAIL_ACCESS_INTERRUPTED"
    else:
        run_status = "INCOMPLETE"
    _write_csv(run_dir / "detail_shadow_results.csv", all_rows, list(RESULT_FIELDS))
    _write_csv(run_dir / "field_comparison.csv", all_comparisons, [
        "sku", "cat1_es", "field", "legacy_value", "scrapling_value", "result",
        "legacy_valid", "scrapling_valid", "scrapling_source",
    ])
    _write_csv(run_dir / "adaptive_usage.csv", all_adaptive, [
        "sku", "cat1_es", "field", "semantic_id", "selector", "source",
        "primary_found", "adaptive_used", "found",
    ])
    _write_csv(run_dir / "parser_failures.csv", [
        {"sku": row.get("sku"), "cat1_es": row.get("cat1_es"), "error_type": row.get("error_type"),
         "legacy_valid": row.get("legacy_valid"), "scrapling_valid": row.get("scrapling_valid"),
         "final_verdict": row.get("final_verdict")}
        for row in all_rows if _is_parser_failure(row)
    ], ["sku", "cat1_es", "error_type", "legacy_valid", "scrapling_valid", "final_verdict"])
    _safe_write_json(run_dir / "detail_shadow_results.json", all_rows)
    _safe_write_json(run_dir / "access_events.json", access_events)
    run_report = {
        "run_id": run_id,
        "target_date": target_date,
        "started_at": started,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "dry_run": True,
        "commit_mode_available": False,
        "production_write_detected": False,
        "worktree": str(Path(__file__).resolve().parents[4]),
        "source_run_id": source_report.get("run_id"),
        "source_date": source_report.get("run_date"),
        "source_qa_state": source_report.get("qa_state"),
        "today_current_source": str(source.resolve()),
        "current_count": int(source_report.get("today_sku") or 0),
        "planned_skus": len(sample),
        "attempted_skus": len(all_rows),
        "navigation_calls": sum(int((event.get("navigation_calls") or 0)) for event in access_events),
        "normal_page_count": normal_count,
        "cf_challenge_count": challenge_count,
        "http_403_count": http_403_count,
        "http_429_count": http_429_count,
        "intermediate_403_count": intermediate_403_count,
        "final_access_state": access.state.value,
        "initial_access_state": initial_access,
        "detail_access_interrupted": stop_reason == "DETAIL_ACCESS_INTERRUPTED",
        "stop_reason": stop_reason,
        "session_warmup": session_warmup or {},
        "session_warmup_ok": bool(session_warmup and session_warmup.get("navigation_ok")),
        "legacy_valid_count": sum(bool(row.get("legacy_valid")) for row in all_rows),
        "scrapling_valid_count": scrapling_valid_count,
        "legacy_valid_rate": round(sum(bool(row.get("legacy_valid")) for row in all_rows) / normal_count, 4) if normal_count else None,
        "scrapling_valid_rate": round(scrapling_valid_count / normal_count, 4) if normal_count else None,
        "primary_selector_hits": sum(bool(row.get("primary_found")) for row in all_adaptive),
        "adaptive_usage_count": sum(bool(row.get("adaptive_used")) for row in all_adaptive),
        "adaptive_success_count": sum(bool(row.get("adaptive_used")) and bool(row.get("found")) for row in all_adaptive),
        "sku_mismatch_count": sku_mismatch_count,
        "field_pollution_count": pollution_count,
        "comparison_counts": {status: sum(row.get("result") == status for row in all_comparisons)
                              for status in ("EXACT_MATCH", "NORMALIZED_MATCH", "LEGACY_ONLY", "SCRAPLING_ONLY", "DIFFERENT", "INVALID")},
        "field_difference_counts": {field: sum(row.get("field") == field and row.get("result") in {"DIFFERENT", "LEGACY_ONLY", "SCRAPLING_ONLY"} for row in all_comparisons)
                                     for field in COMPARE_FIELDS},
        "adaptive_storage": str(adaptive_db),
        "html_snapshot_count": len(list(html_dir.glob("*.html"))),
        "sample_diagnostics": sample_diagnostics,
        "source_qa_report": source_qa,
        "browser_mode": "headed" if cfg["browser"].get("headless") is False else "NOT_RUN",
        "playwright_version": metadata.version("playwright"),
        "scrapling_version": metadata.version("scrapling"),
        "scrapling_fetchers_installed": any(
            name in {"curl-cffi", "browserforge", "patchright", "camoufox", "nodriver"}
            for name in (dist.metadata.get("Name", "").lower() for dist in metadata.distributions())
        ),
        "production_master_modified": False,
        "production_state_modified": False,
        "production_sqlite_modified": False,
        "production_dictionary_modified": False,
        "run_status": run_status,
    }
    _safe_write_json(run_dir / "run_report.json", run_report)
    return run_report


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Offline-safe Scrapling detail parser shadow; never commits.")
    parser.add_argument("--date", default="2026-09-24")
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--seed", type=int, default=20260924)
    parser.add_argument("--source", required=True, help="Validated CURRENT snapshot directory")
    parser.add_argument("--dry-run", action="store_true", default=True,
                        help="Required; this experiment has no commit mode")
    parser.add_argument("--save-html", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--sku", action="append", default=[])
    parser.add_argument("--sku-file")
    parser.add_argument("--category")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--run-id")
    parser.add_argument("--previous-run", help="Required to request the gated 100-SKU second round")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    if args.limit < 1 or args.limit > 100:
        raise SystemExit("--limit must be between 1 and 100")
    if args.resume and not args.run_id:
        raise SystemExit("--resume requires --run-id")
    if not args.resume and args.run_id:
        raise SystemExit("--run-id is only valid with --resume")
    source = Path(args.source).expanduser().resolve()
    repo_root = Path(__file__).resolve().parents[4]
    runtime_root = repo_root / "runtime" / "experiments" / "scrapling_detail_shadow"
    runtime_root.mkdir(parents=True, exist_ok=True)
    requested_skus = set(args.sku)
    if args.sku_file:
        for line in Path(args.sku_file).read_text(encoding="utf-8-sig").splitlines():
            value = line.strip().split(",", 1)[0].strip()
            if value and value.lower() != "sku":
                requested_skus.add(value)
    previous_run = None
    if args.previous_run:
        previous_path = Path(args.previous_run)
        previous_run = previous_path if previous_path.is_absolute() else runtime_root / "runs" / previous_path
    report = run_shadow(
        source=source,
        target_date=args.date,
        limit=args.limit,
        seed=args.seed,
        runtime_root=runtime_root,
        save_html=args.save_html,
        requested_skus=requested_skus or None,
        category=args.category,
        run_id=args.run_id,
        resume=args.resume,
        previous_run=previous_run,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["run_status"] == "COMPLETED" else 2

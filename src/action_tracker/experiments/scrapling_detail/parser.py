"""Deterministic field extraction plus Scrapling's adaptive semantic locators.

This module accepts an already captured HTML string. It never opens a URL or
creates a browser, and its adaptive SQLite store must be supplied explicitly
under the experiment runtime.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any
from urllib.parse import urljoin

from scrapling import Selector
from scrapling.core.storage import SQLiteStorageSystem


FIELD_NAMES = (
    "sku", "name_es", "cat1_es", "cat2_es", "spec_es", "desc_es",
    "details_es", "product_url", "image_url",
)

SEMANTIC_IDS = {
    "name_es": "action_product_title",
    "breadcrumb": "action_product_breadcrumb",
    "spec_es": "action_product_subtitle",
    "desc_es": "action_product_description",
    "details_es": "action_product_specifications",
    "image_url": "action_product_main_image",
}

_URL_SKU = re.compile(r"/p/(\d+)(?:/|$)", re.I)
_ARTICLE_SKU = re.compile(r"N[uú]mero\s+del\s+art[ií]culo\s*:\s*(\d+)", re.I)
_SPACE = re.compile(r"\s+")
_CHALLENGE = re.compile(r"cf-challenge|cf-turnstile|captcha|verify you are human|checking your browser|just a moment|un momento", re.I)


@dataclass(frozen=True)
class Extraction:
    node: Any | None
    source: str
    selector: str

    @property
    def found(self) -> bool:
        return self.node is not None


class ScraplingDetailParser:
    """Parse a single HTML snapshot with isolated adaptive storage."""

    def __init__(self, adaptive_db: Path | str):
        self.adaptive_db = Path(adaptive_db)

    def parse(self, html: str, url: str, target_sku: str) -> dict[str, Any]:
        self.adaptive_db.parent.mkdir(parents=True, exist_ok=True)
        probe = Selector(html, url=url)
        probe_title = _title_text(probe)
        probe_body = probe.css("body").first
        body_text = _text(probe_body) if probe_body else ""
        challenge_detected = bool(_CHALLENGE.search(probe_title) or _CHALLENGE.search(body_text[:3000]))
        if challenge_detected:
            return {
                **{field: "" for field in FIELD_NAMES},
                "field_sources": {field: "INVALID" for field in FIELD_NAMES},
                "adaptive_usage": [],
                "page_title": probe_title,
                "challenge_detected": True,
            }
        page = Selector(
            html,
            url=url,
            adaptive=True,
            storage=SQLiteStorageSystem,
            storage_args={"storage_file": str(self.adaptive_db), "url": url},
        )
        sources = {field: "NOT_FOUND" for field in FIELD_NAMES}
        usage: list[dict[str, Any]] = []

        title = self._locate(page, "h1", SEMANTIC_IDS["name_es"], "css")
        usage.append(self._usage("name_es", title))
        name = _text(title.node) if title.found else ""
        sources["name_es"] = title.source

        breadcrumb = self._locate(
            page,
            "nav[aria-label='Breadcrumbs'], nav[aria-label='breadcrumb'], [data-testid='breadcrumbs'], [data-testid='breadcrumb']",
            SEMANTIC_IDS["breadcrumb"],
            "css",
        )
        usage.append(self._usage("breadcrumb", breadcrumb))
        cat1, cat2 = _categories(breadcrumb.node, name) if breadcrumb.found else ("", "")
        sources["cat1_es"] = sources["cat2_es"] = breadcrumb.source

        subtitle = self._locate(page, "h1 + *", SEMANTIC_IDS["spec_es"], "css")
        usage.append(self._usage("spec_es", subtitle))
        spec = _text(subtitle.node) if subtitle.found else ""
        # Do not mislabel the first price/CTA sibling as a product specification.
        if not spec or len(spec) > 180 or "€" in spec:
            spec = ""
            spec_source = "NOT_FOUND"
        else:
            spec_source = subtitle.source
        sources["spec_es"] = spec_source

        description = self._locate(
            page,
            "//*[self::h2 or self::h3][starts-with(translate(normalize-space(string(.)), "
            "'ABCDEFGHIJKLMNOPQRSTUVWXYZÁÉÍÓÚÜ', 'abcdefghijklmnopqrstuvwxyzáéíóúü'), 'descripción')]",
            SEMANTIC_IDS["desc_es"],
            "xpath",
        )
        usage.append(self._usage("desc_es", description))
        desc = _description_after_heading(description.node) if description.found else ""
        sources["desc_es"] = description.source if desc else "NOT_FOUND"

        specifications = self._locate(
            page,
            "[data-testid='productions-specifications-table']",
            SEMANTIC_IDS["details_es"],
            "css",
        )
        usage.append(self._usage("details_es", specifications))
        details = _specification_rows(specifications.node) if specifications.found else ""
        sources["details_es"] = specifications.source if details else "NOT_FOUND"

        image = self._locate(
            page,
            "[data-testid='main-image'], [data-testid='product-main-image']",
            SEMANTIC_IDS["image_url"],
            "css",
        )
        usage.append(self._usage("image_url", image))
        image_url = _image_url(image.node, url) if image.found else ""
        sources["image_url"] = image.source if image_url else "NOT_FOUND"

        url_match = _URL_SKU.search(url)
        url_sku = url_match.group(1) if url_match else ""
        article_match = _ARTICLE_SKU.search(details)
        article_sku = article_match.group(1) if article_match else ""
        sku = article_sku or url_sku or str(target_sku)
        sources["sku"] = sources["details_es"] if article_sku else ("PRIMARY" if url_sku else "NOT_FOUND")
        sources["product_url"] = "PRIMARY" if url else "NOT_FOUND"

        return {
            "sku": sku,
            "name_es": name,
            "cat1_es": cat1,
            "cat2_es": cat2,
            "spec_es": spec,
            "desc_es": desc,
            "details_es": details,
            "product_url": url,
            "image_url": image_url,
            "field_sources": sources,
            "adaptive_usage": usage,
            "page_title": probe_title,
            "challenge_detected": False,
        }

    @staticmethod
    def _locate(page: Selector, selector: str, identifier: str, kind: str) -> Extraction:
        search = page.css if kind == "css" else page.xpath
        primary = search(selector, identifier=identifier, auto_save=True)
        if primary:
            return Extraction(primary.first, "PRIMARY", selector)
        adaptive = search(selector, identifier=identifier, adaptive=True)
        if adaptive:
            return Extraction(adaptive.first, "ADAPTIVE", selector)
        return Extraction(None, "NOT_FOUND", selector)

    @staticmethod
    def _usage(field: str, extraction: Extraction) -> dict[str, Any]:
        return {
            "field": field,
            "semantic_id": SEMANTIC_IDS.get(field, field),
            "selector": extraction.selector,
            "source": extraction.source,
            "primary_found": extraction.source == "PRIMARY",
            "adaptive_used": extraction.source == "ADAPTIVE",
            "found": extraction.found,
        }


def parse_detail_html(
    html: str,
    url: str,
    target_sku: str,
    adaptive_db: Path | str,
) -> dict[str, Any]:
    """Convenience API used by the offline tests and shadow runner."""
    return ScraplingDetailParser(adaptive_db).parse(html, url, target_sku)


def _text(node: Any) -> str:
    if node is None:
        return ""
    value = str(node.get_all_text(separator=" ", strip=True))
    return _SPACE.sub(" ", value).strip()


def _title_text(page: Selector) -> str:
    title = page.css("title").first
    return _text(title) if title else ""


def _categories(node: Any, product_name: str) -> tuple[str, str]:
    if node is None:
        return "", ""
    candidates = node.css("[data-testid='breadcrumb-label'], a, span")
    labels: list[str] = []
    for candidate in candidates:
        label = _text(candidate)
        if not label:
            continue
        if labels and labels[-1].casefold() == label.casefold():
            continue
        labels.append(label)
    ignored = {"inicio", "home", "action", "action españa"}
    labels = [label for label in labels if label.casefold() not in ignored]
    if labels and product_name and labels[-1].casefold() == product_name.casefold():
        labels.pop()
    if len(labels) >= 2:
        return labels[-2], labels[-1]
    if labels:
        return labels[0], ""
    return "", ""


def _description_after_heading(heading: Any) -> str:
    """Collect the description siblings without crossing into adjacent panels."""
    parts: list[str] = []
    # The live page places the sustainability panel as an ``aside`` immediately
    # after the Description section, before the next h2. Walking the whole
    # document's following axis would therefore contaminate Description with
    # the badge text. Stay within the heading's direct sibling block.
    siblings = heading.xpath("following-sibling::*")
    if not siblings:
        siblings = heading.xpath("following::node()")
    for node in siblings:
        tag = str(getattr(node, "tag", "")).lower()
        if tag in {"h1", "h2", "h3", "section", "aside", "article"}:
            break
        value = _text(node)
        if value:
            parts.append(value)
    return _SPACE.sub(" ", " ".join(parts)).strip()


def _specification_rows(container: Any) -> str:
    if container is None:
        return ""
    rows = container.css("tr")
    output: list[str] = []
    for row in rows:
        cells = [_text(cell) for cell in row.css("th, td")]
        cells = [cell for cell in cells if cell]
        if len(cells) < 2:
            continue
        key, value = cells[0], ": ".join(cells[1:])
        if key and value:
            output.append(f"{key}: {value}")
    return "; ".join(output)


def _image_url(container: Any, page_url: str) -> str:
    if container is None:
        return ""
    node = container
    if str(getattr(node, "tag", "")).lower() not in {"img", "source"}:
        nested = container.css("img, source").first
        if not nested:
            return ""
        node = nested
    attrs = getattr(node, "attrib", {}) or {}
    candidate = attrs.get("src") or attrs.get("data-src") or attrs.get("data-original") or ""
    if not candidate and attrs.get("srcset"):
        candidate = str(attrs["srcset"]).split(",", 1)[0].strip().split(" ", 1)[0]
    return urljoin(page_url, str(candidate).strip()) if candidate else ""

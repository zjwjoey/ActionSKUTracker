"""Dynamic top-level category discovery; configured categories remain fallback only."""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class Category:
    name: str
    slug: str
    url: str
    source: str


def discover_categories(browser, fallback: dict[str, str]) -> tuple[dict[str, str], dict]:
    """Return live `/c/<slug>/` navigation links, falling back only when discovery yields none."""
    links = browser.page.evaluate("""() => [...document.querySelectorAll('a[href]')].map(a =>
        ({href: a.href, name: (a.textContent || '').replace(/\\s+/g, ' ').trim()}))""")
    found: dict[str, str] = {}
    rows: list[dict] = []
    for link in links:
        path = urlparse(link.get("href") or "").path.rstrip("/")
        parts = path.split("/c/")
        if len(parts) != 2 or not parts[1] or "/" in parts[1]:
            continue
        slug = parts[1]
        if slug not in found:
            found[slug] = link["href"].split("?")[0]
            rows.append({"name": link.get("name") or slug, "slug": slug, "url": found[slug],
                         "parent": "", "source": "dynamic", "scan_status": "PENDING"})
    if found:
        return found, {"discovery_status": "SUCCESS", "fallback_used": False, "categories": rows}
    rows = [{"name": slug, "slug": slug, "url": url, "parent": "", "source": "fallback", "scan_status": "PENDING"}
            for slug, url in fallback.items()]
    return dict(fallback), {"discovery_status": "DEGRADED", "fallback_used": True, "categories": rows}

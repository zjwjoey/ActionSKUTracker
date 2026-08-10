"""Sitemap 采集：官网 SKU 存在的证据来源（规范 §14-A）。

sitemap 一次请求即可拿到全量商品 URL，是每日轻量监测的骨架。
用 Playwright 走挑战处理；裸 requests 已实测被 CF 挡（403）。
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

_SKU_URL_RE = re.compile(r"/p/(\d+)/")


@dataclass
class SitemapResult:
    raw_xml: str
    locs: list[str]
    skus: list[str]
    fetched_at: str

    @property
    def sku_set(self) -> set[str]:
        return set(self.skus)


def fetch_sitemap(browser, url: str, timeout_ms: int | None = None) -> SitemapResult:
    """浏览器打开 sitemap.xml，解析全部商品 <loc>。"""
    import datetime as dt

    ok = browser.goto(url, timeout_ms=timeout_ms)
    raw = browser.page.content()
    skus: list[str] = []
    locs: list[str] = []
    # 容错解析：优先 XML 树，失败则正则兜底
    try:
        root = ET.fromstring(raw)
        for loc in root.iter():
            if loc.tag.endswith("loc") and loc.text:
                locs.append(loc.text.strip())
    except ET.ParseError:
        locs = re.findall(r"<loc>\s*([^<]+?)\s*</loc>", raw)
    if not locs:
        raise RuntimeError(f"sitemap 未解析到任何 <loc>，可能仍是挑战页（passed={ok}）")
    for u in locs:
        m = _SKU_URL_RE.search(u)
        if m:
            skus.append(m.group(1))
    return SitemapResult(
        raw_xml=raw,
        locs=locs,
        skus=skus,
        fetched_at=dt.datetime.now().isoformat(timespec="seconds"),
    )

"""详情页字段提取（Python 复刻旧 Node 脚本 extractFn + 校验重试）。

仅在需要补详情的 SKU（NEW/REAPPEARED/变化候选）上调用，禁止全量详情抓取。
"""
from __future__ import annotations

import json
import logging
import re
import time

from ..services.normalization import parse_discount_percent, parse_price

log = logging.getLogger(__name__)

_BAD_TITLE_RE = re.compile(r"^action españa: pequeños precios", re.I)

_EXTRACT_JS = r"""
(url) => {
    const txt = (el) => (el ? el.textContent.replace(/\s+/g, ' ').trim() : '');
    const firstMatch = (re) => {
        const el = [...document.querySelectorAll('h2,h3,span,p,div')].find(
            (e) => re.test(e.textContent || '') && e.textContent.length < 200
        );
        return el ? txt(el) : '';
    };

    let skuOut = (url.match(/\/p\/(\d+)\//) || [])[1] || '';
    const title = txt(document.querySelector('h1')) ||
        document.title.replace(/\s*\|\s*Action.*$/i, '').trim();

    const crumbs = [...document.querySelectorAll('[data-testid="breadcrumb-label"]')].map(e => e.textContent.trim());
    const cat1 = crumbs.length > 1 ? (crumbs[crumbs.length - 3] || crumbs[0]) : (crumbs[0] || '');
    const cat2 = crumbs.length > 1 ? crumbs[crumbs.length - 2] : '';

    let subtitle = '';
    const h1 = document.querySelector('h1');
    if (h1) {
        let n = h1.nextElementSibling;
        let tries = 0;
        while (n && tries < 6) {
            const t = n.textContent.replace(/\s+/g, ' ').trim();
            if (t && t.length > 3 && t.length < 180 && !/€/.test(t)) { subtitle = t; break; }
            n = n.nextElementSibling;
            tries++;
        }
    }
    if (!subtitle) subtitle = firstMatch(/\|/);

    const pd = document.querySelector('[data-testid="product-details"]');
    const priceBox = (pd || document).querySelector('[data-testid="product-card-price"]') || pd || document;
    const whole = txt(priceBox.querySelector('[data-testid="product-card-price-whole"]')).replace(/[^\d]/g, '');
    const frac = txt(priceBox.querySelector('[data-testid="product-card-price-fractional"]')).replace(/[^\d]/g, '');
    const salePrice = (whole || frac) ? `${whole},${frac} €` : '';
    const origEl = priceBox.querySelector('[data-testid="product-card-price-original-amount"]');
    const orig = origEl ? origEl.textContent.replace(/\s+/g, ' ').trim() : '';
    const origPrice = orig ? `${orig} €` : salePrice;
    const discEl = priceBox.querySelector('[data-testid="product-card-price-discount-percentage"]');
    const discount = discEl ? discEl.textContent.replace(/\s+/g, ' ').trim() : '';
    const priceDescEl = priceBox.querySelector('[data-testid="product-card-price-description"]');
    const priceDesc = priceDescEl ? priceDescEl.textContent.replace(/\s+/g, ' ').trim() : '';

    let desc = '';
    const descHead = [...document.querySelectorAll('h2,h3')].find(h => /^descripci/i.test(h.textContent.trim()));
    if (descHead) {
        const sec = descHead.closest('section') || descHead.parentElement;
        desc = sec ? sec.innerText.replace(/\s*\n\s*/g, '\n').trim() : '';
        desc = desc.replace(/^Descripción\s*\n?/i, '').trim();
    }

    let details = '';
    const table = document.querySelector('[data-testid="productions-specifications-table"]');
    if (table) {
        details = [...table.querySelectorAll('tr')]
            .map(tr => {
                const cells = [...tr.querySelectorAll('td')].map(c => c.textContent.replace(/\s+/g, ' ').trim());
                return cells.join(': ');
            })
            .filter(Boolean)
            .join('; ')
            .replace(/::/g, ':');
    }

    const skuMatch = details.match(/Número del artículo\s*:\s*(\d+)/);
    if (skuMatch) skuOut = skuMatch[1];
    if (!skuOut) skuOut = (url.match(/\/p\/(\d+)\//) || [])[1] || '';

    let img = '';
    const mainImg = document.querySelector('[data-testid="main-image"] img, [data-testid="main-image"]');
    if (mainImg && mainImg.currentSrc) img = mainImg.currentSrc;
    else if (mainImg && mainImg.src) img = mainImg.src;
    else { const first = [...document.images].find(i => /product/i.test(i.src)); if (first) img = first.src; }

    const cleanTag = (v) => v.replace(/\s+/g, ' ').trim().replace(/semanal(?=\d)/, 'semanal ');
    let notes = '';
    const detailsBox = document.querySelector('[data-testid="product-details"]');
    const tags = [];
    if (detailsBox) {
        const tagEls = [...detailsBox.querySelectorAll('[data-testid="product-tag"]')];
        for (const t of tagEls) { const v = cleanTag(t.textContent); if (v && !tags.includes(v)) tags.push(v); }
    }
    if (!tags.length && document.querySelector('[data-testid="product-tag"]')) {
        const v = cleanTag(document.querySelector('[data-testid="product-tag"]').textContent);
        if (v) tags.push(v);
    }
    notes = [...tags, discount].filter(Boolean).join(' | ');

    return {
        sku: skuOut,
        name_es: title,
        cat1_es: cat1,
        cat2_es: cat2,
        spec_es: subtitle,
        current_price: salePrice,
        original_price: origPrice,
        unit_price: priceDesc,
        discount: discount,
        desc_es: desc,
        details_es: details,
        product_url: url,
        image_url: img,
        raw_tags: notes,
    };
}
"""


def is_bad_title(title: str) -> bool:
    t = (title or "").strip()
    return not t or t == "www.action.com" or bool(_BAD_TITLE_RE.search(t))


def fetch_product_detail(browser, url: str, sku_hint: str | None = None, max_retries: int = 5) -> dict:
    """抓取单个商品详情页并提取字段。带挑战重试 + 品名有效性校验。"""
    from ..services.browser import is_challenge
    from ..services.access import CollectionBlocked

    page = browser.page
    last_err = ""
    for attempt in range(max_retries):
        try:
            if not browser.goto(url):
                raise CollectionBlocked("detail navigation blocked or rate limited")
            t = page.title()
            if is_challenge(t):
                raise CollectionBlocked("detail challenge detected")
            try:
                page.wait_for_selector('[data-testid="product-card-price"], h1', timeout=15000)
            except Exception:
                pass  # 部分页面结构差异，容忍后继续提取
            row = page.evaluate(_EXTRACT_JS, url)
            if is_bad_title(row.get("name_es") or ""):
                last_err = f"品名无效(第{attempt + 1}次): {str(row.get('name_es'))[:40]}"
                time.sleep(0.8)
                continue
            return _normalize_detail(row, url)
        except CollectionBlocked:
            raise
        except Exception as e:  # noqa: BLE001
            last_err = str(e)
            time.sleep(0.8)
    raise RuntimeError(last_err or "重试耗尽")


def _normalize_detail(raw: dict, url: str) -> dict:
    cur = parse_price(raw.get("current_price") or "")
    orig = parse_price(raw.get("original_price") or "")
    if cur is None and orig is not None:
        cur = orig
    return {
        "sku": str(raw.get("sku") or ""),
        "name_es": raw.get("name_es") or "",
        "cat1_es": raw.get("cat1_es") or "",
        "cat2_es": raw.get("cat2_es") or "",
        "spec_es": raw.get("spec_es") or "",
        "current_price": cur,
        "original_price": orig,
        "unit_price": raw.get("unit_price") or "",
        "discount": parse_discount_percent(raw.get("discount") or ""),
        "desc_es": raw.get("desc_es") or "",
        "details_es": raw.get("details_es") or "",
        "product_url": url,
        "image_url": raw.get("image_url") or "",
        "raw_tags": raw.get("raw_tags") or "",
    }

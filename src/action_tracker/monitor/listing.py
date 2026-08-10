"""Listing 轻量扫描：全类目分页取商品卡，得到 SKU + 轻量字段。

依据实测的页面结构（2026-08-10 验证）：
    product-card / product-card-link / product-card-title / product-card-description
    product-card-price-description / price-whole / price-fractional
    price-original-amount / price-discount-percentage / product-tag
    GridPaginationLink(page=N) / product-grid-number-of-items

一次 listing 扫描不访问任何详情页（规范 §21）。
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from ..services.normalization import parse_discount_percent, parse_price

log = logging.getLogger(__name__)

_SKU_URL_RE = re.compile(r"/p/(\d+)/")

# 配置类别 key -> 官网一级类目显示名（新 SKU 的 cat1_es 用）
CATEGORY_LABELS = {
    "vivienda": "Vivienda",
    "cuidado-personal": "Cuidado personal",
    "moda": "Moda",
    "bricolaje": "Bricolaje",
    "cocina": "Cocina",
    "hogar": "Hogar",
    "comer-y-beber": "Comer y beber",
    "hobby": "Hobby",
    "juguetes": "Juguetes",
    "oficina-y-papeleria": "Oficina y papelería",
    "multimedia": "Multimedia",
    "jardin": "Jardín",
    "mascotas": "Mascotas",
    "viajes": "Viajes",
    "articulos-deportivos": "Artículos deportivos",
}

_EXTRACT_JS = r"""
() => {
    const out = [];
    const cards = document.querySelectorAll('[data-testid="product-card"]');
    for (const c of cards) {
        const link = c.querySelector('a[data-testid="product-card-link"]');
        if (!link) continue;
        const href = link.href || '';
        const m = href.match(/\/p\/(\d+)\//);
        if (!m) continue;
        const txt = (sel) => {
            const el = c.querySelector('[data-testid="' + sel + '"]');
            return el ? el.textContent.replace(/\s+/g, ' ').trim() : '';
        };
        const img = c.querySelector('img[data-testid="product-card-image"]');
        let image = '';
        if (img) {
            const s = img.getAttribute('srcset') || '';
            const m1080 = s.match(/https:\/\/[^ ]+?w_1080\/[^ ]+?\.webp/);
            if (m1080) image = m1080[0];
            else image = img.getAttribute('src') || '';
        }
        const tags = [...c.querySelectorAll('[data-testid="product-tag"]')]
            .map(e => e.textContent.replace(/\s+/g, ' ').trim())
            .filter(Boolean);
        out.push({
            sku: m[1],
            product_url: href.split('?')[0],
            name_es: txt('product-card-title'),
            spec_es: txt('product-card-description'),
            unit_price: txt('product-card-price-description'),
            whole: txt('product-card-price-whole'),
            fractional: txt('product-card-price-fractional'),
            original: txt('product-card-price-original-amount'),
            discount: txt('product-card-price-discount-percentage'),
            tags: tags,
            image_url: image,
        });
    }
    return out;
}
"""


@dataclass
class LightProduct:
    sku: str
    product_url: str
    name_es: str
    spec_es: str
    unit_price: str
    current_price: float | None
    original_price: float | None
    discount: float | None
    raw_tags: str
    image_url: str
    cat1_es: str = ""
    cat2_es: str = ""
    extra: dict = field(default_factory=dict)


def _to_light(raw: dict) -> LightProduct:
    whole = raw.get("whole") or ""
    frac = raw.get("fractional") or ""
    price_txt = f"{whole},{frac}" if frac else (whole or "")
    cur = parse_price(price_txt) or parse_price(raw.get("unit_price") or "")
    orig = parse_price(raw.get("original") or "")
    disc = parse_discount_percent(raw.get("discount") or "")
    tags = raw.get("tags") or []
    return LightProduct(
        sku=str(raw.get("sku")),
        product_url=raw.get("product_url") or "",
        name_es=raw.get("name_es") or "",
        spec_es=raw.get("spec_es") or "",
        unit_price=raw.get("unit_price") or "",
        current_price=cur,
        original_price=orig,
        discount=disc,
        raw_tags=" | ".join(tags),
        image_url=raw.get("image_url") or "",
    )


def _detect_max_page(browser) -> int:
    try:
        return browser.page.evaluate(
            """() => {
                let max = 1;
                for (const a of document.querySelectorAll('[data-testid="GridPaginationLink"]')) {
                    const m = (a.getAttribute('href') || '').match(/page=(\\d+)/);
                    if (m) max = Math.max(max, parseInt(m[1], 10));
                }
                return max;
            }"""
        )
    except Exception:
        return 1


def _grid_ok(browser) -> bool:
    try:
        return browser.page.evaluate(
            """() => {
                const n = document.querySelector('[data-testid="product-grid-number-of-items"]');
                const links = document.querySelectorAll('[data-testid="product-card-link"]');
                return !!n && links.length > 0;
            }"""
        )
    except Exception:
        return False


def _wait_for_grid(browser, tries: int = 12) -> bool:
    """等待商品网格真正渲染（复刻旧脚本 waitForGrid）。

    Cloudflare 挑战页没有网格节点；此时 reload 触发重新校验，CF 会在几秒内
    自动放行真实页面，因此循环 reload 直到网格出现即可自愈。
    """
    page = browser.page
    for _ in range(tries):
        if _grid_ok(browser):
            return True
        try:
            page.reload(wait_until="domcontentloaded")
        except Exception:
            pass
        page.wait_for_timeout(1500)
    return False


def scan_category(browser, cat: str, cat_url: str, max_pages: int | None = None) -> list[LightProduct]:
    """扫描单个类目全部分页，返回轻量商品列表。

    每页先确认网格真正渲染（_wait_for_grid），否则提取会读到旧 DOM/挑战页，
    导致 34 页去重成 1 页（实测 bug：西班牙语挑战标题 'Un momento…' 未被识别）。
    """
    page = browser.page
    passed = browser.goto(cat_url)
    if not passed:
        log.warning("类别 %s 挑战未通过", cat)
    page.wait_for_timeout(1500)
    total = _detect_max_page(browser)
    if max_pages:
        total = min(total, max_pages)
    seen: dict[str, LightProduct] = {}
    for p in range(1, total + 1):
        url = cat_url if p == 1 else f"{cat_url}?page={p}"
        try:
            browser.goto(url)
            if not _wait_for_grid(browser):
                log.warning("  [%s] 页 %d/%d 网格未加载(疑似被拦截)，跳过", cat, p, total)
                browser.sleep()
                continue
            raw_list = page.evaluate(_EXTRACT_JS)
            added = 0
            for r in raw_list:
                lp = _to_light(r)
                if lp.sku not in seen:
                    seen[lp.sku] = lp
                    added += 1
            log.info("  [%s] 页 %d/%d 本页 %d 新增 %d 累计 %d", cat, p, total, len(raw_list), added, len(seen))
        except Exception as e:
            log.warning("  [%s] 页 %d/%d 失败: %s", cat, p, total, e)
        browser.sleep()
    return list(seen.values())


def scan_all_categories(browser, categories: dict[str, str], max_pages: int | None = None) -> dict[str, list[LightProduct]]:
    """扫描所有类目，返回 {cat: [LightProduct, ...]}。"""
    result = {}
    label_map = {k: v for k, v in CATEGORY_LABELS.items()}
    for cat, url in categories.items():
        log.info("扫描类别: %s", cat)
        try:
            items = scan_category(browser, cat, url, max_pages=max_pages)
            for it in items:
                it.cat1_es = label_map.get(cat, cat)
            result[cat] = items
            log.info("  %s -> %d 商品", cat, len(items))
        except Exception as e:
            log.error("  %s 扫描失败: %s", cat, e)
            result[cat] = []
    return result

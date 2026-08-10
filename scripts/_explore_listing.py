"""临时探索：检查 listing 卡片可抽取的轻量字段（开发完成后删除）"""
import json, time, re
from playwright.sync_api import sync_playwright

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"

def is_challenge(title):
    return bool(re.search(r"just a moment", title, re.I)) or title.strip() == "www.action.com"

def wait_past_cf(page, tries=15):
    for i in range(tries):
        t = page.title()
        if not is_challenge(t):
            return True
        time.sleep(0.8)
        page.reload(wait_until="domcontentloaded")
    return False

with sync_playwright() as p:
    b = p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
    ctx = b.new_context(viewport={"width": 1366, "height": 900}, user_agent=UA, locale="es-ES")
    ctx.add_cookies(json.load(open(r"F:\260809action_cc\cookies.json", encoding="utf-8")))
    page = ctx.new_page()
    page.goto("https://www.action.com/es-es/c/hogar/", wait_until="domcontentloaded", timeout=60000)
    wait_past_cf(page)
    page.wait_for_timeout(2500)
    info = page.evaluate(
        """() => {
            const cards = [...document.querySelectorAll('[data-testid="product-card"]')];
            const first = cards[0];
            if (!first) return {err: 'no cards'};
            const out = { text: first.innerText.replace(/\\n/g, ' | ') };
            const tids = {};
            first.querySelectorAll('[data-testid]').forEach(e => {
                const t = e.getAttribute('data-testid');
                if (!(t in tids)) tids[t] = (e.textContent || '').replace(/\\s+/g,' ').trim().slice(0, 80);
            });
            out.testids = tids;
            const link = first.querySelector('a[data-testid="product-card-link"]');
            out.href = link ? link.href : '';
            return out;
        }"""
    )
    for k, v in info.items():
        print(f"--- {k} ---")
        print(v if not isinstance(v, dict) else json.dumps(v, ensure_ascii=False, indent=1))
    b.close()

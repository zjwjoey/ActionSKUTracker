"""Playwright 浏览器会话管理。

复刻旧 Node 脚本（F:\\260809action_cc）在另一台电脑上跑通的 Cloudflare 处理经验：
    - 真实 Chromium + --disable-blink-features=AutomationControlled
    - 加载可选的 cookies.json 保持 consent/locale 会话
    - goto 后检测挑战页标题，重载直至通过
规范 §62：禁止绕过 CAPTCHA/Cloudflare 安全机制。这里走的是真实浏览器会话，
不注入规避脚本、不做自动验证码识别。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"
)

# Cloudflare 挑战页标题：英文 "Just a moment…"，西班牙语站点为 "Un momento…"
CHALLENGE_RE = re.compile(r"just a moment|un momento", re.I)
HOMEPAGE_TITLE_RE = re.compile(r"^action españa: pequeños precios", re.I)


def is_challenge(title: str) -> bool:
    t = (title or "").strip()
    return bool(CHALLENGE_RE.search(t)) or t == "www.action.com"


class BrowserSession:
    def __init__(self, browser_cfg: dict, cookies_path: str | Path | None = None, page: Page | None = None):
        self.cfg = browser_cfg
        self.cookies_path = Path(cookies_path) if cookies_path else None
        self._pw = None
        self._browser = None
        self._ctx = None
        self.page = page

    # ---- 生命周期 ----
    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.close()

    def start(self):
        if self.page is not None:
            return
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=self.cfg.get("headless", True),
            args=["--disable-blink-features=AutomationControlled"],
        )
        self._ctx = self._browser.new_context(
            viewport={"width": 1366, "height": 900},
            user_agent=self.cfg.get("user_agent", DEFAULT_UA),
            locale=self.cfg.get("locale", "es-ES"),
        )
        if self.cookies_path and self.cookies_path.exists():
            try:
                cookies = json.loads(self.cookies_path.read_text(encoding="utf-8"))
                self._ctx.add_cookies(cookies)
            except Exception:
                pass
        self.page = self._ctx.new_page()

    def close(self):
        for obj in (self._browser,):
            try:
                if obj:
                    obj.close()
            except Exception:
                pass
        if self._pw:
            try:
                self._pw.stop()
            except Exception:
                pass

    # ---- 导航 ----
    def goto(self, url: str, timeout_ms: int | None = None) -> bool:
        """访问 URL，尽力通过挑战页。返回最终是否未停留在挑战页。"""
        page = self.page
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms or self.cfg.get("timeout_ms", 45000))
        for _ in range(self.cfg.get("challenge_reloads", 15)):
            try:
                title = page.title()
            except Exception:
                title = ""
            if not is_challenge(title):
                return True
            time.sleep(self.cfg.get("challenge_sleep_ms", 800) / 1000.0)
            try:
                page.reload(wait_until="domcontentloaded")
            except Exception:
                time.sleep(1.0)
        return False

    def sleep(self):
        """温和节奏，避免高频访问被限流。"""
        time.sleep(self.cfg.get("sleep_ms", 1800) / 1000.0)

"""Playwright 浏览器会话管理。

复刻旧版 Node 浏览器采集器在另一台机器上验证过的 Cloudflare 处理经验：
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
    def __init__(self, browser_cfg: dict, cookies_path: str | Path | None = None, page: Page | None = None,
                 access_controller=None, keep_open: bool = False):
        self.cfg = browser_cfg
        self.cookies_path = Path(cookies_path) if cookies_path else None
        self._pw = None
        self._browser = None
        self._ctx = None
        self.page = page
        self.access_controller = access_controller
        self.keep_open = keep_open
        self.profile_dir = Path(self.cfg["profile_dir"]).resolve() if self.cfg.get("profile_dir") else None

    # ---- 生命周期 ----
    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        if not self.keep_open:
            self.close()

    def start(self):
        if self.page is not None:
            return
        self._pw = sync_playwright().start()
        # A persistent context is deliberately the one run-scoped browser session:
        # listing, badge entries, and details share its cookies and site storage.
        # Its profile is runtime state, never staging input or source-controlled data.
        if not self.profile_dir:
            raise ValueError("browser.profile_dir is required for the persistent Playwright session")
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._ctx = self._pw.chromium.launch_persistent_context(
            user_data_dir=str(self.profile_dir),
            headless=self.cfg.get("headless", True),
            args=["--disable-blink-features=AutomationControlled"],
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
        self.page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()

    def close(self):
        for obj in (self._ctx, self._browser):
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

    def manifest(self) -> dict:
        """Safe session facts for evidence; never exposes cookie or token values."""
        return {
            "browser_engine": "playwright",
            "browser_mode": "headless" if self.cfg.get("headless", True) else "headed",
            "persistent_context": True,
            "profile_reused": bool(self.profile_dir and self.profile_dir.exists()),
            "profile_path_identifier": str(self.profile_dir) if self.profile_dir else "",
            "context_strategy": "one persistent context and one reusable page per run",
        }

    # ---- 导航 ----
    def goto(self, url: str, timeout_ms: int | None = None, detail_mode: bool = False) -> bool:
        """访问 URL，尽力通过挑战页。详情页使用受控的 5 分钟等待策略。"""
        page = self.page
        if self.access_controller:
            self.access_controller.before_navigation()
        try:
            response = page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms or self.cfg.get("timeout_ms", 45000))
        except Exception:
            if self.access_controller:
                self.access_controller.record(error=True)
            raise
        status = response.status if response else None
        if status == 429:
            if self.access_controller:
                self.access_controller.record(status=429)
            return False
        if status in (401, 403):
            try:
                title = page.title()
            except Exception:
                title = ""
            # Cloudflare may return a challenge page with HTTP 403.  For a
            # Detail navigation it follows the same bounded 5-minute policy
            # as a 200 challenge page; an ordinary 403 remains an immediate
            # access restriction and is never reloaded.
            if detail_mode and is_challenge(title):
                return self.wait_detail_challenge()
            if self.access_controller:
                # A 403 is never success.  Preserve whether it was a challenge
                # page in the controller event, but do not retry either form.
                self.access_controller.record(challenge=is_challenge(title), status=None if is_challenge(title) else status)
            return False
        if self.access_controller:
            self.access_controller.record(status=status)
        if detail_mode and is_challenge(self._page_title()):
            return self.wait_detail_challenge()
        # A missing config must fail closed to a small bounded retry count.
        for _ in range(int(self.cfg.get("challenge_reloads", 3))):
            try:
                title = page.title()
            except Exception:
                title = ""
            if not is_challenge(title):
                if self.access_controller:
                    self.access_controller.record()
                return True
            if self.access_controller and not self.access_controller.allow_challenge_retry():
                self.access_controller.record(challenge=True)
                return False
            time.sleep((self.cfg.get("challenge_sleep_ms", 800) / 1000.0) * (2 ** _))
            try:
                reload_response = page.reload(
                    wait_until="domcontentloaded", timeout=self.cfg.get("timeout_ms", 45000)
                )
                reload_status = reload_response.status if reload_response else None
                if reload_status == 429:
                    if self.access_controller:
                        self.access_controller.record(status=429)
                    return False
                if reload_status in (401, 403):
                    if self.access_controller:
                        self.access_controller.record(challenge=True)
                    return False
            except Exception:
                time.sleep(1.0)
        if self.access_controller:
            self.access_controller.record(challenge=True)
        return False

    def _page_title(self) -> str:
        try:
            return self.page.title()
        except Exception:
            return ""

    def wait_detail_challenge(self) -> bool:
        """Wait at most five minutes for a detail challenge to clear.

        The only retries are the two scheduled page reloads at two and four
        minutes.  This is a bounded recovery attempt, not a challenge bypass.
        """
        total = max(0.0, float(self.cfg.get("detail_challenge_wait_seconds", 300)))
        checkpoints = sorted({
            max(0.0, float(value))
            for value in self.cfg.get("detail_challenge_reload_at_seconds", [120, 240])
            if float(value) < total
        })
        elapsed = 0.0
        for checkpoint in checkpoints:
            time.sleep(max(0.0, checkpoint - elapsed))
            elapsed = checkpoint
            if self.access_controller and not self.access_controller.allow_challenge_retry():
                if not self.access_controller.blocked:
                    self.access_controller.record(challenge=True)
                return False
            try:
                response = self.page.reload(
                    wait_until="domcontentloaded", timeout=self.cfg.get("timeout_ms", 45000)
                )
                status = response.status if response else None
            except Exception:
                # Keep the five-minute window; a transient reload error is not
                # permission to issue an unbounded retry loop.
                continue
            if status == 429:
                if self.access_controller:
                    self.access_controller.record(status=429)
                return False
            if status in (401, 403):
                title = self._page_title()
                if self.access_controller:
                    self.access_controller.record(
                        challenge=is_challenge(title),
                        status=None if is_challenge(title) else status,
                    )
                return False
            if not is_challenge(self._page_title()):
                if self.access_controller:
                    self.access_controller.record()
                return True

        time.sleep(max(0.0, total - elapsed))
        if not is_challenge(self._page_title()):
            if self.access_controller:
                self.access_controller.record()
            return True
        if self.access_controller:
            self.access_controller.block("DETAIL_CHALLENGE_TIMEOUT")
        return False

    def sleep(self):
        """温和节奏，避免高频访问被限流。"""
        time.sleep(self.cfg.get("sleep_ms", 1800) / 1000.0)

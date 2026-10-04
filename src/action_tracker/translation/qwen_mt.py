"""Small Qwen-MT Flash adapter used by the detail extraction stage.

The adapter deliberately owns only transport concerns.  It sends the raw
Spanish field to the dedicated Qwen-MT endpoint and returns the provider text;
post-translation cleaning remains outside this module.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Mapping


Transport = Callable[[str, Mapping[str, str], Mapping[str, Any], float], Mapping[str, Any]]


class QwenMTError(RuntimeError):
    """A bounded provider failure which can be recorded without aborting a run."""


@dataclass(frozen=True)
class QwenMTResult:
    text: str = ""
    attempts: int = 0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return bool(self.text.strip()) and not self.error


class QwenMTFlashProvider:
    """OpenAI-compatible Qwen-MT Flash client.

    ``transport`` is injectable for tests.  The production transport is kept
    intentionally small so the extraction code does not depend on an SDK.
    """

    model = "qwen-mt-flash"

    def __init__(
        self,
        *,
        endpoint: str | None = None,
        api_key: str | None = None,
        api_key_env: str = "DASHSCOPE_API_KEY",
        model: str = "qwen-mt-flash",
        timeout: float = 60.0,
        max_retries: int = 3,
        backoff_seconds: float = 5.0,
        rate_limit_per_second: float = 0.5,
        transport: Transport | None = None,
    ) -> None:
        self.endpoint = (
            str(endpoint or "").strip()
            or os.environ.get("QWEN_MT_BASE_URL", "").strip()
            or os.environ.get("QWEN_API_ENDPOINT", "").strip()
            or "https://dashscope.aliyuncs.com/compatible-mode/v1"
        ).rstrip("/")
        self.api_key = api_key or os.environ.get(api_key_env, "").strip()
        self.api_key_env = api_key_env
        self.model = str(model or self.model)
        self.timeout = float(timeout)
        self.max_retries = max(0, int(max_retries))
        self.backoff_seconds = max(0.0, float(backoff_seconds))
        self.rate_limit_per_second = max(0.0, float(rate_limit_per_second))
        self._last_request_at: float | None = None
        self.transport = transport or self._http_transport

    def _url(self) -> str:
        if self.endpoint.endswith("/chat/completions"):
            return self.endpoint
        return self.endpoint + "/chat/completions"

    @staticmethod
    def _http_transport(
        url: str, headers: Mapping[str, str], payload: Mapping[str, Any], timeout: float
    ) -> Mapping[str, Any]:
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=dict(headers),
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310
                body = response.read().decode("utf-8")
                return {"status_code": int(response.status), "body": json.loads(body)}
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                body = json.loads(raw)
            except ValueError:
                body = {"error": raw[:500]}
            return {"status_code": int(exc.code), "body": body}
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return {"status_code": 599, "error": str(exc)}

    def _wait_for_slot(self) -> None:
        if self.rate_limit_per_second <= 0:
            return
        interval = 1.0 / self.rate_limit_per_second
        now = time.monotonic()
        if self._last_request_at is not None:
            remaining = interval - (now - self._last_request_at)
            if remaining > 0:
                time.sleep(remaining)
        self._last_request_at = time.monotonic()

    @staticmethod
    def _extract_text(body: Any) -> str:
        if not isinstance(body, Mapping):
            return ""
        choices = body.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], Mapping):
            message = choices[0].get("message") or {}
            content = message.get("content") if isinstance(message, Mapping) else ""
            if isinstance(content, list):
                content = "".join(
                    str(part.get("text", "")) if isinstance(part, Mapping) else str(part)
                    for part in content
                )
            if content:
                return str(content).strip()
        output = body.get("output")
        if isinstance(output, Mapping):
            choices = output.get("choices")
            if isinstance(choices, list) and choices and isinstance(choices[0], Mapping):
                message = choices[0].get("message") or {}
                content = message.get("content") if isinstance(message, Mapping) else ""
                if content:
                    return str(content).strip()
            if output.get("text"):
                return str(output["text"]).strip()
        return str(body.get("text") or "").strip()

    def translate(
        self,
        text: str,
        *,
        source_locale: str = "es",
        target_locale: str = "zh-CN",
        field: str = "",
        sku: str = "",
    ) -> QwenMTResult:
        source = str(text or "").strip()
        if not source:
            return QwenMTResult(text="", attempts=0, error="SOURCE_EMPTY")
        if not self.api_key:
            return QwenMTResult(attempts=0, error=f"MISSING_{self.api_key_env}")
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": source}],
            "translation_options": {
                "source_lang": "Spanish" if str(source_locale).lower().startswith("es") else source_locale,
                "target_lang": "Chinese" if str(target_locale).lower().startswith("zh") else target_locale,
            },
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        last_error = "QWEN_REQUEST_FAILED"
        attempts = 0
        for attempt in range(1, self.max_retries + 2):
            attempts = attempt
            self._wait_for_slot()
            try:
                response = self.transport(self._url(), headers, payload, self.timeout)
            except Exception as exc:  # injectable transports may raise
                response = {"status_code": 599, "error": str(exc)}
            if not isinstance(response, Mapping):
                response = {"status_code": 599, "error": "INVALID_RESPONSE"}
            code = int(response.get("status_code", 200) or 0)
            body = response.get("body", response)
            translated = self._extract_text(body)
            if 200 <= code < 300 and translated:
                return QwenMTResult(text=translated, attempts=attempt)
            detail = response.get("error")
            if not detail and isinstance(body, Mapping):
                detail = body.get("error")
            last_error = f"HTTP_{code}" + (f": {detail}" if detail else "")
            retryable = code == 429 or code >= 500 or code == 599
            if not retryable or attempt > self.max_retries:
                break
            if self.backoff_seconds:
                time.sleep(self.backoff_seconds * (2 ** (attempt - 1)))
        return QwenMTResult(attempts=attempts, error=last_error[:500])


__all__ = ["QwenMTError", "QwenMTFlashProvider", "QwenMTResult"]

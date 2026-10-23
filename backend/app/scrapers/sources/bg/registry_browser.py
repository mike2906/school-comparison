"""Read the ministry register through a headless browser when Cloudflare screens out scripts.

Since October 2026 every *.mon.bg host answers plain HTTP clients with a Cloudflare managed
challenge. A real browser on a home connection usually gets through, so the register import
retries through headless Chromium (Playwright) and sends the same POST requests from inside
the page. Data-centre addresses stay blocked either way: this works on the PC, not in the cloud.
"""

from __future__ import annotations

import asyncio
import time

import httpx

_CHALLENGE_TITLES = ("Just a moment", "Един момент")
# Sends one POST from the page, so it carries the browser's own TLS, cookies and clearance.
_FETCH_JS = """async ([url, body, headers]) => {
  const response = await fetch(url, { method: 'POST', headers, body: JSON.stringify(body), credentials: 'include' })
  return { status: response.status, headers: Object.fromEntries(response.headers), text: await response.text() }
}"""


class BrowserClient:
    """The small part of httpx.AsyncClient the register adapter uses (`post`), sent from a browser page."""

    def __init__(self, origin: str, wait_seconds: float = 60.0):
        self.origin = origin
        self.wait_seconds = wait_seconds
        self._playwright = None
        self._browser = None
        self._page = None

    async def __aenter__(self) -> BrowserClient:
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:  # pragma: no cover - the dependency is in pyproject
            raise RuntimeError("the register needs a browser but Playwright is not installed") from exc
        self._playwright = await async_playwright().start()
        try:
            self._browser = await self._playwright.chromium.launch(headless=True)
            self._page = await self._browser.new_page(locale="bg-BG")
            # The API's own origin: the page passes (or is let through) the challenge there,
            # and later requests are same-origin, so their status and headers stay readable.
            await self._page.goto(self.origin, wait_until="domcontentloaded", timeout=60_000)
            await self._wait_for_clearance()
        except BaseException:
            await self.__aexit__(None, None, None)
            raise
        return self

    async def __aexit__(self, *exc_info) -> None:
        if self._browser is not None:
            await self._browser.close()
        if self._playwright is not None:
            await self._playwright.stop()
        self._browser = self._playwright = self._page = None

    async def _wait_for_clearance(self) -> None:
        deadline = time.monotonic() + self.wait_seconds
        while True:
            title = await self._page.title()
            if not any(marker in title for marker in _CHALLENGE_TITLES):
                return
            if time.monotonic() > deadline:
                raise RuntimeError(
                    f"{self.origin} kept a headless browser on the Cloudflare challenge for"
                    f" {self.wait_seconds:.0f}s (this connection is screened out too)"
                )
            await asyncio.sleep(1)

    async def post(self, url: str, json: dict | None = None, headers: dict | None = None) -> httpx.Response:
        result = await self._page.evaluate(_FETCH_JS, [url, json or {}, headers or {}])
        return httpx.Response(
            result["status"],
            headers=result["headers"],
            content=result["text"].encode(),
            request=httpx.Request("POST", url),
        )

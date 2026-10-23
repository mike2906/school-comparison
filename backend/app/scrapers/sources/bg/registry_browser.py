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


def browser_response(url: str, result: dict) -> httpx.Response:
    """An httpx.Response from what the page's fetch returned.

    The browser has already decompressed and decoded the body, so the encoding headers go and
    the text is stored as UTF-8.
    """
    content_type = result["headers"].get("content-type", "text/plain").split(";")[0]
    headers = {"content-type": f"{content_type}; charset=utf-8"}
    if "cf-mitigated" in result["headers"]:
        headers["cf-mitigated"] = result["headers"]["cf-mitigated"]
    return httpx.Response(
        result["status"], headers=headers, content=result["text"].encode(), request=httpx.Request("POST", url)
    )


class BrowserClient:
    """The small part of httpx.AsyncClient the register adapter uses (`post`), sent from a browser page."""

    def __init__(self, origin: str, wait_seconds: float = 60.0):
        self.origin = origin
        self.wait_seconds = wait_seconds
        self._playwright = None
        self._browser = None
        self._page = None

    async def __aenter__(self) -> BrowserClient:
        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()
        try:
            self._browser = await self._playwright.chromium.launch(headless=True)
            # Headless Chromium names itself "HeadlessChrome"; send the ordinary browser's name.
            self._page = await self._browser.new_page(
                locale="bg-BG",
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)"
                    f" Chrome/{self._browser.version} Safari/537.36"
                ),
            )
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
        from playwright.async_api import Error as PlaywrightError

        deadline = time.monotonic() + self.wait_seconds
        while True:
            try:
                title = await self._page.title()
            except PlaywrightError:
                title = _CHALLENGE_TITLES[0]  # the challenge page reloading itself as it passes
            if not any(marker in title for marker in _CHALLENGE_TITLES):
                return
            if time.monotonic() > deadline:
                raise RuntimeError(
                    f"{self.origin} kept a headless browser on the Cloudflare challenge for"
                    f" {self.wait_seconds:.0f}s (this connection is screened out too)"
                )
            await asyncio.sleep(1)

    async def post(self, url: str, json: dict | None = None, headers: dict | None = None) -> httpx.Response:
        return browser_response(url, await self._page.evaluate(_FETCH_JS, [url, json or {}, headers or {}]))

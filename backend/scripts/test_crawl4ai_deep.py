"""Prototype test: inspect Crawl4AI navigation output.

Tests navigation quality WITHOUT needing a database or API keys.
Shows content length and categories discovered.

Usage:
  uv run python scripts/test_crawl4ai_deep.py --url https://example-school.bg
  uv run python scripts/test_crawl4ai_deep.py  # uses built-in test URLs
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x:x@localhost:5432/x")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

from app.scrapers.navigator import WebsiteNavigator  # noqa: E402


TEST_URLS = [
    "https://kids-academy.bg",
    "https://albena.bg",
]


def _fmt_content(text: str | None, max_chars: int = 120) -> str:
    if not text:
        return "(empty)"
    preview = text.strip().replace("\n", " ")[:max_chars]
    return f"{preview}... [{len(text)} chars]"


async def _test_url(url: str) -> dict:
    navigator = WebsiteNavigator()
    start = time.perf_counter()
    try:
        final_url, pages = await navigator.discover_pages(url)
        elapsed = time.perf_counter() - start
        contentful = [p for p in pages if p.markdown and p.markdown.strip()]
        return {
            "url": url,
            "final_url": final_url,
            "pages_total": len(pages),
            "pages_contentful": len(contentful),
            "elapsed": elapsed,
            "pages": [
                {
                    "url": p.url,
                    "category": p.category,
                    "content_preview": _fmt_content(p.markdown),
                    "content_len": len(p.markdown or ""),
                }
                for p in pages
            ],
        }
    except Exception as exc:
        elapsed = time.perf_counter() - start
        return {
            "url": url,
            "error": str(exc),
            "elapsed": elapsed,
            "pages_total": 0,
            "pages_contentful": 0,
            "pages": [],
        }


def _print_result(r: dict) -> None:
    print(f"\n{'='*70}")
    print(f"URL:      {r['url']}")
    if "error" in r:
        print(f"ERROR:    {r['error']}")
    else:
        print(f"Final:    {r.get('final_url', '?')}")
    print(f"Pages:    {r['pages_total']} total, {r['pages_contentful']} with content")
    print(f"Time:     {r['elapsed']:.1f}s")
    for p in r["pages"]:
        cat = p.get("category") or "unclassified"
        clen = p.get("content_len", 0)
        preview = p.get("content_preview", "(empty)")
        url_short = p["url"][:60]
        print(f"  [{cat:12s}] {url_short}")
        if clen:
            print(f"             {preview}")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Test Crawl4AI navigator without DB.")
    parser.add_argument("--url", default="", help="School URL to test (default: test_urls)")
    args = parser.parse_args()

    urls = [args.url] if args.url else TEST_URLS

    for url in urls:
        print(f"\n{'#'*70}")
        print(f"# Testing: {url}")
        print(f"{'#'*70}")
        result = await _test_url(url)
        _print_result(result)

    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())

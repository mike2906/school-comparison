#!/usr/bin/env python3
import argparse
import json
import os
import re
import sys
import tempfile
import urllib.request
from urllib.parse import urljoin, urlparse
from typing import Iterable, List, Optional, Tuple

import pdfplumber


def parse_pages(pages: Optional[str], total: int) -> List[int]:
    if not pages:
        return list(range(1, total + 1))
    result: List[int] = []
    parts = [p.strip() for p in pages.split(",") if p.strip()]
    for part in parts:
        if "-" in part:
            start_s, end_s = part.split("-", 1)
            start = int(start_s)
            end = int(end_s)
            if start > end:
                start, end = end, start
            result.extend(list(range(start, end + 1)))
        else:
            result.append(int(part))
    # clamp to total and keep order, de-dup
    seen = set()
    ordered: List[int] = []
    for p in result:
        if p < 1 or p > total:
            continue
        if p in seen:
            continue
        seen.add(p)
        ordered.append(p)
    return ordered


def download_to_temp(url: str) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; PDFReader/1.0)"
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = resp.read()
    fd, path = tempfile.mkstemp(suffix=".pdf")
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    return path


def find_pdf_urls(page_url: str) -> List[str]:
    req = urllib.request.Request(
        page_url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; PDFReader/1.0)"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        html = resp.read().decode("utf-8", errors="ignore")
    # Basic href/src scan for .pdf links (case-insensitive)
    candidates = re.findall(r'(?:href|src)=[\"\\\']([^\"\\\']+\\.pdf)(?:\\?[^\"\\\']*)?[\"\\\']', html, flags=re.I)
    # Also scan for bare .pdf URLs in text
    candidates.extend(re.findall(r'(https?://[^\"\\\']+\\.pdf)(?:\\?[^\"\\\']*)?', html, flags=re.I))
    out: List[str] = []
    seen = set()
    for c in candidates:
        if not c:
            continue
        full = urljoin(page_url, c)
        # Keep only http(s)
        parsed = urlparse(full)
        if parsed.scheme not in ("http", "https"):
            continue
        if full in seen:
            continue
        seen.add(full)
        out.append(full)
    return out


def extract_pdf(path: str, pages: Optional[str], include_tables: bool) -> Tuple[List[dict], str]:
    results: List[dict] = []
    text_out: List[str] = []
    with pdfplumber.open(path) as pdf:
        page_numbers = parse_pages(pages, len(pdf.pages))
        for page_no in page_numbers:
            page = pdf.pages[page_no - 1]
            text = page.extract_text() or ""
            page_result = {
                "page": page_no,
                "text": text,
            }
            if include_tables:
                tables = page.extract_tables() or []
                page_result["tables"] = tables
            results.append(page_result)
            if text:
                text_out.append(text)
    return results, "\n\n".join(text_out).strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract text and tables from PDFs.")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--url", help="PDF URL")
    src.add_argument("--file", help="Local PDF file path")
    src.add_argument("--page-url", help="Web page URL to discover PDF links")
    parser.add_argument("--pages", help="Page selection, e.g. '1-2,5'")
    parser.add_argument("--tables", action="store_true", help="Include tables in JSON output")
    parser.add_argument("--format", choices=["text", "json"], default="text")
    parser.add_argument("--output", help="Output file path (optional)")
    parser.add_argument("--list-pdfs", action="store_true", help="Only list discovered PDF URLs")
    args = parser.parse_args()

    tmp_path = None
    try:
        if args.page_url:
            pdf_urls = find_pdf_urls(args.page_url)
            if args.list_pdfs:
                output = "\n".join(pdf_urls)
                if args.output:
                    with open(args.output, "w", encoding="utf-8") as f:
                        f.write(output)
                else:
                    sys.stdout.write(output)
                return 0
            if not pdf_urls:
                raise SystemExit("No PDF URLs found on page.")
            # Use first PDF found
            tmp_path = download_to_temp(pdf_urls[0])
            path = tmp_path
        elif args.url:
            tmp_path = download_to_temp(args.url)
            path = tmp_path
        else:
            path = args.file

        results, text_out = extract_pdf(path, args.pages, args.tables)

        if args.format == "json":
            payload = {"pages": results}
            output = json.dumps(payload, ensure_ascii=True, indent=2)
        else:
            output = text_out

        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(output)
        else:
            sys.stdout.write(output)

        return 0
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


if __name__ == "__main__":
    raise SystemExit(main())

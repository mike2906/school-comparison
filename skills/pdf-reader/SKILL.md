---
name: pdf-reader
description: Extract text and tables from PDFs (local files, PDF URLs, or PDFs linked from a web page) with a small local pdfplumber script. Use when a school website links fee or admissions PDFs and you need structured data from them.
---

# PDF Reader Skill

Use this skill when the user needs to read or extract content from a PDF. It supports local files and URLs.

## Quick Start

1. Run the extractor script with a URL, a page URL (auto-discover PDFs), or a file path.
2. Use JSON output if you want page-structured text and tables.

Example:

```bash
cd backend  # pdfplumber is a backend dependency
uv run python ../skills/pdf-reader/scripts/extract_pdf.py --url "https://example.com/file.pdf" --format json --tables
```

## Notes

- The script uses `pdfplumber` for text/table extraction.
- For URL downloads, it writes to a temporary file and cleans up automatically.
- Use `--pages` to limit extraction (e.g., `1-2,5`).
- Use `--page-url` to discover PDF links; add `--list-pdfs` to print them.

"""Print the text of an official page or PDF, for the rule-update drafting workflow.

Only hosts the admission rules already rely on can be read: the hosts of every source in
the rule files and the watch config, plus Bulgarian government and court domains. That
keeps an agent reading untrusted page text from sending data anywhere else.

    uv run python -m scripts.fetch_official <url> [--max-chars 200000]
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

import httpx
import pdfplumber

from app.services.admission_rule_watch import SOURCES_DIR, USER_AGENT, clean_text

RULES_DIR = Path(__file__).resolve().parents[2] / "frontend" / "src" / "admission" / "rules"
OFFICIAL_SUFFIXES = (
    "sofia.bg",
    "government.bg",
    "justice.bg",
    "parliament.bg",
    "mon.bg",
    "strategy.bg",
    "bta.bg",
)


def _urls(value: object) -> list[str]:
    if isinstance(value, dict):
        return [u for v in value.values() for u in _urls(v)]
    if isinstance(value, list):
        return [u for v in value for u in _urls(v)]
    return [value] if isinstance(value, str) and value.startswith("http") else []


def allowed_hosts() -> set[str]:
    hosts: set[str] = set()
    for path in [*RULES_DIR.rglob("*.json"), *SOURCES_DIR.glob("*/admission_rule_watch.json")]:
        hosts.update(urlparse(u).hostname or "" for u in _urls(json.loads(path.read_text("utf-8"))))
    hosts.discard("")
    return hosts


def is_allowed(url: str, hosts: set[str]) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in ("http", "https") or not host:
        return False
    return host in hosts or any(host == s or host.endswith("." + s) for s in OFFICIAL_SUFFIXES)


def fetch_text(url: str, hosts: set[str]) -> str:
    def check(request: httpx.Request) -> None:
        if not is_allowed(str(request.url), hosts):
            raise ValueError(f"not an allowed official host: {request.url.host}")

    with httpx.Client(
        timeout=60.0, follow_redirects=True, headers={"User-Agent": USER_AGENT}, event_hooks={"request": [check]}
    ) as client:
        response = client.get(url)
        response.raise_for_status()
    if "pdf" in response.headers.get("content-type", "") or response.content[:5] == b"%PDF-":
        with pdfplumber.open(io.BytesIO(response.content)) as pdf:
            return "\n\n".join(page.extract_text() or "" for page in pdf.pages)
    if "html" in response.headers.get("content-type", ""):
        return clean_text(response.text)
    return response.text


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("url")
    parser.add_argument("--max-chars", type=int, default=200_000)
    args = parser.parse_args()
    hosts = allowed_hosts()
    if not is_allowed(args.url, hosts):
        sys.exit(f"Refused: {args.url} is not on an official host the rules use.")
    print(fetch_text(args.url, hosts)[: args.max_chars])


if __name__ == "__main__":
    main()

"""Check the official admission-rule sources and open a GitHub issue for each change.

Run daily by .github/workflows/watch-admission-rules.yml; needs no database. Without
``--issues`` it only prints what it found and leaves the state unchanged, so a trial run
does not swallow a change.

    uv run python -m scripts.watch_admission_rules --state ../rule-watch-state \
        --rules ../frontend/src/admission/rules [--issues]

Docs: docs/ADMISSION_POINTS.md → Keeping rules current.
"""

from __future__ import annotations

import argparse
import logging
import os
import time
from pathlib import Path

import httpx

from app.services.admission_rule_watch import (
    USER_AGENT,
    Finding,
    load_config,
    load_rule_files,
    load_state,
    make_fetcher,
    run,
    save_state,
)

logger = logging.getLogger("watch_admission_rules")

LABEL = "rule-watch"
FOOTER = "\n\n---\n_Opened by the admission rule watch (.github/workflows/watch-admission-rules.yml)._"
MAX_BODY = 60_000  # GitHub rejects issue and comment bodies over 65,536 characters


def merge(findings: list[Finding]) -> list[Finding]:
    """One finding per key; bodies of repeated keys (several new council items) are joined."""
    merged: dict[str, Finding] = {}
    for finding in findings:
        if finding.key in merged:
            merged[finding.key].body += "\n\n---\n\n" + finding.body
        else:
            merged[finding.key] = Finding(**vars(finding))
    return list(merged.values())


def _body(finding: Finding) -> str:
    body = finding.body
    if len(body) > MAX_BODY:
        body = body[:MAX_BODY] + "\n\n… (cut; the full text is in the workflow log)"
    return body + FOOTER


def _send(gh: httpx.Client, url: str, payload: dict) -> httpx.Response:
    """POST with up to three tries, so a passing GitHub error does not drop a finding."""
    for attempt in range(1, 4):
        try:
            response = gh.post(url, json=payload)
        except httpx.TransportError:
            if attempt == 3:
                raise
        else:
            if (response.status_code < 500 and response.status_code != 429) or attempt == 3:
                break
        time.sleep(attempt * 5)
    response.raise_for_status()
    return response


def post_issues(findings: list[Finding], repo: str, token: str) -> None:
    """Open an issue per finding, or comment on the open one with the same title.

    Non-repeatable findings (missing files, unreachable sources) that already have an
    open issue are skipped, so a daily run does not repeat them.
    """
    api = f"https://api.github.com/repos/{repo}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    with httpx.Client(timeout=30.0, headers=headers) as gh:
        gh.post(f"{api}/labels", json={"name": LABEL, "color": "d4c5f9", "description": "Admission rule sources changed"})
        response = gh.get(f"{api}/issues", params={"labels": LABEL, "state": "open", "per_page": 100})
        response.raise_for_status()
        open_issues = {issue["title"]: issue["number"] for issue in response.json()}
        for finding in findings:
            number = open_issues.get(finding.title)
            if number and not finding.repeatable:
                logger.info("Already open: #%s %s", number, finding.title)
                continue
            if number:
                _send(gh, f"{api}/issues/{number}/comments", {"body": _body(finding)})
                logger.info("Commented on #%s %s", number, finding.title)
            else:
                created = _send(gh, f"{api}/issues", {"title": finding.title, "body": _body(finding), "labels": [LABEL]})
                open_issues[finding.title] = created.json()["number"]
                logger.info("Opened #%s %s", created.json()["number"], finding.title)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--state", type=Path, required=True, help="checkout of the rule-watch-data branch")
    parser.add_argument("--rules", type=Path, required=True, help="frontend/src/admission/rules")
    parser.add_argument("--country", default="bg")
    parser.add_argument("--issues", action="store_true", help="open GitHub issues (needs GITHUB_TOKEN, GITHUB_REPOSITORY)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    state = load_state(args.state)
    rules = load_rule_files(args.rules / args.country)
    with httpx.Client(timeout=45.0, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
        result = run(load_config(args.country), rules, state, make_fetcher(client))
    for line in result.report:
        print(line)
    findings = merge(result.findings)
    for finding in findings:
        print(f"\n## {finding.title}\n{finding.body}")
    print(f"\n{len(findings)} finding(s)")
    if not args.issues:
        print("Trial run: state not saved.")
        return
    # Post before saving: if posting fails, the next run sees the same change again.
    if findings:
        post_issues(findings, os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_TOKEN"])
    save_state(args.state, result.state)


if __name__ == "__main__":
    main()

"""Check the official admission-rule sources and open a GitHub issue for each change.

Run daily by .github/workflows/watch-admission-rules.yml; needs no database. Without
``--issues`` it only prints what it found (and still updates the state directory).

    uv run python -m scripts.watch_admission_rules --state ../rule-watch-state \
        --rules ../frontend/src/admission/rules [--issues]

Docs: docs/ADMISSION_POINTS.md → Keeping rules current.
"""

from __future__ import annotations

import argparse
import logging
import os
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


def merge(findings: list[Finding]) -> list[Finding]:
    """One finding per key; bodies of repeated keys (several new council items) are joined."""
    merged: dict[str, Finding] = {}
    for finding in findings:
        if finding.key in merged:
            merged[finding.key].body += "\n\n---\n\n" + finding.body
        else:
            merged[finding.key] = Finding(**vars(finding))
    return list(merged.values())


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
                gh.post(f"{api}/issues/{number}/comments", json={"body": finding.body + FOOTER}).raise_for_status()
                logger.info("Commented on #%s %s", number, finding.title)
            else:
                created = gh.post(
                    f"{api}/issues", json={"title": finding.title, "body": finding.body + FOOTER, "labels": [LABEL]}
                )
                created.raise_for_status()
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
    if args.issues and findings:
        # Post before saving: if posting fails, the next run sees the same change again.
        post_issues(findings, os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_TOKEN"])
    save_state(args.state, result.state)
    print(f"\n{len(findings)} finding(s)")


if __name__ == "__main__":
    main()

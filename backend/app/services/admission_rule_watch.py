"""Watch the official sources behind the admission rule files and flag changes.

Rules change every one to three years, usually by a council decision in February or
March, and sometimes by a court ruling. Nothing here rewrites a rule: the watcher only
notices that something changed, or that a campaign's rules are missing or still
provisional when they should be confirmed, and reports it so a person or an agent adds
the next rule file (``docs/ADMISSION_POINTS.md`` → Keeping rules current).

Targets are listed per country in ``app/scrapers/sources/<country>/admission_rule_watch.json``:

- ``page``: the cleaned text of one page; any change is reported with a diff.
- ``json``: a JSON endpoint, compared in canonical form (for example the ИСОДЗ calendar).
- ``listing``: a list page; each new link matching ``link_pattern`` is checked for
  ``keywords`` (in its link text, or in the linked page when ``follow`` is set). The
  first run only records the links already there.

The same file names the date checks per rule family (``families``). State (hashes, texts,
seen links, failure counts) lives on the ``rule-watch-data`` branch, written by
``.github/workflows/watch-admission-rules.yml``.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urljoin
from urllib.robotparser import RobotFileParser

from bs4 import BeautifulSoup

SOURCES_DIR = Path(__file__).resolve().parent.parent / "scrapers" / "sources"
# sofia.bg rejects user agents without a browser-style prefix ("Unsupported Browser").
ROBOTS_NAME = "SchoolDeciderBot"  # the crawler name in app/config.py CRAWLER_USER_AGENT
USER_AGENT = "Mozilla/5.0 (compatible; schooldecider.com admission rule watch; +https://schooldecider.com)"
FAILURES_BEFORE_REPORT = 3
MAX_DIFF_LINES = 80
MAX_FOLLOW_PER_RUN = 15
FOLLOW_ATTEMPTS = 3
FOLLOWED_MIN_CHARS = 200
RECHECK_DAYS = 21
STATE_FILE = "state.json"


@dataclass
class Finding:
    """One thing a person or agent should look at. ``key`` identifies the open issue."""

    key: str
    title: str
    body: str
    kind: str  # "changed", "new_item", "missing", "provisional", "unreachable"
    repeatable: bool = False  # a new occurrence adds a comment to the open issue


@dataclass
class Fetched:
    url: str
    text: str = ""
    html: str = ""
    error: Optional[str] = None


Fetcher = Callable[[str], Fetched]


@dataclass
class RunResult:
    findings: list[Finding] = field(default_factory=list)
    state: dict[str, Any] = field(default_factory=dict)
    report: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- config


def load_config(country: str) -> dict[str, Any]:
    path = SOURCES_DIR / country / "admission_rule_watch.json"
    return json.loads(path.read_text(encoding="utf-8"))


def load_rule_files(rules_dir: Path) -> list[dict[str, Any]]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(rules_dir.rglob("*.json"))]


def family_of(rule: dict[str, Any]) -> str:
    """``bg/sofia/kindergarten/2026-2027`` -> ``bg/sofia/kindergarten``."""
    return rule["id"].rsplit("/", 1)[0]


# --------------------------------------------------------------------------- text


def clean_text(html: str, select: Optional[str] = None) -> str:
    """Visible text, one block per line, whitespace collapsed, empty lines dropped."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "template", "svg"]):
        tag.decompose()
    root = soup.select_one(select) if select else soup
    if root is None:
        return ""
    lines = (" ".join(line.split()) for line in root.get_text("\n").splitlines())
    return "\n".join(line for line in lines if line)


def canonical_json(raw: str) -> str:
    """Sorted keys, and a top-level list sorted too, so a reordered response is no change."""
    data = json.loads(raw)
    if isinstance(data, list):
        data = sorted(data, key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True))
    return json.dumps(data, ensure_ascii=False, sort_keys=True, indent=1)


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


def matching_lines(text: str, keywords: list[str], context: int = 1) -> list[str]:
    """Lines containing any keyword, with ``context`` lines around each, in page order."""
    lines = text.splitlines()
    wanted = [_norm(k) for k in keywords]
    keep: set[int] = set()
    for i, line in enumerate(lines):
        normalised = _norm(line)
        if any(k in normalised for k in wanted):
            keep.update(range(max(0, i - context), min(len(lines), i + context + 1)))
    return [lines[i] for i in sorted(keep)]


def short_diff(old: str, new: str) -> str:
    diff = list(difflib.unified_diff(old.splitlines(), new.splitlines(), "before", "after", lineterm="", n=1))
    if len(diff) > MAX_DIFF_LINES:
        diff = diff[:MAX_DIFF_LINES] + [f"... {len(diff) - MAX_DIFF_LINES} more diff lines"]
    return "\n".join(diff)


def links(html: str, base_url: str, pattern: str, drop_query: bool = False) -> list[tuple[str, str]]:
    """(absolute url, link text) for each distinct link whose href matches ``pattern``.

    ``drop_query`` strips the query string, for sites that add session or redirect
    parameters to otherwise stable links (an unstable URL would look like a new entry).
    """
    soup = BeautifulSoup(html, "html.parser")
    regex = re.compile(pattern)
    seen: dict[str, str] = {}
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not regex.search(href):
            continue
        url = urljoin(base_url, href).split("#", 1)[0]
        if drop_query:
            url = url.split("?", 1)[0]
        text = " ".join(a.get_text(" ").split())
        if url not in seen or (text and not seen[url]):
            seen[url] = text
    return list(seen.items())


# --------------------------------------------------------------------------- targets


def _affects(target: dict[str, Any]) -> str:
    families = target.get("affects") or []
    return ", ".join(f"`{f}`" for f in families) or "none listed"


def _footer(target: dict[str, Any]) -> str:
    return (
        f"\n\nSource: {target['url']}\nRule families: {_affects(target)}\n"
        f"Why it is watched: {target.get('why', '')}\n\n"
        "If this changes the rules, add a new rule file for the campaign with its acts, "
        "sources and worked examples (docs/ADMISSION_POINTS.md → Keeping rules current); "
        "never edit an old file's meaning. If it does not, close this issue."
    )


def _check_page(target: dict[str, Any], fetched: Fetched, entry: dict[str, Any], result: RunResult) -> None:
    if target["kind"] == "json":
        try:
            text = canonical_json(fetched.text)
        except ValueError as exc:
            raise WatchError(f"not JSON: {exc}") from exc
    else:
        text = clean_text(fetched.html or fetched.text, target.get("select"))
    if len(text) < target.get("min_chars", 50):
        raise WatchError(f"only {len(text)} characters of text (selector or page layout changed?)")
    digest = text_hash(text)
    if entry.get("hash") and entry["hash"] != digest:
        result.findings.append(
            Finding(
                key=f"changed:{target['id']}",
                title=f"Rule watch: {target['title']} changed",
                body=f"The watched text changed since {entry.get('checked_at', 'the last check')}.\n\n"
                f"```diff\n{short_diff(entry.get('text', ''), text)}\n```" + _footer(target),
                kind="changed",
                repeatable=True,
            )
        )
        entry["changed_at"] = _now()
    result.report.append(f"{target['id']}: {len(text)} chars, {'baseline' if not entry.get('hash') else 'ok'}")
    entry["hash"], entry["text"] = digest, text


def _hits(lines: list[str], target: dict[str, Any]) -> list[str]:
    """Lines (with context) holding a keyword and, when the target sets ``also``, one of those too.

    ``also`` gives a bare keyword its context: "Наредба № 10" is also an agriculture
    ordinance's number, and a court name alone says nothing about admissions.
    """
    also = [_norm(a) for a in target.get("also") or []]
    text = "\n".join(line for line in lines if not also or any(a in _norm(line) for a in also))
    return matching_lines(text, target.get("keywords") or [], context=1 if target.get("follow") else 0)


def _new_item(target: dict[str, Any], url: str, link_text: str, hits: list[str], what: str) -> Finding:
    quoted = "\n".join(f"> {line}" for line in hits[:40])
    return Finding(
        key=f"new_item:{target['id']}",
        title=f"Rule watch: {target['title']} mention admission rules",
        body=f"{what}: [{link_text or url}]({url})\n\nMatching lines:\n\n{quoted}" + _footer(target),
        kind="new_item",
        repeatable=True,
    )


def _open(url: str, target: dict[str, Any], fetch: Fetcher) -> tuple[Optional[list[str]], Optional[str]]:
    """The followed page's lines, or an error. A near-empty 200 (a maintenance or bot-check
    page) counts as an error, so the page is retried rather than marked as read."""
    page = fetch(url)
    if page.error:
        return None, page.error
    text = clean_text(page.html or page.text, target.get("select"))
    if len(text) < FOLLOWED_MIN_CHARS:
        return None, f"only {len(text)} characters of text"
    return text.splitlines(), None


def _check_listing(
    target: dict[str, Any], fetched: Fetched, entry: dict[str, Any], result: RunResult, fetch: Fetcher, today: date
) -> None:
    found = links(fetched.html or fetched.text, target["url"], target["link_pattern"], target.get("drop_query", False))
    if len(found) < target.get("min_links", 1):
        raise WatchError(f"{len(found)} links match {target['link_pattern']!r} (page layout changed?)")
    seen: list[str] = entry.get("seen") or []
    baseline = not seen and not entry.get("baselined")
    new = [(url, text) for url, text in found if url not in seen]
    if seen and len(new) == len(found):
        # Every entry on the page is new: older ones may have scrolled past unread.
        result.findings.append(
            Finding(
                key=f"overflow:{target['id']}",
                title=f"Rule watch: {target['title']} may have missed entries",
                body=f"All {len(found)} entries on the page were new since the last check, so earlier ones "
                "may have scrolled to the next page unread. Look through the listing's older pages by hand "
                "back to the last check." + _footer(target),
                kind="unreachable",
                repeatable=True,
            )
        )
    recent: dict[str, dict[str, Any]] = entry.setdefault("recent", {})
    followed = 0
    for url, link_text in new:
        if baseline:
            seen.append(url)
            continue
        if target.get("follow"):
            if followed >= MAX_FOLLOW_PER_RUN:
                break  # the rest stay unseen and are checked next run
            followed += 1
            lines, error = _open(url, target, fetch)
            if error:
                attempts = entry.setdefault("attempts", {})
                attempts[url] = attempts.get(url, 0) + 1
                result.report.append(f"{target['id']}: {url} failed {attempts[url]}x ({error})")
                if attempts[url] < FOLLOW_ATTEMPTS:
                    continue  # retried next run
                del attempts[url]
                hits = [f"(could not be read after {FOLLOW_ATTEMPTS} tries: {error}; check it by hand)"]
            else:
                entry.get("attempts", {}).pop(url, None)
                hits = _hits(lines, target)
                recent[url] = {"since": today.isoformat(), "hits": text_hash("\n".join(hits))}
        else:
            hits = _hits([link_text], target)
        if hits:
            result.findings.append(_new_item(target, url, link_text, hits, "New entry"))
        seen.append(url)
    # Pages followed in the last RECHECK_DAYS are read again: a session page gains its
    # decisions after the session, a consultation its outcome.
    titles = dict(found)
    for url, info in list(recent.items()):
        if (today - date.fromisoformat(info["since"])).days > RECHECK_DAYS:
            del recent[url]
            continue
        if url in [u for u, _ in new] or followed >= MAX_FOLLOW_PER_RUN:
            continue
        followed += 1
        lines, error = _open(url, target, fetch)
        if error:
            continue
        hits = _hits(lines, target)
        digest = text_hash("\n".join(hits))
        if hits and digest != info["hits"]:
            result.findings.append(_new_item(target, url, titles.get(url, ""), hits, "Updated entry"))
        info["hits"] = digest
    entry["seen"] = seen[-max(500, 3 * len(found)) :]
    entry["baselined"] = True
    result.report.append(
        f"{target['id']}: {len(found)} links, {len(new)} new{' (baseline)' if baseline else ''}, followed {followed}"
    )


class WatchError(Exception):
    pass


def check_targets(
    targets: list[dict[str, Any]], state: dict[str, Any], fetch: Fetcher, result: RunResult, today: date
) -> None:
    for target in targets:
        entry = state.setdefault("targets", {}).setdefault(target["id"], {})
        fetched = fetch(target["url"])
        try:
            if fetched.error:
                raise WatchError(fetched.error)
            if target["kind"] == "listing":
                _check_listing(target, fetched, entry, result, fetch, today)
            elif target["kind"] in ("page", "json"):
                _check_page(target, fetched, entry, result)
            else:
                raise ValueError(f"unknown target kind {target['kind']!r}")
        except Exception as exc:  # noqa: BLE001 - one broken target must not stop the others
            entry["failures"] = entry.get("failures", 0) + 1
            entry["last_error"] = str(exc)
            result.report.append(f"{target['id']}: FAILED {entry['failures']}x: {exc}")
            if entry["failures"] >= FAILURES_BEFORE_REPORT:
                result.findings.append(
                    Finding(
                        key=f"unreachable:{target['id']}",
                        title=f"Rule watch: cannot read {target['title']}",
                        body=f"The last {entry['failures']} checks failed. Latest error: {exc}\n\n"
                        "Changes to this source are not being noticed. Fix the target in "
                        "`admission_rule_watch.json` (new URL, selector or link pattern) or remove it."
                        + _footer(target),
                        kind="unreachable",
                    )
                )
            continue
        entry["failures"] = 0
        entry.pop("last_error", None)
        entry["checked_at"] = _now()


# --------------------------------------------------------------------------- calendar


def _due(year: int, month_day: str) -> date:
    month, day = (int(part) for part in month_day.split("-"))
    return date(year, month, day)


def check_families(
    families: dict[str, dict[str, Any]], rules: list[dict[str, Any]], today: date, result: RunResult
) -> None:
    """Missing next-campaign files and provisional files past their confirmation date.

    A campaign ``Y/Y+1`` is the school year the child starts; its applications run in
    year ``Y``. ``next_campaign_by`` (MM-DD of year Y) is when its file must exist,
    ``confirm_by`` (MM-DD of year Y) when a provisional file must have been re-checked.
    """
    by_family: dict[str, dict[str, dict[str, Any]]] = {}
    for rule in rules:
        by_family.setdefault(family_of(rule), {})[rule["campaign"]] = rule
    for family, config in families.items():
        files = by_family.get(family, {})
        year = today.year
        campaign = f"{year}/{year + 1}"
        rule = files.get(campaign)
        if today >= _due(year, config["next_campaign_by"]) and rule is None:
            result.findings.append(
                Finding(
                    key=f"missing:{family}:{campaign}",
                    title=f"Rule watch: no {campaign} rules for {family}",
                    body=f"Applications for {campaign} run in {year}, and `{family}` has no rule file for it, "
                    "so the calculator shows older rules as an illustration only. Check the official "
                    "sources and add the file (verified, or provisional with a note).\n\n"
                    f"Sources to start from: {config.get('note', '')}",
                    kind="missing",
                )
            )
        if (
            rule is not None
            and (rule.get("verified") or {}).get("status") == "provisional"
            and today >= _due(year, config["confirm_by"])
        ):
            result.findings.append(
                Finding(
                    key=f"provisional:{family}:{campaign}",
                    title=f"Rule watch: confirm the {campaign} rules for {family}",
                    body=f"`{rule['id']}` is still provisional (checked {rule['verified'].get('on')}), and "
                    f"the {campaign} schedule and any amendments should be out by now. Re-check the "
                    "sources; add a verified file for the campaign (or a new provisional one with a "
                    "note on what is still missing).\n\n"
                    f"Sources to start from: {config.get('note', '')}",
                    kind="provisional",
                )
            )
        result.report.append(f"{family}: campaigns {sorted(files)}")


# --------------------------------------------------------------------------- run


def run(
    config: dict[str, Any],
    rules: list[dict[str, Any]],
    state: dict[str, Any],
    fetch: Fetcher,
    today: Optional[date] = None,
) -> RunResult:
    result = RunResult(state=state)
    today = today or date.today()
    check_targets(config.get("targets", []), state, fetch, result, today)
    check_families(config.get("families", {}), rules, today, result)
    state["last_run"] = _now()
    return result


def load_state(state_dir: Path) -> dict[str, Any]:
    path = state_dir / STATE_FILE
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def save_state(state_dir: Path, state: dict[str, Any]) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / STATE_FILE).write_text(json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True) + "\n", "utf-8")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- http


def make_fetcher(client: Any, delay: float = 1.0) -> Fetcher:
    """A fetcher over an ``httpx.Client`` that honours robots.txt and spaces requests."""
    import time

    robots: dict[str, Optional[RobotFileParser]] = {}

    def allowed(url: str) -> bool:
        root = urljoin(url, "/")
        if root not in robots:
            parser: Optional[RobotFileParser] = RobotFileParser()
            try:
                response = client.get(urljoin(root, "robots.txt"))
                if response.status_code in (401, 403):
                    parser.disallow_all = True  # as RobotFileParser.read() treats them
                elif response.status_code >= 400:
                    parser = None  # no robots.txt: everything allowed
                else:
                    parser.parse(response.text.splitlines())
            except Exception:  # noqa: BLE001 - unreachable robots.txt: let the page fetch decide
                parser = None
            robots[root] = parser
        parser = robots[root]
        # Match rules addressed to our crawler name, not to "Mozilla" (the UA's first token).
        return parser is None or parser.can_fetch(ROBOTS_NAME, url)

    def fetch(url: str) -> Fetched:
        if not allowed(url):
            return Fetched(url, error="robots.txt disallows this URL")
        time.sleep(delay)
        try:
            response = client.get(url)
        except Exception as exc:  # noqa: BLE001 - every network error is a failed check
            return Fetched(url, error=f"{type(exc).__name__}: {exc}")
        if response.status_code >= 400:
            return Fetched(url, error=f"HTTP {response.status_code}")
        is_html = "html" in response.headers.get("content-type", "")
        return Fetched(url, text=response.text, html=response.text if is_html else "")

    return fetch

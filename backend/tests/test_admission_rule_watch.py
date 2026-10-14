"""Admission rule watch: change detection, listings, date checks, and config coverage."""

import json
import re
from datetime import date
from pathlib import Path

from app.scrapers.sources.bg import ruo_sofia_min_max
from app.scrapers.sources.bg.kg_sofia_ratings import RULE_VERSIONS
from app.services.admission_rule_watch import (
    FAILURES_BEFORE_REPORT,
    Fetched,
    clean_text,
    family_of,
    load_config,
    load_rule_files,
    matching_lines,
    run,
)
from scripts.watch_admission_rules import merge

RULES_DIR = Path(__file__).resolve().parents[2] / "frontend" / "src" / "admission" / "rules"


def _fetcher(pages):
    calls = []

    def fetch(url):
        calls.append(url)
        value = pages.get(url)
        if value is None:
            return Fetched(url, error="HTTP 404")
        return Fetched(url, text=value, html=value if value.lstrip().startswith("<") else "")

    fetch.calls = calls
    return fetch


PAGE = {"id": "p", "title": "Ordinance page", "kind": "page", "url": "https://x.bg/o", "select": "main", "why": "w"}
LISTING = {
    "id": "council",
    "title": "Council sessions",
    "kind": "listing",
    "url": "https://x.bg/sessions",
    "link_pattern": "/content/",
    "follow": True,
    "keywords": ["прием на деца"],
    "affects": ["bg/sofia/kindergarten"],
}


def _html(body):
    return f"<html><body><nav>menu</nav><main>{body}</main><footer>© 2026</footer></body></html>"


def _sessions(*ids):
    return _html("".join(f'<a href="/content/{i}">Заседание №{i}</a>' for i in ids))


def test_clean_text_keeps_selected_visible_text():
    html = "<html><script>x=1</script><main><p>Чл. 18  т. 1</p>\n<p></p><p>8 точки</p></main><footer>f</footer></html>"
    assert clean_text(html, "main") == "Чл. 18 т. 1\n8 точки"
    assert clean_text(html, ".missing") == ""


def test_page_baseline_then_change_reports_diff():
    text = "Адрес: 5 точки. " * 10
    state = {}
    first = run({"targets": [PAGE]}, [], state, _fetcher({PAGE["url"]: _html(text)}))
    assert first.findings == []
    assert state["targets"]["p"]["hash"]

    second = run({"targets": [PAGE]}, [], state, _fetcher({PAGE["url"]: _html(text)}))
    assert second.findings == []

    changed = run({"targets": [PAGE]}, [], state, _fetcher({PAGE["url"]: _html(text.replace("5", "8"))}))
    [finding] = changed.findings
    assert finding.kind == "changed" and finding.repeatable
    assert "-Адрес: 5 точки." in finding.body and "+Адрес: 8 точки." in finding.body
    assert "https://x.bg/o" in finding.body


def test_footer_churn_outside_selector_is_ignored():
    text = "Наредба за прием " * 10
    state = {}
    run({"targets": [PAGE]}, [], state, _fetcher({PAGE["url"]: _html(text)}))
    other_footer = _html(text).replace("© 2026", "© 2027")
    assert run({"targets": [PAGE]}, [], state, _fetcher({PAGE["url"]: other_footer})).findings == []


def test_json_target_compares_canonical_form():
    target = {"id": "cal", "title": "Calendar", "kind": "json", "url": "https://x.bg/cal", "min_chars": 10}
    state = {}
    run({"targets": [target]}, [], state, _fetcher({target["url"]: '[{"start": "2026-05-22", "title": "a"}]'}))
    reordered = '[{"title": "a",   "start": "2026-05-22"}]'
    assert run({"targets": [target]}, [], state, _fetcher({target["url"]: reordered})).findings == []
    two = '[{"start": "2026-05-22", "title": "a"}, {"start": "2026-06-05", "title": "c"}]'
    run({"targets": [target]}, [], state, _fetcher({target["url"]: two}))
    reordered = '[{"start": "2026-06-05", "title": "c"}, {"start": "2026-05-22", "title": "a"}]'
    assert run({"targets": [target]}, [], state, _fetcher({target["url"]: reordered})).findings == []
    added = '[{"start": "2026-05-22", "title": "a"}, {"start": "2026-06-05", "title": "c"}, {"start": "2027-05-21", "title": "b"}]'
    [finding] = run({"targets": [target]}, [], state, _fetcher({target["url"]: added})).findings
    assert "2027-05-21" in finding.body


def test_listing_baselines_then_reports_only_new_matching_items():
    state = {}
    fetch = _fetcher({LISTING["url"]: _sessions(1, 2)})
    assert run({"targets": [LISTING]}, [], state, fetch).findings == []
    assert fetch.calls == [LISTING["url"]]  # the baseline does not open old sessions

    pages = {
        LISTING["url"]: _sessions(1, 2, 3, 4),
        "https://x.bg/content/3": _html("<p>т. 5 Наредба за изменение на Наредбата за прием на деца в общинските детски градини</p>"),
        "https://x.bg/content/4": _html("<p>т. 1 Бюджет</p>"),
    }
    result = run({"targets": [LISTING]}, [], state, _fetcher(pages))
    [finding] = result.findings
    assert finding.kind == "new_item"
    assert "https://x.bg/content/3" in finding.body and "Наредбата за прием на деца" in finding.body
    assert "content/4" not in finding.body

    again = run({"targets": [LISTING]}, [], state, _fetcher(pages))
    assert again.findings == []


def test_listing_retries_a_new_page_that_failed_to_load():
    state = {}
    run({"targets": [LISTING]}, [], state, _fetcher({LISTING["url"]: _sessions(1)}))
    pages = {LISTING["url"]: _sessions(1, 2)}
    assert run({"targets": [LISTING]}, [], state, _fetcher(pages)).findings == []
    pages["https://x.bg/content/2"] = _html("<p>Прием на деца: нови правила</p>")
    [finding] = run({"targets": [LISTING]}, [], state, _fetcher(pages)).findings
    assert "content/2" in finding.body


def test_listing_reports_a_new_page_that_never_opens():
    state = {}
    run({"targets": [LISTING]}, [], state, _fetcher({LISTING["url"]: _sessions(1)}))
    pages = {LISTING["url"]: _sessions(1, 2)}
    for _ in range(2):
        assert run({"targets": [LISTING]}, [], state, _fetcher(pages)).findings == []
    [finding] = run({"targets": [LISTING]}, [], state, _fetcher(pages)).findings
    assert "could not be opened" in finding.body and "content/2" in finding.body
    assert run({"targets": [LISTING]}, [], state, _fetcher(pages)).findings == []


def test_broken_target_does_not_stop_the_others():
    broken = dict(LISTING, id="broken", link_pattern="(")
    state = {}
    result = run({"targets": [broken, PAGE]}, [], state, _fetcher({LISTING["url"]: _sessions(1), PAGE["url"]: _html("x" * 60)}))
    assert state["targets"]["broken"]["failures"] == 1
    assert state["targets"]["p"]["hash"]
    assert result.findings == []


def test_listing_without_follow_matches_link_text():
    target = dict(LISTING, follow=False, keywords=["Наредба № 10"], link_pattern=".")
    state = {}
    run({"targets": [target]}, [], state, _fetcher({target["url"]: _html('<a href="/a">Старо</a>')}))
    page = _html('<a href="/a">Старо</a><a href="/b">Проект на наредба № 10 за изменение</a><a href="/c">Друго</a>')
    [finding] = run({"targets": [target]}, [], state, _fetcher({target["url"]: page})).findings
    assert "https://x.bg/b" in finding.body and "/c" not in finding.body


def test_listing_drop_query_keeps_links_stable():
    target = dict(LISTING, drop_query=True)
    state = {}
    run({"targets": [target]}, [], state, _fetcher({target["url"]: _html('<a href="/content/1?redirect=a">S1</a>')}))
    moved = _html('<a href="/content/1?redirect=b&amp;x=1">S1</a>')
    fetch = _fetcher({target["url"]: moved})
    assert run({"targets": [target]}, [], state, fetch).findings == []
    assert fetch.calls == [target["url"]]


def test_unreadable_source_is_reported_after_repeated_failures():
    state = {}
    for attempt in range(1, FAILURES_BEFORE_REPORT + 1):
        result = run({"targets": [LISTING]}, [], state, _fetcher({LISTING["url"]: _html("<p>no links</p>")}))
        assert bool(result.findings) == (attempt == FAILURES_BEFORE_REPORT)
    [finding] = result.findings
    assert finding.kind == "unreachable" and not finding.repeatable
    run({"targets": [LISTING]}, [], state, _fetcher({LISTING["url"]: _sessions(1)}))
    assert state["targets"]["council"]["failures"] == 0


def _rule(family, campaign, status):
    return {"id": f"{family}/{campaign.replace('/', '-')}", "campaign": campaign, "verified": {"status": status, "on": "2026-10-14"}}


FAMILIES = {"bg/sofia/kindergarten": {"next_campaign_by": "01-05", "confirm_by": "01-10"}}


def test_missing_next_campaign_file_is_reported_after_its_date():
    rules = [_rule("bg/sofia/kindergarten", "2026/2027", "verified")]
    assert run({"families": FAMILIES}, rules, {}, _fetcher({}), today=date(2026, 12, 31)).findings == []
    [finding] = run({"families": FAMILIES}, rules, {}, _fetcher({}), today=date(2027, 1, 5)).findings
    assert finding.kind == "missing" and "2027/2028" in finding.title


def test_provisional_file_must_be_confirmed_by_its_date():
    rules = [_rule("bg/sofia/kindergarten", "2026/2027", "verified"), _rule("bg/sofia/kindergarten", "2027/2028", "provisional")]
    assert run({"families": FAMILIES}, rules, {}, _fetcher({}), today=date(2027, 1, 9)).findings == []
    [finding] = run({"families": FAMILIES}, rules, {}, _fetcher({}), today=date(2027, 1, 10)).findings
    assert finding.kind == "provisional"
    rules[1]["verified"]["status"] = "verified"
    assert run({"families": FAMILIES}, rules, {}, _fetcher({}), today=date(2027, 3, 1)).findings == []


def test_merge_joins_findings_with_the_same_key():
    state = {}
    run({"targets": [LISTING]}, [], state, _fetcher({LISTING["url"]: _sessions(1)}))
    pages = {LISTING["url"]: _sessions(1, 2, 3)}
    for i in (2, 3):
        pages[f"https://x.bg/content/{i}"] = _html(f"<p>Решение {i}: прием на деца</p>")
    [finding] = merge(run({"targets": [LISTING]}, [], state, _fetcher(pages)).findings)
    assert "content/2" in finding.body and "content/3" in finding.body


def test_matching_lines_keeps_context():
    text = "a\nb\nНаредба за ПРИЕМ НА ДЕЦА\nc\nd"
    assert matching_lines(text, ["прием на деца"]) == ["b", "Наредба за ПРИЕМ НА ДЕЦА", "c"]


# ------------------------------------------------------------------ config coverage


def test_every_rule_family_is_watched():
    config = load_config("bg")
    families = {family_of(rule) for rule in load_rule_files(RULES_DIR / "bg")}
    assert families == set(config["families"]), "add the family's dates to admission_rule_watch.json"
    month_day = re.compile(r"^\d\d-\d\d$")
    for family in config["families"].values():
        assert month_day.match(family["next_campaign_by"]) and month_day.match(family["confirm_by"])
    for target in config["targets"]:
        assert target["kind"] in {"page", "json", "listing"}
        assert set(target.get("affects", [])) <= families
        if target["kind"] == "listing":
            re.compile(target["link_pattern"])
            assert target["keywords"]


def test_capture_rule_versions_match_the_rule_files():
    """Cut-off rows are tagged with the rules version in force; it must exist as a rule file version."""
    rules = load_rule_files(RULES_DIR / "bg")
    captures = {f"bg/sofia/{family}": versions for family, versions in RULE_VERSIONS.items()}
    captures["bg/grade_8"] = ruo_sofia_min_max.RULE_VERSIONS
    for family, versions in captures.items():
        files = [r for r in rules if family_of(r) == family]
        assert files, family
        assert max(versions) == max(r["version"] for r in files), (
            f"RULE_VERSIONS for {family} is behind the newest rule file; add the new version"
        )
        acts = {act["id"] for r in files for act in r.get("acts", [])}
        assert set(versions) <= acts, f"RULE_VERSIONS for {family} names a version no rule file records"


def test_config_is_valid_json_with_unique_ids():
    config = load_config("bg")
    ids = [t["id"] for t in config["targets"]]
    assert len(ids) == len(set(ids))
    json.dumps(config)

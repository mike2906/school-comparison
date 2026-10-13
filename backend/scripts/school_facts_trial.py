"""Trial: concrete school facts plus a short paragraph, read from each school's own pages.

The old summariser (``app/ai/summariser.py``) wrote from second-hand extracted lists and was
told to leave out every number, so it came out generic. This trial reuses the fees agent's
page finder with its own link-picking prompt, asks one model call for the concrete facts a
parent comparing schools would use (each with the page line it came from), keeps a fact only
when that line is on the page, and writes a paragraph from the kept facts alone. For
comparison it also runs the old summariser on the same school when it can.

Nothing is written to the database and no pipeline run is opened. The script stops before a
call that could take the total past ``--max-usd``. Run from ``backend/``::

    uv run python scripts/school_facts_trial.py --out ../facts_trial
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
import sys
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, Field
from sqlalchemy import func, select

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.ai.client import create_agent, extract_provider_cost_usd, get_model, get_model_costs  # noqa: E402
from app.config import CRAWLER_USER_AGENT, get_settings  # noqa: E402
from app.database import async_session_maker  # noqa: E402
from app.main import app  # noqa: E402,F401  (loads every model)
from app.models import School  # noqa: E402
from app.scrapers.fee_pages import fee_http_client, page_key  # noqa: E402
from app.scrapers.stages import fees_agent  # noqa: E402

READER_TIER = "pricing"
READER_OUTPUT_TOKENS_RESERVE = 4000
MAX_READ_CHARS = 100_000

PICK_PROMPT = """You help find the pages of a school website that describe what the school is like for a child
and a parent: about the school, curriculum or programme, subjects and languages taught, profiled classes,
daily schedule and after-school care, admissions, clubs and activities, transport, meals, facilities,
support for children with special needs. You get the institution's name, the pages read so far, and the
same-site links not yet opened, as "URL | link text". Return up to 4 URLs, copied exactly from the list,
most useful first. Skip news, galleries, events, contacts, documents and policies, and fee pages.
Return none if nothing fits."""

# When the picker returns nothing, follow links whose URL or text names one of these topics.
_FACTS_LINK_RE = re.compile(
    r"за[\s-]?нас|about|програм|учебн|curricul|прием|admission|дневен|режим|занималн|целодневн|"
    r"извънклас|клуб|club|activit|транспорт|transport|хран|meal|езиц|language|профил",
    re.IGNORECASE,
)

READER_PROMPT = """You read a school's own web pages (each starts with "=== PAGE <url>") and list the concrete facts
a parent comparing schools would use. Good facts are specific and checkable, for example:
- curriculum, programme or qualification (Cambridge, IB, Bulgarian state programme, Montessori)
- which languages are taught, from which grade, how many hours a week, native-speaker teachers
- profiled or specialised classes (maths, languages, arts, sport) and the entry grade
- class or group size, the grades or ages taken
- school day hours, after-school care (занималня, целодневно обучение), holiday camps
- transport (school bus and the areas it covers), meals (own kitchen, catering)
- notable facilities (pool, sports hall, labs), named clubs or teams
- support for children with special educational needs, a psychologist or speech therapist
- how admission works (test, interview, ranking), when stated as the current process
Do not list: fees or prices; exam results or rankings; mission statements, values or claims anyone could
make ("individual approach", "modern facilities", "caring environment", "quality education"); news, events,
competitions won, one-off projects; anything dated to a past school year.
Rules:
- Only what the pages state about this institution. If a page covers a sister school or kindergarten,
  leave its facts out unless they plainly apply to this one.
- For every fact, copy into "quote" the page line it comes from, verbatim and in the page's language,
  and the URL of its page from the PAGE line.
- Write each fact as one short line, in English (fact_en) and Bulgarian (fact_bg). Keep numbers the page
  states.
- At most 8 facts, the most useful for choosing first. Fewer is fine. If the pages state nothing concrete,
  return no facts: that is a correct answer, never pad."""

PARAGRAPH_PROMPT = """You write the short description shown on a school's page in a directory for parents
comparing schools. You get the school's name and type and a list of facts checked against its website.
Write 2 to 3 plain sentences in English and the same in Bulgarian, using only those facts, leading with
what most sets this school apart. Keep the numbers. No praise, no marketing words, nothing the facts do not
say, and do not mention the website or what is missing."""


class Fact(BaseModel):
    topic: str = Field(description="One or two words, e.g. curriculum, languages, class size, transport")
    fact_en: str
    fact_bg: str
    quote: str = Field(description="The page line the fact comes from, copied verbatim")
    source_url: str = Field(description="The URL from the PAGE line of the page the quote is on")


class Facts(BaseModel):
    facts: list[Fact] = Field(description="Concrete facts, at most 8; empty if the pages state none")


class Paragraph(BaseModel):
    en: str
    bg: str


def _normalise(text: str) -> str:
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)  # markdown links keep their text
    text = re.sub(r"[*_#>|`\\]+", " ", text)
    text = text.replace("“", '"').replace("”", '"').replace("„", '"').replace("’", "'")
    text = text.replace("–", "-").replace("—", "-")
    return " ".join(text.casefold().split())


def quote_on_page(fact: Fact, pages: list[fees_agent.PageRead]) -> bool:
    quote = _normalise(fact.quote).strip(" .…")
    if len(quote) < 8:
        return False
    by_key = {page_key(page.url): page for page in pages}
    page = by_key.get(page_key(fact.source_url))
    candidates = [page] if page else pages
    return any(quote in _normalise(candidate.text) for candidate in candidates)


async def _call(spend: fees_agent.Spend, tier: str, prompt: str, schema: type, message: str, school_id: int):
    in_price, out_price = get_model_costs(tier)
    estimate = (len(message) / 3 * in_price + READER_OUTPUT_TOKENS_RESERVE * out_price) / 1_000_000
    if not spend.allows(estimate):
        raise fees_agent.SpendCapReached(f"spent ${spend.spent_usd:.4f} of ${spend.cap_usd}")
    agent = create_agent(tier, prompt, schema, retries=1, model_settings={"temperature": 0})
    result = await asyncio.wait_for(agent.run(message), timeout=150)
    cost = extract_provider_cost_usd(result)
    spend.spent_usd += cost
    return result.output, cost


async def _old_summary(db, school_id: int) -> Any:
    """The old summariser's output for comparison, or why it could not run."""
    from sqlalchemy.orm import selectinload

    from app.ai.summariser import generate_school_summary
    from app.scrapers.summary_pipeline import prepare_summary_candidate

    try:
        school = (
            await db.execute(
                select(School)
                .where(School.id == school_id)
                .options(*[selectinload(rel) for rel in School.__mapper__.relationships])
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        candidate = prepare_summary_candidate(school)
        if candidate.summary_input is None:
            return {"skipped": candidate.reason}
        out = await generate_school_summary(candidate.summary_input, school_id=None)
        return {"mode": out["generation_mode"], "en": out["summary_i18n"]["en"], "cost_usd": out["token_cost_usd"]}
    except Exception as exc:  # comparison only: never stop the trial
        return {"skipped": f"{type(exc).__name__}: {str(exc)[:150]}"}


async def openrouter_credit() -> dict[str, Any]:
    key = get_settings().openrouter_api_key
    headers = {"Authorization": f"Bearer {key}"}
    out: dict[str, Any] = {}
    async with httpx.AsyncClient(timeout=20) as client:
        for name, url in (("credits", "https://openrouter.ai/api/v1/credits"), ("key", "https://openrouter.ai/api/v1/key")):
            try:
                response = await client.get(url, headers=headers)
                data = response.json().get("data") if response.status_code == 200 else None
                # Balances only: the key's label and account ids stay out of the committed report.
                out[name] = (
                    {k: data.get(k) for k in ("total_credits", "total_usage", "usage_daily", "limit_remaining")}
                    if isinstance(data, dict)
                    else f"HTTP {response.status_code}"
                )
            except Exception as exc:
                out[name] = f"failed: {exc}"
    return out


async def sample(db, per_kind: int, seed: int, city: str) -> list[School]:
    base = select(School).where(
        func.lower(School.city) == city,
        School.website_url.isnot(None),
        School.education_level != "kindergarten",
    )
    picked: list[School] = []
    for kinds in (["state"], ["private", "international"]):
        rows = (await db.execute(base.where(School.school_type.in_(kinds)).order_by(School.id))).scalars().all()
        picked += random.Random(seed).sample(list(rows), min(per_kind, len(rows)))
    return picked


async def run_school(crawler, run_config, client, db, school: School, spend: fees_agent.Spend) -> dict[str, Any]:
    name = (school.name_i18n or {}).get("bg") or (school.name_i18n or {}).get("en")
    report: dict[str, Any] = {
        "school_id": school.id,
        "name": name,
        "name_en": (school.name_i18n or {}).get("en"),
        "type": f"{school.school_type} {school.education_level}",
        "website": school.website_url,
    }
    before = spend.spent_usd
    pages = await fees_agent.find_pages(crawler, run_config, client, school, spend)
    readable = [page for page in pages if page.text.strip()]
    report["pages"] = [{"url": p.url, "chars": len(p.text), "note": p.note} for p in pages]
    if not readable:
        report["outcome"] = "no readable pages"
    else:
        text = "\n\n".join(f"=== PAGE {p.url}\n{p.text}" for p in readable)[:MAX_READ_CHARS]
        facts, _ = await _call(
            spend,
            READER_TIER,
            READER_PROMPT,
            Facts,
            f"Institution: {name} ({school.school_type} {school.education_level})\n\n{text}",
            school.id,
        )
        kept = [f for f in facts.facts if quote_on_page(f, readable)]
        report["facts"] = [f.model_dump() | {"quote_found": f in kept} for f in facts.facts]
        report["outcome"] = f"{len(kept)} of {len(facts.facts)} facts kept"
        if len(kept) >= 2:
            listed = "\n".join(f"- {f.fact_en}" for f in kept)
            paragraph, _ = await _call(
                spend,
                READER_TIER,
                PARAGRAPH_PROMPT,
                Paragraph,
                f"School: {name} ({report['name_en'] or ''}), {school.school_type} {school.education_level}\n\nFacts:\n{listed}",
                school.id,
            )
            report["paragraph"] = paragraph.model_dump()
    report["cost_usd"] = round(spend.spent_usd - before, 6)
    report["old_summary"] = await _old_summary(db, school.id)
    return report


def markdown(reports: list[dict[str, Any]], spent: float, credit: dict[str, Any]) -> str:
    lines = ["# School facts trial", "", f"Spent ${spent:.4f}. OpenRouter: `{json.dumps(credit)}`", ""]
    for r in reports:
        lines += [f"## {r['name']} ({r.get('name_en') or ''}), school {r['school_id']}", ""]
        lines += [f"{r['type']}, {r['website']}. {r.get('outcome')}. Pages read: {len(r.get('pages', []))}.", ""]
        if r.get("paragraph"):
            lines += ["**Paragraph (EN):** " + r["paragraph"]["en"], "", "**Paragraph (BG):** " + r["paragraph"]["bg"], ""]
        for f in r.get("facts", []):
            mark = "kept" if f["quote_found"] else "DROPPED, quote not on page"
            lines.append(f"- {f['topic']}: {f['fact_en']} ({mark}) _\"{f['quote'][:160]}\"_")
        old = r.get("old_summary") or {}
        lines += ["", "**Old summariser:** " + (old.get("en", {}).get("long") if old.get("en") else f"skipped ({old.get('skipped')})"), ""]
        if r.get("error"):
            lines += [f"Error: {r['error']}", ""]
    return "\n".join(lines)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--per-kind", type=int, default=5)
    parser.add_argument("--seed", type=int, default=20261013)
    parser.add_argument("--city", default="sofia")
    parser.add_argument("--max-usd", type=float, default=0.50)
    args = parser.parse_args()

    from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig  # type: ignore

    # The fees agent's finder, steered to the pages that describe the school.
    fees_agent.PICK_PROMPT = PICK_PROMPT
    fees_agent._FEE_LINK_RE = _FACTS_LINK_RE

    args.out.mkdir(parents=True, exist_ok=True)
    credit = await openrouter_credit()
    print("OpenRouter before:", json.dumps(credit), flush=True)
    spend = fees_agent.Spend(args.max_usd)
    browser = BrowserConfig(headless=True, verbose=False, ignore_https_errors=True, user_agent=CRAWLER_USER_AGENT)
    run_config = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        check_robots_txt=True,
        page_timeout=30000,
        wait_until="domcontentloaded",
        delay_before_return_html=0.5,
        exclude_external_links=False,
        verbose=False,
    )
    reports: list[dict[str, Any]] = []
    async with async_session_maker() as db:
        schools = await sample(db, args.per_kind, args.seed, args.city)
        async with AsyncWebCrawler(config=browser) as crawler, fee_http_client() as client:
            for school in schools:
                print(f"school {school.id} {school.school_type} {school.website_url}", flush=True)
                try:
                    reports.append(await run_school(crawler, run_config, client, db, school, spend))
                except fees_agent.SpendCapReached as exc:
                    print(f"stopped: {exc}", flush=True)
                    break
                except Exception as exc:
                    reports.append({"school_id": school.id, "name": (school.name_i18n or {}).get("bg"),
                                    "type": school.school_type, "website": school.website_url,
                                    "outcome": "error", "error": str(exc)[:200]})
                print(f"  {reports[-1].get('outcome')} (${spend.spent_usd:.4f} so far)", flush=True)
        await db.rollback()  # read only
    credit_after = await openrouter_credit()
    (args.out / "facts_trial.json").write_text(
        json.dumps({"spent_usd": spend.spent_usd, "credit_before": credit, "credit_after": credit_after,
                    "reader_model": get_model(READER_TIER), "schools": reports}, ensure_ascii=False, indent=2)
    )
    (args.out / "facts_trial.md").write_text(markdown(reports, spend.spent_usd, credit_after))
    print(f"done: {len(reports)} schools, ${spend.spent_usd:.4f}; OpenRouter after: {json.dumps(credit_after)}")


if __name__ == "__main__":
    asyncio.run(main())

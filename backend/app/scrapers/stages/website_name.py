"""Website-name stage: the name a school's own website writes in Latin letters.

Reads the home page (its title, site name, logo text, headings and opening text), asks one
cheap call for the Latin name the site uses for this institution, and keeps it only when
code finds it on the page and a distinctive word of it is in the site's address or matches
the register name (``app.utils.website_name``). Stores ``attributes["website_name"]``; the
English page shows it and search finds it. The default is a dry run that writes nothing.
Not part of ``all``.
"""

from __future__ import annotations

import datetime
import logging
import re
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Optional

from bs4 import BeautifulSoup
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import create_agent, extract_provider_cost_usd, get_model, get_model_costs
from app.config import get_settings
from app.models import School
from app.models.pipeline_run import PipelineRun
from app.scrapers.fee_pages import fee_http_client
from app.scrapers.shared_site_check import level_family
from app.scrapers.stages import common as stage_common
from app.scrapers.stages.fees_agent import Spend, SpendCapReached
from app.scrapers.stages.social_links import Fetch, _http_fetch
from app.services.pipeline_runs import finalize_pipeline_run, start_pipeline_run
from app.services.provider_costs import ProviderCostError, execute_billable_request, provider_cost_scope
from app.utils.i18n_resolver import derive_english_name
from app.utils.school_facts import SITE_ACCEPTED_STATUSES
from app.utils.website_name import (
    ATTRIBUTE_KEY,
    SCHEMA_VERSION,
    host,
    is_latin_name,
    matches_site_or_register,
    names_other_level,
)

logger = logging.getLogger(__name__)

STAGE = "website-name"
TIER = "cheap"
DEFAULT_MAX_USD = 0.30
# The opening text of the home page: the name is in the header, the title or the first lines.
MAX_TEXT_CHARS = 4000
OUTPUT_TOKENS = 200
# Less visible text than this (and no site name or logo text) is a page built by scripts.
MIN_TEXT_CHARS = 200
# State schools rarely write a Latin brand; their English name comes from the register.
DEFAULT_TYPES = ("private", "international")

PROMPT = """You get the home page of a school or kindergarten website (its title, site name, logo text,
headings and opening text) and the institution's name in the Bulgarian register.
Return the name the website uses for this institution written in Latin letters, exactly as the site
writes it (keep its capitals and spelling), such as "UWEKIND", "Funky Monkey Kindergarten" or
"Maple Bear Sofia".
- Only a name the page itself writes in Latin letters. Never transliterate or translate a Cyrillic
  name yourself: if the site writes the name only in Cyrillic, return null.
- The institution's own name, not the company that runs it, a network or group of several
  kindergartens or schools, a sister school, the web designer, or the site's template ("My Blog").
- If the site is shared by a school and a kindergarten, give the name it uses for this institution
  ("UWEKIND Kindergarten"); if it uses one brand for both, give the brand.
- Leave out page words around the name: "Home", "Начало", taglines, slogans.
- Return null when you are not sure."""


class SiteName(BaseModel):
    latin_name: Optional[str] = Field(description="The name in Latin letters as the site writes it, or null")


Caller = Callable[[str, int], Awaitable[SiteName]]


def page_summary(html: str) -> tuple[str, str, str]:
    """(what the reader sees, all the page's text for the on-page check, its visible text)."""
    soup = BeautifulSoup(html, "html.parser")
    parts = []
    if soup.title and soup.title.get_text(strip=True):
        parts.append(f"Title: {soup.title.get_text(' ', strip=True)}")
    for prop in ("og:site_name", "og:title"):
        tag = soup.find("meta", attrs={"property": prop})
        if tag and str(tag.get("content") or "").strip():
            parts.append(f"{prop}: {str(tag['content']).strip()}")
    logos = []
    for img in soup.find_all("img"):
        alt = str(img.get("alt") or "").strip()
        marks = " ".join([alt, str(img.get("src") or ""), " ".join(img.get("class") or [])]).casefold()
        if alt and "logo" in marks and alt not in logos:
            logos.append(alt)
    if logos:
        parts.append("Logo text: " + " | ".join(logos[:5]))
    headings = [h.get_text(" ", strip=True) for h in soup.find_all(["h1", "h2"])][:6]
    if headings := [h for h in headings if h]:
        parts.append("Headings: " + " | ".join(headings))
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    text = "\n".join(line.strip() for line in soup.get_text("\n").splitlines() if line.strip())
    parts.append("Opening text:\n" + text[:MAX_TEXT_CHARS])
    alts = " ".join(str(img.get("alt") or "") for img in soup.find_all("img"))
    return "\n".join(parts), "\n".join(parts) + "\n" + text + "\n" + alts, text


def _squash(text: str) -> str:
    return " ".join(re.sub(r"[’`]", "'", text).casefold().split())


def check_name(
    name: Optional[str],
    page_text: str,
    website_url: Optional[str],
    register_names: list[str],
    *,
    family: str = "school",
    shared_with_other_level: bool = False,
) -> Optional[str]:
    """Why ``name`` is not kept, or None when it is.

    ``shared_with_other_level``: a school of the other level (kindergarten or school) has
    the same website, so a name naming only that level is the sibling's."""
    if not name:
        return "none on the page"
    if not is_latin_name(name):
        return "not a Latin name"
    if is_web_address(name, website_url):
        return "the web address, not a name"
    if not _on_page(name, page_text):
        return "not on the page"
    if not matches_site_or_register(name, website_url, register_names):
        return "matches neither the site's address nor the register name"
    if shared_with_other_level and names_other_level(name, family):
        return "names the other level on a shared site"
    return None


def _on_page(name: str, page_text: str) -> bool:
    """The name is a whole phrase on the page, or written there as one word ("questjunior"
    for Quest Junior: the reader adds the spaces a logo or handle leaves out)."""
    text = _squash(page_text)
    phrases = {_squash(name), _squash(name).replace(" ", "")}
    return any(re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text) for phrase in phrases if phrase)


def is_web_address(name: str, website_url: Optional[str]) -> bool:
    """A domain ("izgrevou.com", "Eduteh.EU") or the address's handle with a country or city
    tag ("Wonderhouse-bg", "Lincolnsofia"): the reader took the address for the name. A
    brand that is its address ("Kidsville" for kidsville.space) is still a name."""
    if re.fullmatch(r"[\w-]+(?:\.[a-z]{2,6})+", name.strip(), flags=re.IGNORECASE):
        return True
    letters = re.sub(r"[^a-z0-9]", "", name.casefold())
    labels = host(website_url).split(".")[:-1]
    handle = re.sub(r"[^a-z0-9]", "", labels[-1]) if labels else ""
    return " " not in name.strip() and letters == handle and bool(re.search(r"(?:bg|sofia|eu)$", handle))


def _register_names(school: School) -> list[str]:
    bg = (school.name_i18n or {}).get("bg") or ""
    return [bg, (school.name_i18n or {}).get("en") or "", derive_english_name(bg) or ""]


async def read_school(
    school: School, fetch: Fetch, call: Caller, *, shared_with_other_level: bool = False
) -> dict[str, Any]:
    home = await fetch(school.website_url)
    if home is None:
        return {"read": False, "outcome": "not_read"}
    final, html = home
    if host(final) != host(school.website_url):
        # Another host's name (an operator's, a network's) may not be this school's, and a
        # moved site is URL validation's to accept: not read, the stored name stays.
        return {"read": False, "outcome": "not_read", "page": final, "note": "redirects to another host"}
    summary, page_text, visible = page_summary(html)
    if len(visible.strip()) < MIN_TEXT_CHARS and not any(
        line.startswith(("og:site_name", "Logo text")) for line in summary.splitlines()
    ):
        # A page built by scripts the plain fetch does not run: not read, the stored name stays.
        return {"read": False, "outcome": "not_read", "page": final, "note": "thin home page"}
    register = (school.name_i18n or {}).get("bg") or (school.name_i18n or {}).get("en") or ""
    answer = await call(
        f"Register name: {register} ({school.school_type} {school.education_level})\n\n{summary}", school.id
    )
    name = (answer.latin_name or "").strip() or None
    dropped = check_name(
        name,
        page_text,
        school.website_url,
        _register_names(school),
        family=level_family(school.education_level),
        shared_with_other_level=shared_with_other_level,
    )
    return {
        "read": True,
        "page": final,
        "answer": name,
        "name": None if dropped else name,
        "dropped": dropped if name else None,
        "outcome": "found" if not dropped else ("dropped" if name else "none"),
    }


def model_caller(spend: Spend) -> Caller:
    async def call(message: str, school_id: int) -> SiteName:
        in_price, out_price = get_model_costs(TIER)
        estimate = ((len(PROMPT) + len(message)) / 3 * in_price + OUTPUT_TOKENS * out_price) / 1_000_000
        agent = create_agent(
            TIER, PROMPT, SiteName, retries=1, model_settings={"temperature": 0, "max_tokens": OUTPUT_TOKENS}
        )
        with spend.hold(estimate):
            result = await execute_billable_request(
                lambda: agent.run(message), model=get_model(TIER), school_id=school_id, stage=STAGE, timeout_seconds=60
            )
        spend.spent_usd += extract_provider_cost_usd(result)
        return result.output

    return call


def name_block(name: Optional[str], website_url: Optional[str], page: Optional[str]) -> dict[str, Any]:
    return {
        "_schema_version": SCHEMA_VERSION,
        "read_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "website_url": website_url,
        "source_url": page,
        "model": get_model(TIER),
        "name": name,
    }


async def run_website_name(
    db: AsyncSession,
    *,
    country: str = "bg",
    city: Optional[str] = None,
    school_ids: Sequence[int] = (),
    school_types: Sequence[str] = DEFAULT_TYPES,
    limit: Optional[int] = None,
    max_usd: float = DEFAULT_MAX_USD,
    apply: bool = False,
    fetch: Optional[Fetch] = None,
    call: Optional[Caller] = None,
) -> dict[str, Any]:
    """Run over schools with a website URL validation accepted (the given ids, or the given
    school types); returns the report. A home page not read keeps the stored name; one read
    with no name kept clears it."""
    query = select(School.id).where(
        School.country_code == country,
        School.website_url.isnot(None),
        School.scrape_status.in_(SITE_ACCEPTED_STATUSES),
    )
    if school_ids:
        query = query.where(School.id.in_(list(school_ids)))
    elif school_types:
        query = query.where(School.school_type.in_(list(school_types)))
    if city:
        query = query.where(func.lower(School.city) == city.lower())
    ids = list((await db.execute(query.order_by(School.id).limit(limit))).scalars().all())
    # The level families on each website host, to tell a kindergarten's name from its
    # sister school's on a site they share (uwekind.com).
    families: dict[str, set[str]] = {}
    for website_url, education_level in await db.execute(
        select(School.website_url, School.education_level).where(
            School.country_code == country, School.website_url.isnot(None)
        )
    ):
        families.setdefault(host(website_url), set()).add(level_family(education_level))

    spend = Spend(max_usd)
    run = await start_pipeline_run(
        db, country=country, city=city, cli_stage=STAGE, config={"provider_cost_cap_usd": max_usd, "apply": apply}
    )
    run_id = run.id
    call = call or model_caller(spend)
    results: list[dict[str, Any]] = []
    error: Optional[str] = None

    async def run_all(fetch: Fetch) -> None:
        nonlocal error
        for school_id in ids:
            school = await db.get(School, school_id)
            result: dict[str, Any] = {
                "school_id": school_id,
                "name_bg": (school.name_i18n or {}).get("bg"),
                "website": school.website_url,
            }
            try:
                shared = len(families.get(host(school.website_url), ())) > 1
                result |= await read_school(school, fetch, call, shared_with_other_level=shared)
            except (SpendCapReached, ProviderCostError) as exc:
                error = str(exc)[:200]
                break
            except Exception as exc:  # one school's page must not stop the run; it keeps its name
                logger.exception("website-name failed for school %s", school_id)
                result |= {"read": False, "outcome": "error", "note": str(exc)[:200]}
            if result["read"] and apply:
                await db.refresh(school, attribute_names=["attributes"], with_for_update=True)
                school.attributes = {
                    **(school.attributes or {}),
                    ATTRIBUTE_KEY: name_block(result["name"], school.website_url, result.get("page")),
                }
                await db.commit()
                result["stored"] = True
            results.append(result)

    try:
        with provider_cost_scope(
            pipeline_run_id=run.id,
            stage=STAGE,
            cap_usd=max_usd,
            request_reserve_usd=float(get_settings().provider_request_reserve_usd),
            max_in_flight=1,
        ):
            if fetch is not None:
                await run_all(fetch)
            else:
                from crawl4ai.utils import RobotsParser  # type: ignore

                async with fee_http_client() as client:
                    await run_all(_http_fetch(client, RobotsParser()))
    except Exception as exc:
        logger.exception("website-name run %s stopped", run_id)
        if db.in_transaction():
            await db.rollback()
        error = f"run stopped: {str(exc)[:200]}"
    failed = sum(r["outcome"] == "error" for r in results)
    run = await db.get(PipelineRun, run_id, populate_existing=True)
    await finalize_pipeline_run(
        db,
        run,
        country=country,
        city=city,
        stage_summaries=[
            stage_common.stage_summary(processed=len(results), succeeded=len(results) - failed, failed=failed)
        ],
        error_summary=error,
    )
    outcomes: dict[str, int] = {}
    for result in results:
        outcomes[result["outcome"]] = outcomes.get(result["outcome"], 0) + 1
    return {
        "apply": apply,
        "pipeline_run_id": run_id,
        "model": get_model(TIER),
        "schools": len(results),
        "outcomes": outcomes,
        "spent_usd": round(max(spend.spent_usd, float(run.total_llm_cost_usd or 0)), 6),
        "stopped": error,
        "results": results,
    }

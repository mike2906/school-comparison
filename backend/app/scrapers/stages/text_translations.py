"""Translate-texts stage: the other language for the texts a page shows as written.

Collects every school's shown texts that are in the other language (``app.utils.text_translations``):
fee names and age groups the fees agent gave no English for, English fee names on English-only
sites, 8th-grade class names from the РУО lists, and programme, facility and activity items the
curated glossary (``list_translations.json``) does not cover. Texts another school already has a
translation for are reused; the rest go to one cheap model in batches, and a translation is kept
only when it is in the target language and keeps every number of the original. Stores each
school's translations in ``attributes["text_translations"]``. Runs before every publish
(``scripts/publish_snapshot.sh``), so new website or ministry text is translated before it goes
live; with nothing new it makes no model call. The default is a dry run that writes nothing.
"""

from __future__ import annotations

import datetime
import json
import logging
import re
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Optional

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.ai.client import create_agent, extract_provider_cost_usd, get_model, get_model_costs
from app.config import get_settings
from app.models import School
from app.models.pipeline_run import PipelineRun
from app.scrapers.stages import common as stage_common
from app.scrapers.stages.fees_agent import Spend, SpendCapReached
from app.services.pipeline_runs import finalize_pipeline_run, start_pipeline_run
from app.services.provider_costs import ProviderCostError, execute_billable_request, provider_cost_scope
from app.utils.list_translations import is_untranslated
from app.utils.school_attributes import build_localized_attributes
from app.utils.school_facts import publication_attributes
from app.utils.text_translations import (
    ATTRIBUTE_KEY,
    LOCALES,
    SCHEMA_VERSION,
    cutoff_texts,
    key,
    needs_translation,
    price_texts,
    stored,
)

logger = logging.getLogger(__name__)

STAGE = "translate-texts"
TIER = "translation"
DEFAULT_MAX_USD = 0.20
BATCH_SIZE = 60
OUTPUT_TOKENS = 6000
LIST_FIELDS = ("facilities", "special_programs", "activities_offered")
_DIGITS_RE = re.compile(r"\d+")
_CYRILLIC_RE = re.compile(r"[Ѐ-ӿ]")

LANGUAGE_NAMES = {"en": "English", "bg": "Bulgarian"}

PROMPT = """You translate short texts shown on a school comparison website for parents in Sofia.
They are written by Bulgarian schools and kindergartens on their websites, or come from the
Ministry of Education's admission lists: fee names, age groups, payment instalments, 8th-grade
class profiles, programmes, facilities and activities.

Translate each item into {language}, as a parent would read it on the {language} page.
- Say what the item says, plainly. Add nothing, explain nothing, and drop nothing.
- Keep every number, date, amount, percentage, code and Roman numeral exactly as written
  ("вноска II, до 15.11.26 г." -> "Instalment II, by 15.11.26").
- Keep names as names: a brand, programme or school name stays as written; in English a Bulgarian
  proper name is transliterated ("Слънчице" -> "Slanchitse").
- 8th-grade class profiles use these abbreviations: АЕ English, НЕ German, ФЕ French, ИЕ Spanish,
  ИтЕ or Ит.Е Italian, РЕ Russian, КитЕ Chinese, ЯЕ Japanese, КорЕ Korean, АрЕ Arabic,
  Исп.Е Spanish; БЕЛ Bulgarian language and literature, М Mathematics, Инф Informatics,
  ИТ Information technology, ФА Physics and astronomy, БЗО Biology and health education,
  ХООС Chemistry and environmental protection, ИЦ History and civilisations, ГИ Geography and
  economics, Фил Philosophy; "интензивно" intensive, "разширено" extended, "дуална" dual
  (work-based) training. ПГ, ПГУ or ПК is the preparatory group (preschool).
- Return null for an item you cannot translate with confidence.

Return one entry per item, with its id."""


class Translated(BaseModel):
    id: int
    text: Optional[str] = Field(description="The translation, or null")


class Translations(BaseModel):
    items: list[Translated]


Caller = Callable[[list[str], str], Awaitable[list[Optional[str]]]]


def checked(source: str, target: Optional[str], locale: str) -> Optional[str]:
    """``target`` when it is a usable translation of ``source`` into ``locale``, else None:
    in the target language (no Cyrillic in English, mostly Cyrillic in Bulgarian), not
    implausibly long, and with exactly the original's numbers."""
    target = key(target)
    if not target or target == key(source) or len(target) > 3 * len(source) + 30:
        return None
    if locale == "en" and _CYRILLIC_RE.search(target):
        return None
    if locale == "bg" and needs_translation(target, "bg"):
        return None
    if sorted(_DIGITS_RE.findall(source)) != sorted(_DIGITS_RE.findall(target)):
        return None
    return target


def shown_texts(school: School) -> dict[str, set[str]]:
    """The school's shown texts in the other language, by the locale they need."""
    out: dict[str, set[str]] = {locale: set() for locale in LOCALES}
    for row in school.pricing or []:
        for locale, texts in price_texts(row).items():
            out[locale].update(texts)
    out["en"].update(cutoff_texts(school.admission_info))
    public = publication_attributes(school.attributes, school.scrape_status, school.website_url)
    for locale in LOCALES:
        localized = build_localized_attributes(public, locale, school.school_type, translate=False)
        for field in LIST_FIELDS:
            out[locale].update(
                key(item)
                for item in localized.get(field) or []
                if needs_translation(item, locale) and is_untranslated(item, locale)
            )
    return out


def model_caller(spend: Spend) -> Caller:
    async def call(texts: list[str], locale: str) -> list[Optional[str]]:
        prompt = PROMPT.format(language=LANGUAGE_NAMES[locale])
        message = json.dumps([{"id": i, "text": text} for i, text in enumerate(texts)], ensure_ascii=False)
        in_price, out_price = get_model_costs(TIER)
        estimate = ((len(prompt) + len(message)) / 3 * in_price + OUTPUT_TOKENS * out_price) / 1_000_000
        agent = create_agent(TIER, prompt, Translations, retries=1, model_settings={"max_tokens": OUTPUT_TOKENS})
        with spend.hold(estimate):
            result = await execute_billable_request(
                lambda: agent.run(message), model=get_model(TIER), school_id=None, stage=STAGE, timeout_seconds=120
            )
        spend.spent_usd += extract_provider_cost_usd(result)
        answers = {item.id: item.text for item in result.output.items}
        return [answers.get(i) for i in range(len(texts))]

    return call


async def translate(texts: Sequence[str], locale: str, call: Caller, out: dict[str, Optional[str]]) -> None:
    """Put ``texts`` translated into ``locale`` in ``out``, batch by batch (None for each one
    not kept), so a run stopped by its cap keeps the batches done before."""
    texts = list(texts)
    for start in range(0, len(texts), BATCH_SIZE):
        batch = texts[start:start + BATCH_SIZE]
        answers = await call(batch, locale)
        out.update({text: checked(text, answer, locale) for text, answer in zip(batch, answers)})


def translation_block(translations: dict[str, dict[str, Optional[str]]]) -> dict[str, Any]:
    return {
        "_schema_version": SCHEMA_VERSION,
        "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "model": get_model(TIER),
        **{locale: dict(sorted(translations[locale].items())) for locale in LOCALES},
    }


async def run_text_translations(
    db: AsyncSession,
    *,
    country: str = "bg",
    city: Optional[str] = None,
    school_ids: Sequence[int] = (),
    max_usd: float = DEFAULT_MAX_USD,
    apply: bool = False,
    call: Optional[Caller] = None,
) -> dict[str, Any]:
    """Translate what the schools' pages show in the other language; returns the report.

    Each school keeps exactly the translations its page needs now: new texts are added and
    texts it no longer shows are dropped. Texts the model was asked for and gave nothing
    usable for are stored as null, so they are not asked again."""
    query = select(School).where(School.country_code == country).options(selectinload(School.pricing))
    if school_ids:
        query = query.where(School.id.in_(list(school_ids)))
    if city:
        query = query.where(func.lower(School.city) == city.lower())
    schools = list((await db.execute(query.order_by(School.id))).scalars().all())

    needed = {school.id: shown_texts(school) for school in schools}
    have = {school.id: stored(school.attributes) for school in schools}
    known: dict[str, dict[str, Optional[str]]] = {locale: {} for locale in LOCALES}
    for translations in have.values():
        for locale in LOCALES:
            known[locale].update(translations[locale])
    asked = {
        locale: sorted({text for texts in needed.values() for text in texts[locale]} - set(known[locale]))
        for locale in LOCALES
    }

    spend = Spend(max_usd)
    run = await start_pipeline_run(
        db, country=country, city=city, cli_stage=STAGE, config={"provider_cost_cap_usd": max_usd, "apply": apply}
    )
    run_id = run.id
    call = call or model_caller(spend)
    error: Optional[str] = None
    new: dict[str, dict[str, Optional[str]]] = {locale: {} for locale in LOCALES}
    try:
        with provider_cost_scope(
            pipeline_run_id=run.id,
            stage=STAGE,
            cap_usd=max_usd,
            request_reserve_usd=float(get_settings().provider_request_reserve_usd),
            max_in_flight=1,
        ):
            for locale in LOCALES:
                await translate(asked[locale], locale, call, new[locale])
    except (SpendCapReached, ProviderCostError) as exc:
        error = str(exc)[:200]
    except Exception as exc:  # a failed call keeps what was translated before it
        logger.exception("translate-texts run %s stopped", run_id)
        error = f"run stopped: {str(exc)[:200]}"
    for locale in LOCALES:
        known[locale].update(new[locale])

    changed = 0
    for school in schools:
        wanted = {
            locale: {text: known[locale][text] for text in needed[school.id][locale] if text in known[locale]}
            for locale in LOCALES
        }
        if wanted == have[school.id]:
            continue
        changed += 1
        if apply:
            await db.refresh(school, attribute_names=["attributes"], with_for_update=True)
            attributes = dict(school.attributes or {})
            if any(wanted.values()):
                attributes[ATTRIBUTE_KEY] = translation_block(wanted)
            else:
                attributes.pop(ATTRIBUTE_KEY, None)
            school.attributes = attributes
            await db.commit()

    run = await db.get(PipelineRun, run_id, populate_existing=True)
    await finalize_pipeline_run(
        db,
        run,
        country=country,
        city=city,
        stage_summaries=[stage_common.stage_summary(processed=len(schools), succeeded=len(schools), failed=0)],
        error_summary=error,
    )
    return {
        "apply": apply,
        "pipeline_run_id": run_id,
        "model": get_model(TIER),
        "schools": len(schools),
        "schools_changed": changed,
        "asked": {locale: len(asked[locale]) for locale in LOCALES},
        "translated": {locale: sum(value is not None for value in new[locale].values()) for locale in LOCALES},
        "spent_usd": round(max(spend.spent_usd, float(run.total_llm_cost_usd or 0)), 6),
        "stopped": error,
        "new": new,
    }

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
from app.models.pricing import Pricing
from app.models.source_page import SourcePage
from app.schemas.school import SchoolPricingMixin
from app.scrapers.stages import common as stage_common
from app.scrapers.stages.fees_agent import Spend, SpendCapReached
from app.services.pipeline_runs import finalize_pipeline_run, start_pipeline_run
from app.services.provider_costs import ProviderCostError, execute_billable_request, provider_cost_scope
from app.utils.list_translations import translate_items
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
# Roman numerals up to the grades and instalments labels number (I to XII), also typed with a
# Cyrillic І or Х. Not C or M: a lone "М" is mathematics in a class profile ("М и Инф").
_CYRILLIC_ROMAN = str.maketrans({"І": "I", "Х": "X"})
# Not a letter of an abbreviation either ("V.I.P.").
_ROMAN_RE = re.compile(r"(?<![\wЀ-ӿ.])[IVX]+(?![\wЀ-ӿ]|\.[A-Za-zЀ-ӿ])")

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


class BatchMismatch(RuntimeError):
    """The model's answer does not give each item of the batch exactly once."""


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
    if sorted(roman_numerals(source)) != sorted(roman_numerals(target)):
        return None
    return target


def roman_numerals(text: str) -> list[str]:
    """The Roman numerals written as words ("вноска II", "ХI клас" with a Cyrillic Х), in Latin."""
    latin = text.translate(_CYRILLIC_ROMAN)
    return _ROMAN_RE.findall(latin)


def shown_texts(school: School) -> dict[str, set[str]]:
    """The school's shown texts in the other language, by the locale they need."""
    out: dict[str, set[str]] = {locale: set() for locale in LOCALES}
    for row in published_price_rows(school):
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
                if needs_translation(item, locale) and translate_items([item], locale) == [item]
            )
    return out


def published_price_rows(school: School) -> list[Any]:
    """The school's fee rows the API publishes, through the response's own gates."""
    projection = SchoolPricingMixin.model_validate(
        {"attributes": school.attributes, "scrape_status": school.scrape_status, "pricing": list(school.pricing or [])}
    )
    shown = {row.id for row in projection.pricing}
    return [row for row in school.pricing or [] if row.id in shown]


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
        ids = [item.id for item in result.output.items]
        if sorted(ids) != list(range(len(texts))):
            # A skipped or repeated id may have shifted the answers onto the wrong items:
            # keep none of the batch (it is asked again next run).
            raise BatchMismatch(f"{len(texts)} items asked, ids {len(ids)} back")
        answers = {item.id: item.text for item in result.output.items}
        return [answers[i] for i in range(len(texts))]

    return call


async def translate(texts: Sequence[str], locale: str, call: Caller, out: dict[str, Optional[str]]) -> None:
    """Put ``texts`` translated into ``locale`` in ``out``, batch by batch (None for each one
    not kept), so a run stopped by its cap keeps the batches done before."""
    texts = list(texts)
    for start in range(0, len(texts), BATCH_SIZE):
        batch = texts[start:start + BATCH_SIZE]
        out.update(await _answers(batch, locale, call))


async def _answers(batch: list[str], locale: str, call: Caller) -> dict[str, Optional[str]]:
    """A batch's checked answers. A batch that came back mismatched is asked again text by text,
    and a single text that still does is stored as null, so a publish does not ask it hourly."""
    try:
        answers = await call(batch, locale)
    except BatchMismatch:
        if len(batch) == 1:
            logger.warning("translate-texts: no %s answer that matches %r", locale, batch[0])
            return {batch[0]: None}
        logger.warning("translate-texts: a %s batch came back mismatched; asking text by text", locale)
        out: dict[str, Optional[str]] = {}
        for text in batch:
            out.update(await _answers([text], locale, call))
        return out
    return {text: checked(text, answer, locale) for text, answer in zip(batch, answers)}


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
    plan: bool = False,
    call: Optional[Caller] = None,
) -> dict[str, Any]:
    """Translate what the schools' pages show in the other language; returns the report.

    Each school keeps exactly the translations its page needs now: new texts are added and
    texts it no longer shows are dropped. Texts the model was asked for and gave nothing
    usable for are stored as null, so they are not asked again."""
    # The fee gate reads the linked page's validity, as the API's query loads it.
    query = select(School).where(School.country_code == country).options(
        selectinload(School.pricing).selectinload(Pricing.source_page).load_only(SourcePage.id, SourcePage.is_valid)
    )
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
            for text, value in translations[locale].items():
                if value is not None and checked(text, value, locale) is None:
                    continue  # stored before a check it now fails: asked again
                # A usable translation another school has wins over a stored failure.
                if known[locale].get(text) is None:
                    known[locale][text] = value
    asked = {
        locale: sorted({text for texts in needed.values() for text in texts[locale]} - set(known[locale]))
        for locale in LOCALES
    }

    def wanted(school_id: int) -> dict[str, dict[str, Optional[str]]]:
        return {
            locale: {text: known[locale][text] for text in needed[school_id][locale] if text in known[locale]}
            for locale in LOCALES
        }

    if plan:
        # What an --apply run would change, without asking the model or writing anything.
        pending = sum(
            1
            for school in schools
            if wanted(school.id) != have[school.id]
            or any(set(needed[school.id][locale]) & set(asked[locale]) for locale in LOCALES)
        )
        return {"plan": True, "schools": len(schools), "schools_to_change": pending,
                "asked": {locale: len(asked[locale]) for locale in LOCALES}}

    spend = Spend(max_usd)
    call = call or model_caller(spend)
    errors: list[str] = []
    new: dict[str, dict[str, Optional[str]]] = {locale: {} for locale in LOCALES}
    run_id = None
    if any(asked.values()):
        # Only a run that asks the model is a pipeline run (the cost ledger counts its calls):
        # the hourly cut-off publish runs this stage too, and usually has nothing to ask.
        run = await start_pipeline_run(
            db, country=country, city=city, cli_stage=STAGE, config={"provider_cost_cap_usd": max_usd, "apply": apply}
        )
        run_id = run.id
        with provider_cost_scope(
            pipeline_run_id=run.id,
            stage=STAGE,
            cap_usd=max_usd,
            request_reserve_usd=float(get_settings().provider_request_reserve_usd),
            max_in_flight=1,
        ):
            for locale in LOCALES:
                try:
                    await translate(asked[locale], locale, call, new[locale])
                except (SpendCapReached, ProviderCostError) as exc:
                    errors.append(f"{locale}: {str(exc)[:200]}")
                    break
                except Exception as exc:  # the batches done before a failed call are kept
                    logger.exception("translate-texts run %s stopped in %s", run_id, locale)
                    errors.append(f"{locale}: run stopped: {str(exc)[:200]}")
    for locale in LOCALES:
        known[locale].update(new[locale])

    changed = 0
    try:
        for school in schools:
            translations = wanted(school.id)
            if translations == have[school.id]:
                continue
            changed += 1
            if apply:
                await db.refresh(school, attribute_names=["attributes"], with_for_update=True)
                attributes = dict(school.attributes or {})
                if any(translations.values()):
                    attributes[ATTRIBUTE_KEY] = translation_block(translations)
                else:
                    attributes.pop(ATTRIBUTE_KEY, None)
                school.attributes = attributes
                await db.commit()
    except Exception as exc:
        logger.exception("translate-texts could not store translations")
        if db.in_transaction():
            await db.rollback()
        errors.append(f"storing stopped: {str(exc)[:200]}")

    error = "; ".join(errors) or None
    spent = spend.spent_usd
    if run_id is not None:
        run = await db.get(PipelineRun, run_id, populate_existing=True)
        await finalize_pipeline_run(
            db,
            run,
            country=country,
            city=city,
            stage_summaries=[
                stage_common.stage_summary(processed=len(schools), succeeded=len(schools) - bool(error), failed=int(bool(error)))
            ],
            error_summary=error,
        )
        spent = max(spent, float(run.total_llm_cost_usd or 0))
    return {
        "apply": apply,
        "pipeline_run_id": run_id,
        "model": get_model(TIER),
        "schools": len(schools),
        "schools_changed": changed,
        "asked": {locale: len(asked[locale]) for locale in LOCALES},
        "translated": {locale: sum(value is not None for value in new[locale].values()) for locale in LOCALES},
        "spent_usd": round(spent, 6),
        "stopped": error,
        "new": new,
    }

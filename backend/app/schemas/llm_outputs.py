import json

from pydantic import BaseModel, Field, Json, model_validator

from app.schemas.school import SummaryI18n, SummaryText


class SchoolSummary(BaseModel):
    """AI-generated school summary in multiple languages."""

    summary_i18n: SummaryI18n = Field(
        description=(
            "Summary in each supported language, e.g. "
            "{'bg': {'short': '...', 'long': '...'}, 'en': {'short': '...', 'long': '...'}}"
        )
    )


class SummaryI18nStrict(BaseModel):
    bg: SummaryText
    en: SummaryText


class SchoolSummaryStrict(BaseModel):
    """Strict internal schema for bilingual LLM-generated summaries."""

    summary_i18n: SummaryI18nStrict | Json[SummaryI18nStrict] = Field(
        description=(
            "Bulgarian (bg) and English (en) summaries, each with a one-sentence `short` "
            "and a 2-4 sentence `long`, e.g. "
            "{'bg': {'short': '...', 'long': '...'}, 'en': {'short': '...', 'long': '...'}}"
        )
    )

    @model_validator(mode="before")
    @classmethod
    def _coerce_stringified_summary_i18n(cls, value):
        if isinstance(value, dict):
            raw_summary = value.get("summary_i18n")
            if isinstance(raw_summary, str):
                try:
                    return {
                        **value,
                        "summary_i18n": json.loads(raw_summary),
                    }
                except json.JSONDecodeError:
                    raise ValueError("summary_i18n must be valid JSON when provided as a string") from None
        return value


class NavigatorOutput(BaseModel):
    """Output from the Navigator agent - identifies relevant pages."""

    pricing_pages: list[str] = Field(
        default_factory=list, description="URLs likely containing pricing info"
    )
    about_pages: list[str] = Field(
        default_factory=list, description="URLs with about/general info"
    )
    contact_pages: list[str] = Field(
        default_factory=list, description="URLs with contact information"
    )
    admission_pages: list[str] = Field(
        default_factory=list, description="URLs with admission information"
    )

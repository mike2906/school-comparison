from typing import Optional

from pydantic import BaseModel, Field

from app.schemas.school import SummaryI18n


class ExtractedPrice(BaseModel):
    """Structured price information extracted by LLM."""

    category: str = Field(description="Price category: tuition, food, transport, or activities")
    amount: float = Field(description="Numeric amount")
    currency: str = Field(default="BGN", description="Currency code")
    period: str = Field(description="Period: monthly, yearly, or one_time")
    age_group: Optional[str] = Field(default=None, description="Age group if specified")
    confidence: float = Field(
        default=1.0, description="Confidence score 0-1, lower if price not found in raw HTML"
    )


class ExtractedPrices(BaseModel):
    """All prices extracted from a school website."""

    prices: list[ExtractedPrice] = Field(default_factory=list)
    extraction_notes: Optional[str] = Field(
        default=None, description="Notes about the extraction process"
    )


class SchoolSummary(BaseModel):
    """AI-generated school summary in multiple languages."""

    summary_i18n: SummaryI18n = Field(
        description=(
            "Summary in each supported language, e.g. "
            "{'bg': {'short': '...', 'long': '...'}, 'en': {'short': '...', 'long': '...'}}"
        )
    )


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

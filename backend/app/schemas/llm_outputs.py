from pydantic import BaseModel, Field

from app.schemas.school import SummaryI18n


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

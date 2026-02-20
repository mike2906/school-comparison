from typing import Optional
from pydantic import BaseModel, Field

class ExtractedPrice(BaseModel):
    """Structured price information extracted by LLM."""

    category: str = Field(description="Price category label from source text")
    amount: Optional[float] = Field(default=None, description="Numeric amount")
    currency: str = Field(default="BGN", description="Currency code (e.g. BGN, EUR, USD)")
    period: str = Field(description="Period label from source text")
    amount_min: Optional[float] = Field(default=None, description="Minimum amount if a range is specified")
    amount_max: Optional[float] = Field(default=None, description="Maximum amount if a range is specified")
    plan_name: Optional[str] = Field(default=None, description="Name of the plan (e.g. 'Standard', 'Premium')")
    academic_year: Optional[str] = Field(default=None, description="Academic year (e.g. '2023/2024')")
    age_group: Optional[str] = Field(default=None, description="Age group if specified (e.g. '5-7 years', 'Grade 1')")
    notes: Optional[str] = Field(default=None, description="Any additional notes or context about the price")
    confidence: float = Field(
        default=1.0, description="Confidence score 0-1, lower if price not clearly stated"
    )


class PriceExtractionOutput(BaseModel):
    """Output from price extraction."""

    prices: list[ExtractedPrice] = Field(default_factory=list)
    has_pricing_info: bool = Field(
        default=False,
        description="Whether pricing information was found on the page",
    )
    confidence_notes: Optional[str] = Field(default=None, description="Notes on confidence/ambiguity")


class ExtractedLanguageFocus(BaseModel):
    """Language focus details."""
    language: str = Field(description="Language name")
    level: Optional[str] = Field(default=None, description="Level of instruction (e.g. 'intensive', 'mother tongue')")


class GeneralInfoExtractionOutput(BaseModel):
    """Output from general info extraction."""

    languages: list[ExtractedLanguageFocus] = Field(default_factory=list, description="Languages taught/focused on")
    facilities: list[str] = Field(default_factory=list, description="List of facilities (e.g. 'swimming pool', 'lab')")
    programs: list[str] = Field(default_factory=list, description="Educational programs (e.g. 'IB', 'A-Levels', 'Montessori')")
    extracurricular: list[str] = Field(default_factory=list, description="Extracurricular activities")
    class_size: Optional[str] = Field(default=None, description="Average class size info")
    founded_year: Optional[str] = Field(default=None, description="Year the school was founded")
    accreditations: list[str] = Field(default_factory=list, description="Accreditations and affiliations")
    has_useful_info: bool = Field(default=False, description="Whether useful general info was found")


class LanguagesExtractionOutput(BaseModel):
    """Section output for language extraction."""

    languages: list[ExtractedLanguageFocus] = Field(default_factory=list)


class FacilitiesExtractionOutput(BaseModel):
    """Section output for facilities extraction."""

    facilities: list[str] = Field(default_factory=list)


class ProgramsExtractionOutput(BaseModel):
    """Section output for programs/extracurricular extraction."""

    programs: list[str] = Field(default_factory=list)
    extracurricular: list[str] = Field(default_factory=list)


class MetadataExtractionOutput(BaseModel):
    """Section output for metadata extraction."""

    class_size: Optional[str] = Field(default=None)
    founded_year: Optional[str] = Field(default=None)
    accreditations: list[str] = Field(default_factory=list)

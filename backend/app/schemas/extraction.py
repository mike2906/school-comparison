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
    discounts: list[str] = Field(default_factory=list, description="Discount terms, e.g. sibling discount")
    installments: list[str] = Field(default_factory=list, description="Installment payment terms")
    includes: list[str] = Field(default_factory=list, description="Items explicitly included in this fee")
    excludes: list[str] = Field(default_factory=list, description="Items explicitly excluded from this fee")
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


class AdmissionExtractionOutput(BaseModel):
    """Website-derived admissions details."""

    deadlines: list[str] = Field(default_factory=list, description="Admission timelines/deadlines")
    required_documents: list[str] = Field(default_factory=list, description="Required admission documents")
    application_steps: list[str] = Field(default_factory=list, description="How to apply")
    entrance_requirements: list[str] = Field(default_factory=list, description="Interviews/tests/entry requirements")
    available_spots: list[str] = Field(default_factory=list, description="Available places/capacity snippets")
    has_useful_info: bool = Field(default=False, description="Whether useful admission info was found")


class OperationsExtractionOutput(BaseModel):
    """Website-derived day-to-day school operations details."""

    working_hours: Optional[str] = Field(default=None, description="Operating hours")
    day_options: list[str] = Field(default_factory=list, description="Full-day/half-day/day organization options")
    daily_schedule: list[str] = Field(default_factory=list, description="Daily schedule/routine snippets")
    meals: list[str] = Field(default_factory=list, description="Meals/menu/food details")
    transport: list[str] = Field(default_factory=list, description="School transport/bus details")
    uniforms: list[str] = Field(default_factory=list, description="Uniform policy/details")
    has_useful_info: bool = Field(default=False, description="Whether useful operations info was found")


class ServicesExtractionOutput(BaseModel):
    """Website-derived support/safety service details."""

    support_services: list[str] = Field(default_factory=list, description="Psychologist/speech therapist/nurse/etc.")
    safety_features: list[str] = Field(default_factory=list, description="Security/CCTV/access-control details")
    has_useful_info: bool = Field(default=False, description="Whether useful services info was found")


class PricingTermsExtractionOutput(BaseModel):
    """Website-derived pricing policy/terms details (non-row based)."""

    discounts: list[str] = Field(default_factory=list, description="Discount terms")
    installments: list[str] = Field(default_factory=list, description="Installment terms")
    included_items: list[str] = Field(default_factory=list, description="Items included in tuition/fees")
    excluded_items: list[str] = Field(default_factory=list, description="Items excluded from tuition/fees")
    deposits: list[str] = Field(default_factory=list, description="Deposit-related terms")
    application_fees: list[str] = Field(default_factory=list, description="Application/candidate fee terms")
    registration_fees: list[str] = Field(default_factory=list, description="Registration/enrollment fee terms")
    has_useful_info: bool = Field(default=False, description="Whether useful pricing terms were found")


class GeneralInfoExtractionOutput(BaseModel):
    """Output from general info extraction."""

    display_name_i18n: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Official public-facing school name variants shown on the website, keyed by language "
            "(e.g. {'bg': 'Фюжън Скул', 'en': 'Fusion School'}). "
            "Only include names explicitly shown on the website."
        ),
    )
    languages: list[ExtractedLanguageFocus] = Field(default_factory=list, description="Languages taught/focused on")
    facilities: list[str] = Field(default_factory=list, description="List of facilities (e.g. 'swimming pool', 'lab')")
    programs: list[str] = Field(default_factory=list, description="Educational programs (e.g. 'IB', 'A-Levels', 'Montessori')")
    extracurricular: list[str] = Field(default_factory=list, description="Extracurricular activities")
    class_size: Optional[str] = Field(default=None, description="Average class size info")
    founded_year: Optional[str] = Field(default=None, description="Year the school was founded")
    accreditations: list[str] = Field(default_factory=list, description="Accreditations and affiliations")
    admission: AdmissionExtractionOutput = Field(default_factory=AdmissionExtractionOutput)
    operations: OperationsExtractionOutput = Field(default_factory=OperationsExtractionOutput)
    services: ServicesExtractionOutput = Field(default_factory=ServicesExtractionOutput)
    pricing_terms: PricingTermsExtractionOutput = Field(default_factory=PricingTermsExtractionOutput)
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

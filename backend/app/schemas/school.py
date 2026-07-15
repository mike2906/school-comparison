from datetime import datetime
from typing import Any, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, JsonValue, computed_field

from app.schemas.pricing import PricingResponse
from app.schemas.field_source import FieldSourceResponse
from app.utils.display_gating import (
    blocked_pricing_row_ids,
    pricing_row_is_publishable,
    summary_is_publishable,
)
from app.utils.i18n_resolver import resolve_address_i18n, resolve_name_i18n
from app.utils.location_tags import semantic_location_tags
from app.utils.school_attributes import build_display_attributes
from app.utils.website_data import attributes_for_publication, website_data_is_publishable


class SummaryText(BaseModel):
    short: str
    long: str


SummaryI18n = dict[str, SummaryText]


class LanguageFocusItem(BaseModel):
    language: Optional[str] = None
    level: Optional[str] = None


class SchoolLocalizedAttributes(BaseModel):
    """Free-text display attributes, resolved for one locale."""

    language_focus: list[LanguageFocusItem] = []
    languages_of_instruction: list[str] = []
    facilities: list[str] = []
    special_programs: list[str] = []
    activities_offered: list[str] = []
    entry_requirements: list[str] = []
    application_deadlines: list[str] = []
    available_spots: list[str] = []
    daily_schedule: list[str] = []

    model_config = ConfigDict(extra="forbid")


class SchoolFilterTags(BaseModel):
    """Canonical advanced-filter tags mapped from free text (P1.9).

    Locale-independent option keys (`cafeteria`, `sports_program` …) matching the
    frontend `DEFAULT_ADVANCED_OPTIONS`. Served alongside — not instead of — the
    free-text lists in `SchoolLocalizedAttributes`, which are kept for display.
    """

    facilities: list[str] = []
    special_programs: list[str] = []
    teaching_approach: list[str] = []

    model_config = ConfigDict(extra="forbid")


class SchoolDisplayAttributes(BaseModel):
    """Locale-independent display attributes.

    `extra="forbid"` is the gate: `schools.attributes` is an internal scratchpad
    (`moe_*` codes, `extracted`, `data_validation`, `source_refs`), and nothing may
    reach the browser unless it is declared here. See `app.utils.school_attributes`.
    """

    class_size: Optional[Union[int, float]] = None
    has_canteen: Optional[bool] = None
    uniform_required: Optional[bool] = None
    special_focus: Optional[str] = None
    teaching_approach: list[str] = []
    teacher_student_ratio: Optional[str] = None
    school_hours: Optional[str] = None
    established_year: Optional[int] = None
    filter_tags: SchoolFilterTags = Field(default_factory=SchoolFilterTags)

    model_config = ConfigDict(extra="forbid")


class SchoolAdmissionInfo(BaseModel):
    """Allowlisted official/curated admission data.

    The internal ``website_extracted`` mirror is intentionally absent: its useful
    fields are projected through ``attributes_i18n`` and validation gates instead.
    """

    system: Optional[JsonValue] = None
    status: Optional[JsonValue] = None
    requirements: Optional[JsonValue] = None
    deadline: Optional[JsonValue] = None
    spots_available: Optional[JsonValue] = None
    platform_url: Optional[str] = None
    rounds: list[dict[str, JsonValue]] = Field(default_factory=list)
    historical_rounds: list[dict[str, JsonValue]] = Field(default_factory=list)
    historical_thresholds: list[dict[str, JsonValue]] = Field(default_factory=list)
    min_score: Optional[JsonValue] = None
    historical_min_scores: list[dict[str, JsonValue]] = Field(default_factory=list)

    model_config = ConfigDict(extra="ignore")


class SchoolAttributesMixin(BaseModel):
    """Projects the raw `attributes` JSONB into the public display payload.

    The raw column is validated into `raw_attributes` and excluded from output;
    `attributes` and `attributes_i18n` are computed from it.
    """

    raw_attributes: Optional[dict[str, Any]] = Field(
        default=None,
        validation_alias="attributes",
        exclude=True,
    )
    raw_admission_info: Optional[dict[str, Any]] = Field(
        default=None,
        validation_alias="admission_info",
        exclude=True,
    )
    raw_scrape_status: Optional[str] = Field(
        default=None,
        validation_alias="scrape_status",
        exclude=True,
    )

    @property
    def website_data_publishable(self) -> bool:
        return website_data_is_publishable(self.raw_attributes, self.raw_scrape_status)

    @property
    def public_attributes_input(self) -> dict[str, Any]:
        return attributes_for_publication(self.raw_attributes, self.raw_scrape_status)

    @computed_field(return_type=SchoolDisplayAttributes)
    @property
    def attributes(self) -> SchoolDisplayAttributes:
        base, _ = build_display_attributes(self.public_attributes_input)
        return SchoolDisplayAttributes.model_validate(base)

    @computed_field(return_type=dict[str, SchoolLocalizedAttributes])
    @property
    def attributes_i18n(self) -> dict[str, SchoolLocalizedAttributes]:
        _, localized = build_display_attributes(self.public_attributes_input)
        return {
            locale: SchoolLocalizedAttributes.model_validate(values)
            for locale, values in localized.items()
        }

    @computed_field(return_type=Optional[dict[str, JsonValue]])
    @property
    def admission_info(self) -> Optional[dict[str, JsonValue]]:
        if not isinstance(self.raw_admission_info, dict):
            return None
        public = SchoolAdmissionInfo.model_validate(self.raw_admission_info)
        payload = public.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
        return payload or None


class SchoolPricingMixin(SchoolAttributesMixin):
    """Projects the ORM pricing rows into the public payload, gated by P1.7.

    A row is withheld when it has no ``source_url``, a confidence below the shared
    floor, or an error-level validation issue on its ``pricing[{id}]`` path — so the
    API never publishes a price a parent can't trace, that we're unsure of, or that
    Stage 6 already flagged as wrong. Extends ``SchoolAttributesMixin`` to reach the
    validation report via ``raw_attributes``.
    """

    raw_pricing: list[Any] = Field(
        default_factory=list,
        validation_alias="pricing",
        exclude=True,
    )

    @computed_field(return_type=list[PricingResponse])
    @property
    def pricing(self) -> list[PricingResponse]:
        blocked_ids = blocked_pricing_row_ids(self.public_attributes_input)
        published: list[PricingResponse] = []
        for raw_row in self.raw_pricing:
            if isinstance(raw_row, dict):
                source = raw_row.get("source")
                source_url = raw_row.get("source_url")
                pricing_context = raw_row.get("pricing_context")
            else:
                source = getattr(raw_row, "source", None)
                source_url = getattr(raw_row, "source_url", None)
                pricing_context = getattr(raw_row, "pricing_context", None)

            # Gate the stored values before Pydantic can coerce malformed input
            # (for example, a string confidence of "0.9") into a valid public type.
            if not pricing_row_is_publishable(source, source_url, pricing_context):
                continue

            row = PricingResponse.model_validate(raw_row)
            if row.id in blocked_ids:
                continue
            published.append(row)
        return published


class SchoolLocationAgeGroupShift(BaseModel):
    age_group: str
    shift: Optional[str] = None
    has_organised_groups: Optional[bool] = None

    model_config = {"from_attributes": True}


class SchoolLocationBase(BaseModel):
    age_groups: list[str]
    age_group_shifts: Optional[list[SchoolLocationAgeGroupShift]] = None
    address_i18n: dict
    lat: Optional[float] = None
    lng: Optional[float] = None
    phone: Optional[str] = None
    raw_location_tags: Optional[list[str]] = Field(
        default=None,
        validation_alias="location_tags",
        exclude=True,
    )
    is_primary: bool = True

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    @computed_field(return_type=list[str])
    @property
    def location_tags(self) -> list[str]:
        """Public focus tags only; provenance/coords metadata is withheld (P1.8)."""
        return semantic_location_tags(self.raw_location_tags)

    @computed_field(return_type=dict[str, str])
    @property
    def resolved_address_i18n(self) -> dict[str, str]:
        return resolve_address_i18n(self.address_i18n)


class SchoolLocationResponse(SchoolLocationBase):
    id: int
    school_id: int


class ExamResultResponse(BaseModel):
    id: int
    school_id: int
    year: int
    exam_type: str
    subject: str
    metric: str
    value: float
    source_url: Optional[str] = None

    model_config = {"from_attributes": True}


class SchoolBase(SchoolAttributesMixin):
    country_code: str = "bg"
    name_i18n: dict
    school_type: str
    education_level: str
    source_url: Optional[str] = None
    website_url: Optional[str] = None
    raw_summary_i18n: Optional[SummaryI18n] = Field(
        default=None,
        validation_alias="summary_i18n",
        exclude=True,
    )
    num_pupils: Optional[int] = None
    model_config = ConfigDict(populate_by_name=True)

    @computed_field(return_type=Optional[SummaryI18n])
    @property
    def summary_i18n(self) -> Optional[SummaryI18n]:
        # P1.7: a stored summary is withheld once validation regresses below `ok`,
        # not just blocked from regeneration.
        if not self.website_data_publishable or not summary_is_publishable(self.public_attributes_input):
            return None
        return self.raw_summary_i18n

    @computed_field(return_type=dict[str, str])
    @property
    def resolved_name_i18n(self) -> dict[str, str]:
        return resolve_name_i18n(self.name_i18n, self.public_attributes_input)


class SchoolCreate(SchoolBase):
    locations: list[SchoolLocationBase] = []


class SchoolResponse(SchoolBase, SchoolPricingMixin):
    id: int
    created_at: datetime
    updated_at: datetime
    locations: list[SchoolLocationResponse] = []
    exam_results: list[ExamResultResponse] = []
    raw_field_sources: list[Any] = Field(
        default_factory=list,
        validation_alias="field_sources",
        exclude=True,
    )

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    @computed_field(return_type=list[FieldSourceResponse])
    @property
    def field_sources(self) -> list[FieldSourceResponse]:
        rows = [FieldSourceResponse.model_validate(row) for row in self.raw_field_sources]
        if self.website_data_publishable:
            return rows
        return [
            row
            for row in rows
            if row.source_type.value not in {"scraped_website", "official_website"}
        ]


class SchoolListResponse(SchoolPricingMixin):
    id: int
    country_code: str = "bg"
    name_i18n: dict
    school_type: str
    education_level: str
    locations: list[SchoolLocationResponse] = []

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    @computed_field(return_type=dict[str, str])
    @property
    def resolved_name_i18n(self) -> dict[str, str]:
        return resolve_name_i18n(self.name_i18n, self.public_attributes_input)

from datetime import datetime
from functools import cached_property
from typing import Any, ClassVar, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, JsonValue, computed_field, field_validator

from app.models.pricing import PriceSource
from app.schemas.field_source import FieldSourceResponse
from app.schemas.pricing import PricingResponse
from app.utils.display_gating import (
    blocked_pricing_row_ids,
    exam_result_is_publishable,
    implausible_tuition_row_ids,
    is_scraped_price,
    pricing_row_is_publishable,
    summary_is_publishable,
)
from app.utils.fee_discounts import publishable_fee_discounts
from app.utils.i18n_resolver import resolve_address_i18n, resolve_name_i18n
from app.utils.location_tags import semantic_location_tags
from app.utils.school_attributes import build_display_attributes, publishable_display_i18n
from app.utils.school_facts import SITE_ACCEPTED_STATUSES, publication_attributes, shown_school_facts
from app.utils.website_data import (
    website_data_is_publishable,
    website_data_withheld_reasons,
)


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
    # Official round cut-offs, derived from the archive by scripts/import_cutoffs.py
    # (docs/ADMISSION_CUTOFFS.md). Aggregates only; `cutoffs` stays off list responses.
    cutoffs: list[dict[str, JsonValue]] = Field(default_factory=list)
    cutoff_summary: list[dict[str, JsonValue]] = Field(default_factory=list)
    kg_sofia_ids: list[str] = Field(default_factory=list)
    admin_district: Optional[str] = None
    catchment_ids: dict[str, list[str]] = Field(default_factory=dict)

    model_config = ConfigDict(extra="ignore")


# Full round history is detail/compare-only: a school collects hundreds of rows a year.
LIST_RESPONSE_ADMISSION_EXCLUDE = frozenset({"cutoffs"})


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
        # Shown school facts ride along so their tags, languages and class size reach the
        # display and filter projections.
        website_url = getattr(self, "website_url", None) or getattr(self, "raw_website_url", None)
        return publication_attributes(self.raw_attributes, self.raw_scrape_status, website_url)

    @cached_property
    def display_projection(self) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
        # Shared by `attributes` and `attributes_i18n`: the projection is the most
        # expensive part of serializing a school, so build it once per response object.
        return build_display_attributes(
            self.public_attributes_input, getattr(self, "school_type", None)
        )

    @computed_field(return_type=SchoolDisplayAttributes)
    @property
    def attributes(self) -> SchoolDisplayAttributes:
        base, _ = self.display_projection
        return SchoolDisplayAttributes.model_validate(base)

    @computed_field(return_type=dict[str, SchoolLocalizedAttributes])
    @property
    def attributes_i18n(self) -> dict[str, SchoolLocalizedAttributes]:
        _, localized = self.display_projection
        return {
            locale: SchoolLocalizedAttributes.model_validate(values)
            for locale, values in localized.items()
        }

    # Keys of the public admission payload this response leaves out.
    admission_exclude: ClassVar[frozenset[str]] = frozenset()

    @computed_field(return_type=Optional[dict[str, JsonValue]])
    @property
    def admission_info(self) -> Optional[dict[str, JsonValue]]:
        if not isinstance(self.raw_admission_info, dict):
            return None
        raw = {key: value for key, value in self.raw_admission_info.items() if key not in self.admission_exclude}
        public = SchoolAdmissionInfo.model_validate(raw)
        payload = public.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
        return payload or None


class SchoolPricingMixin(SchoolAttributesMixin):
    """Projects the ORM pricing rows into the public payload, gated by P1.7.

    A row is withheld when it has no ``source_url``, a confidence below the shared
    floor, or an error-level validation issue on its ``pricing[{id}]`` path — so the
    API never publishes a price a parent can't trace, that we're unsure of, or that
    Stage 6 already flagged as wrong, and a scraped row while the school's website data
    is withheld. Tuition rows implausibly cheap next to the school's
    other tuition are also withheld, so a misfiled add-on never becomes the headline
    price. Extends ``SchoolAttributesMixin`` to reach the
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
        # Gate the stored values before Pydantic can coerce malformed input
        # (for example, a string confidence of "0.9") into a valid public type.
        # A scraped row is website-derived data (P1.16): withheld with the rest of it.
        website_ok = self.website_data_publishable
        publishable = [
            row
            for row in self.raw_pricing
            if pricing_row_is_publishable(row) and (website_ok or not is_scraped_price(row))
        ]
        blocked_ids |= implausible_tuition_row_ids(publishable)
        published: list[PricingResponse] = []
        for raw_row in publishable:
            row = PricingResponse.model_validate(raw_row)
            if row.id in blocked_ids:
                continue
            published.append(row)

        # Deterministic order: newest academic year first, undated rows last, then a
        # stable tiebreak. Sorted here rather than in SQL because the canonical year is
        # a Python normalization of the school's own wording.
        published.sort(
            key=lambda item: (
                item.academic_year_canonical is None,
                -int((item.academic_year_canonical or "0/0").split("/")[0]),
                item.category.value,
                item.id,
            )
        )
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
    # Any, not dict: a malformed JSON value must not fail the whole response.
    raw_geocode_meta: Any = Field(
        default=None,
        validation_alias="geocode_meta",
        exclude=True,
    )
    is_primary: bool = True

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    @computed_field(return_type=bool)
    @property
    def coordinates_approximate(self) -> bool:
        """The pin is street/area level, not the building (only this flag is published)."""
        meta = self.raw_geocode_meta if isinstance(self.raw_geocode_meta, dict) else {}
        has_pin = self.lat is not None and self.lng is not None
        return has_pin and meta.get("precision") == "approximate"

    @field_validator("address_i18n", mode="before")
    @classmethod
    def sanitize_address_i18n(cls, value: Any) -> dict[str, str]:
        """Withhold tainted locale values before either address field is serialized."""
        return publishable_display_i18n(value)

    @computed_field(return_type=list[str])
    @property
    def location_tags(self) -> list[str]:
        """Public focus tags only; provenance/coords metadata is withheld (P1.8)."""
        return semantic_location_tags(self.raw_location_tags)

    @computed_field(return_type=dict[str, str])
    @property
    def resolved_address_i18n(self) -> dict[str, str]:
        return publishable_display_i18n(resolve_address_i18n(self.address_i18n))


class SchoolLocationResponse(SchoolLocationBase):
    id: int
    school_id: int


def _publishable_exam_results(rows: Any) -> Any:
    """Drop exam rows resting on too few pupils before they are serialized."""
    if not isinstance(rows, (list, tuple)):
        return rows
    return [row for row in rows if exam_result_is_publishable(getattr(row, "pupil_count", None))]


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


class ExamResultListItem(BaseModel):
    """Exam row for list and search responses: only what the school cards read.

    The row ids and the source link stay on the detail and compare responses.
    """

    year: int
    exam_type: str
    subject: str
    metric: str
    value: float

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


class SchoolFact(BaseModel):
    topic: str
    text_i18n: dict[str, str]
    source_url: str
    # "key" facts show first; "more" behind a "show more".
    rank: Literal["key", "more"]


class BilingualName(BaseModel):
    bg: str
    en: str


class FactAdmission(BaseModel):
    grade: Optional[int] = None  # 0 is kindergarten or preschool
    grade_to: Optional[int] = None  # the last grade of a range
    types: list[str] = []  # entrance_exam, interview, trial_day, open_entry
    subjects: list[BilingualName] = []


class FactPlaces(BaseModel):
    grade: Optional[int] = None
    grade_to: Optional[int] = None
    status: str  # full, waiting_list


class FactApplications(BaseModel):
    open: Optional[str] = None  # YYYY-MM-DD
    close: Optional[str] = None


class FactLanguage(BaseModel):
    language: str
    level: Optional[str] = None
    hours_per_week: Optional[int] = None


class FactsAtAGlance(BaseModel):
    """The labels of a school's shown facts summed up (``app.utils.school_facts.at_a_glance``)."""

    admission: list[FactAdmission] = []
    places: list[FactPlaces] = []
    highest_grade: Optional[int] = None
    curricula: list[str] = []
    diploma_recognised: Optional[bool] = None
    applications: Optional[FactApplications] = None
    instruction_languages: list[str] = []
    languages: list[FactLanguage] = []
    bus_areas: list[BilingualName] = []
    special_needs_support: Optional[bool] = None


class SchoolFacts(BaseModel):
    """Facts read from the school's own pages (school-facts stage); each links its page."""

    read_at: str
    facts: list[SchoolFact]
    at_a_glance: FactsAtAGlance = Field(default_factory=FactsAtAGlance)


class FeeDiscount(BaseModel):
    """A discount the school's own pages state beside its fees (sibling, early payment)."""

    kind: str
    percent: Optional[float] = None
    amount: Optional[float] = None
    currency: Optional[str] = None
    child: Optional[int] = None
    text_i18n: dict[str, str]
    source_url: str


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

    @field_validator("exam_results", mode="before")
    @classmethod
    def withhold_thin_exam_results(cls, value: Any) -> Any:
        return _publishable_exam_results(value)

    @computed_field(return_type=Optional[SchoolFacts])
    @property
    def school_facts(self) -> Optional[SchoolFacts]:
        # ``shown_school_facts`` is the gate (withholding marker, accepted website, same
        # site). "minor" facts are not shown; they only feed the filters.
        shown = shown_school_facts(self.raw_attributes, self.raw_scrape_status, self.website_url)
        facts = [fact for fact in (shown or {}).get("facts", []) if fact["rank"] != "minor"]
        return SchoolFacts.model_validate(shown | {"facts": facts}) if facts else None

    @computed_field(return_type=list[FeeDiscount])
    @property
    def fee_discounts(self) -> list[FeeDiscount]:
        # Gated like the facts, and shown only beside published fees (``publishable_fee_discounts``).
        if "withheld_marker" in website_data_withheld_reasons(self.raw_attributes, self.raw_scrape_status):
            return []
        if str(self.raw_scrape_status or "").strip().lower() not in SITE_ACCEPTED_STATUSES:
            return []
        # Beside the published fees read from the same site: not beside manual rows while the
        # website's are withheld (no current Stage 6 report).
        if not self.website_data_publishable:
            return []
        fee_pages = [
            row.source_url for row in self.pricing if row.source is PriceSource.SCRAPED_WEBSITE and row.source_url
        ]
        return [
            FeeDiscount.model_validate(d)
            for d in publishable_fee_discounts(self.raw_attributes, self.website_url, fee_pages) or []
        ]

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


class RelatedSchoolResponse(BaseModel):
    """Public projection of a related school: only fields its own response publishes."""

    id: int
    resolved_name_i18n: dict[str, str]
    school_type: str
    education_level: str

    model_config = ConfigDict(extra="forbid")


class SamePlaceSchoolResponse(RelatedSchoolResponse):
    """A related institution in the same place (one pin, one card); computed, not stored."""

    place_id: int  # names the place: one of its pin ids, the same on every entry for it
    location_id: int  # this school's pin in the place
    other_location_id: int  # the other institution's pin there


class SchoolDetailResponse(SchoolResponse):
    """Detail endpoint only: adds the computed kindergarten ↔ school links (UF42c)."""

    continues_to: Optional[RelatedSchoolResponse] = None
    continued_from: list[RelatedSchoolResponse] = []
    same_place: list[SamePlaceSchoolResponse] = []


class SchoolListResponse(SchoolPricingMixin):
    admission_exclude: ClassVar[frozenset[str]] = LIST_RESPONSE_ADMISSION_EXCLUDE

    id: int
    country_code: str = "bg"
    name_i18n: dict
    school_type: str
    education_level: str
    locations: list[SchoolLocationResponse] = []
    exam_results: list[ExamResultListItem] = []
    # Set on the list endpoint only (``same_place_by_school``); search leaves it empty.
    same_place: list[SamePlaceSchoolResponse] = []
    # Not served on cards; the school-facts gate needs it so fact tags reach ``filter_tags``.
    raw_website_url: Optional[str] = Field(default=None, validation_alias="website_url", exclude=True)

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    @field_validator("exam_results", mode="before")
    @classmethod
    def withhold_thin_exam_results(cls, value: Any) -> Any:
        rows = _publishable_exam_results(value)
        if not isinstance(rows, list):
            return rows
        # The cards read NVO only; ДЗИ rows (a dozen subjects a year) stay on the detail
        # and compare responses.
        return [row for row in rows if getattr(row, "exam_type", None) != "dzi"]

    @computed_field(return_type=dict[str, str])
    @property
    def resolved_name_i18n(self) -> dict[str, str]:
        return resolve_name_i18n(self.name_i18n, self.public_attributes_input)

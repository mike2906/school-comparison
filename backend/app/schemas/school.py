from datetime import datetime
from typing import Any, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.schemas.pricing import PricingResponse
from app.schemas.field_source import FieldSourceResponse
from app.utils.i18n_resolver import resolve_address_i18n, resolve_name_i18n
from app.utils.school_attributes import build_display_attributes


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

    model_config = ConfigDict(extra="forbid")


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

    @computed_field(return_type=SchoolDisplayAttributes)
    @property
    def attributes(self) -> SchoolDisplayAttributes:
        base, _ = build_display_attributes(self.raw_attributes)
        return SchoolDisplayAttributes.model_validate(base)

    @computed_field(return_type=dict[str, SchoolLocalizedAttributes])
    @property
    def attributes_i18n(self) -> dict[str, SchoolLocalizedAttributes]:
        _, localized = build_display_attributes(self.raw_attributes)
        return {
            locale: SchoolLocalizedAttributes.model_validate(values)
            for locale, values in localized.items()
        }


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
    location_tags: Optional[list[str]] = None
    is_primary: bool = True

    model_config = {"from_attributes": True}

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
    summary_i18n: Optional[SummaryI18n] = None
    num_pupils: Optional[int] = None
    admission_info: Optional[dict] = None

    model_config = ConfigDict(populate_by_name=True)

    @computed_field(return_type=dict[str, str])
    @property
    def resolved_name_i18n(self) -> dict[str, str]:
        return resolve_name_i18n(self.name_i18n, self.raw_attributes)


class SchoolCreate(SchoolBase):
    locations: list[SchoolLocationBase] = []


class SchoolResponse(SchoolBase):
    id: int
    created_at: datetime
    updated_at: datetime
    locations: list[SchoolLocationResponse] = []
    pricing: list[PricingResponse] = []
    exam_results: list[ExamResultResponse] = []
    field_sources: list[FieldSourceResponse] = []

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class SchoolListResponse(SchoolAttributesMixin):
    id: int
    country_code: str = "bg"
    name_i18n: dict
    school_type: str
    education_level: str
    admission_info: Optional[dict] = None
    locations: list[SchoolLocationResponse] = []
    pricing: list[PricingResponse] = []
    exam_results: list[ExamResultResponse] = []
    field_sources: list[FieldSourceResponse] = []

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    @computed_field(return_type=dict[str, str])
    @property
    def resolved_name_i18n(self) -> dict[str, str]:
        return resolve_name_i18n(self.name_i18n, self.raw_attributes)

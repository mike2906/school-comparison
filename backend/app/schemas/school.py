from datetime import datetime
from typing import Optional, Union

from pydantic import BaseModel, ConfigDict

from app.schemas.pricing import PricingResponse
from app.schemas.field_source import FieldSourceResponse


class SummaryText(BaseModel):
    short: str
    long: str


SummaryI18n = dict[str, SummaryText]


class LanguageFocusItem(BaseModel):
    language: Optional[str] = None
    level: Optional[str] = None


class SchoolAttributes(BaseModel):
    languages_of_instruction: Optional[list[str]] = None
    has_canteen: Optional[bool] = None
    activities_offered: Optional[list[str]] = None
    language_focus: Optional[list[Union[LanguageFocusItem, str]]] = None
    special_programs: Optional[list[str]] = None
    facilities: Optional[list[str]] = None
    teaching_approach: Optional[list[str]] = None
    class_size: Optional[int] = None
    uniform_required: Optional[bool] = None
    special_focus: Optional[str] = None

    model_config = ConfigDict(extra="allow")


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


class SchoolBase(BaseModel):
    country_code: str = "bg"
    name_i18n: dict
    school_type: str
    education_level: str
    source_url: Optional[str] = None
    website_url: Optional[str] = None
    summary_i18n: Optional[SummaryI18n] = None
    num_pupils: Optional[int] = None
    admission_info: Optional[dict] = None
    attributes: Optional[SchoolAttributes] = None


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

    model_config = {"from_attributes": True}


class SchoolListResponse(BaseModel):
    id: int
    country_code: str = "bg"
    name_i18n: dict
    school_type: str
    education_level: str
    admission_info: Optional[dict] = None
    attributes: Optional[dict] = None
    locations: list[SchoolLocationResponse] = []
    pricing: list[PricingResponse] = []
    exam_results: list[ExamResultResponse] = []
    field_sources: list[FieldSourceResponse] = []

    model_config = {"from_attributes": True}

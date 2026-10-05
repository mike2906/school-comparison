from datetime import datetime

from pydantic import BaseModel


class CountryConfigResponse(BaseModel):
    code: str
    name_i18n: dict
    education_config: dict
    map_config: dict
    supported_languages: list[str]
    default_language: str
    default_currency: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CountryListResponse(BaseModel):
    code: str
    name_i18n: dict
    supported_languages: list[str]
    default_language: str
    default_currency: str

    model_config = {"from_attributes": True}

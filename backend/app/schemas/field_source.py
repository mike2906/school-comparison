from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict

from app.models.field_source import SourceType, SourceConfidence


class FieldSourceBase(BaseModel):
    """Parent-facing provenance only; raw extracted values remain internal."""

    category: Optional[str] = None
    value_text: Optional[str] = None
    source_type: SourceType
    source_url: Optional[str] = None
    display_url: Optional[str] = None
    scraped_at: Optional[datetime] = None
    last_verified: Optional[datetime] = None
    confidence: Optional[SourceConfidence] = None

    model_config = ConfigDict(from_attributes=True, extra="ignore")


class FieldSourceResponse(FieldSourceBase):
    id: int

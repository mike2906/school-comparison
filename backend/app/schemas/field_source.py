from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict

from app.models.field_source import SourceConfidence, SourceType


class FieldSourceBase(BaseModel):
    """Metadata-only parent-facing provenance; extracted values remain internal."""

    source_type: SourceType
    source_url: Optional[str] = None
    last_verified: Optional[datetime] = None
    confidence: Optional[SourceConfidence] = None

    model_config = ConfigDict(from_attributes=True, extra="ignore")


class FieldSourceResponse(FieldSourceBase):
    pass

from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from app.models.field_source import SourceType, SourceConfidence


class FieldSourceBase(BaseModel):
    school_id: int
    category: Optional[str] = None
    field_key: str
    field_path: Optional[str] = None
    value_text: Optional[str] = None
    value_json: Optional[dict] = None
    source_type: SourceType
    source_name: Optional[str] = None
    source_url: Optional[str] = None
    display_url: Optional[str] = None
    scraped_at: Optional[datetime] = None
    last_verified: Optional[datetime] = None
    confidence: Optional[SourceConfidence] = None
    confidence_score: Optional[float] = None
    submitted_by: Optional[str] = None
    verified: Optional[bool] = None
    notes: Optional[str] = None


class FieldSourceResponse(FieldSourceBase):
    id: int
    created_at: datetime

    model_config = {"from_attributes": True}

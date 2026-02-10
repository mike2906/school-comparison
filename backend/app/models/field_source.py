import enum
from datetime import datetime
from typing import Optional

from sqlalchemy import String, Text, Enum, Integer, DateTime, ForeignKey, JSON, Boolean, Float
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class SourceType(str, enum.Enum):
    OFFICIAL_WEBSITE = "official_website"
    GOVERNMENT = "government"
    COMMUNITY_FORUM = "community_forum"
    PARENT_SUBMITTED = "parent_submitted"
    SCHOOL_CONTACT = "school_contact"
    SCRAPED_WEBSITE = "scraped_website"
    UNKNOWN = "unknown"


class SourceConfidence(str, enum.Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    CONFLICTING = "conflicting"


class FieldSource(Base):
    __tablename__ = "field_sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    school_id: Mapped[int] = mapped_column(ForeignKey("schools.id"), nullable=False)
    category: Mapped[Optional[str]] = mapped_column(String(100))
    field_key: Mapped[str] = mapped_column(String(200), nullable=False)
    field_path: Mapped[Optional[str]] = mapped_column(String(500))
    value_text: Mapped[Optional[str]] = mapped_column(Text)
    value_json: Mapped[Optional[dict]] = mapped_column(JSON)
    source_type: Mapped[SourceType] = mapped_column(Enum(SourceType), nullable=False)
    source_name: Mapped[Optional[str]] = mapped_column(String(200))
    source_url: Mapped[Optional[str]] = mapped_column(String(1000))
    display_url: Mapped[Optional[str]] = mapped_column(String(300))
    scraped_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_verified: Mapped[Optional[datetime]] = mapped_column(DateTime)
    confidence: Mapped[Optional[SourceConfidence]] = mapped_column(Enum(SourceConfidence))
    confidence_score: Mapped[Optional[float]] = mapped_column(Float)
    submitted_by: Mapped[Optional[str]] = mapped_column(String(200))
    verified: Mapped[Optional[bool]] = mapped_column(Boolean)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    school: Mapped["School"] = relationship("School", back_populates="field_sources")


# Import for relationship type hints
from app.models.school import School

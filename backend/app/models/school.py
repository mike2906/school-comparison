from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import String, Text, Integer, Boolean, DateTime, JSON, Float, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class School(Base):
    __tablename__ = "schools"

    id: Mapped[int] = mapped_column(primary_key=True)
    country_code: Mapped[str] = mapped_column(String(2), ForeignKey("countries.code"), nullable=False, default="bg")
    name_i18n: Mapped[dict] = mapped_column(JSON, nullable=False)
    school_type: Mapped[str] = mapped_column(String(50), nullable=False)
    education_level: Mapped[str] = mapped_column(String(50), nullable=False)
    source_url: Mapped[Optional[str]] = mapped_column(String(1000))
    website_url: Mapped[Optional[str]] = mapped_column(String(1000))
    summary_i18n: Mapped[Optional[dict]] = mapped_column(JSON, default=dict)
    num_pupils: Mapped[Optional[int]] = mapped_column(Integer)
    admission_info: Mapped[Optional[dict]] = mapped_column(JSON, default=dict)
    attributes: Mapped[Optional[dict]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )

    country: Mapped["Country"] = relationship("Country")
    locations: Mapped[list["SchoolLocation"]] = relationship(
        "SchoolLocation", back_populates="school", cascade="all, delete-orphan"
    )
    pricing: Mapped[list["Pricing"]] = relationship(
        "Pricing", back_populates="school", cascade="all, delete-orphan"
    )
    exam_results: Mapped[list["ExamResult"]] = relationship(
        "ExamResult", back_populates="school", cascade="all, delete-orphan"
    )
    field_sources: Mapped[list["FieldSource"]] = relationship(
        "FieldSource", back_populates="school", cascade="all, delete-orphan"
    )
    source_pages: Mapped[list["SourcePage"]] = relationship(
        "SourcePage", back_populates="school", cascade="all, delete-orphan"
    )


class SchoolLocation(Base):
    __tablename__ = "school_locations"

    id: Mapped[int] = mapped_column(primary_key=True)
    school_id: Mapped[int] = mapped_column(ForeignKey("schools.id"), nullable=False)
    address_i18n: Mapped[dict] = mapped_column(JSON, nullable=False)
    lat: Mapped[Optional[float]] = mapped_column(Float)
    lng: Mapped[Optional[float]] = mapped_column(Float)
    phone: Mapped[Optional[str]] = mapped_column(String(100))
    location_tags: Mapped[Optional[list[str]]] = mapped_column(JSON, default=list)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=True)

    school: Mapped["School"] = relationship("School", back_populates="locations")
    age_group_shifts: Mapped[list["SchoolLocationAgeGroupShift"]] = relationship(
        "SchoolLocationAgeGroupShift", back_populates="location", cascade="all, delete-orphan"
    )

    @property
    def age_groups(self) -> list[str]:
        return [link.age_group for link in self.age_group_shifts]


class SchoolLocationAgeGroupShift(Base):
    __tablename__ = "location_age_group_shifts"

    location_id: Mapped[int] = mapped_column(ForeignKey("school_locations.id"), primary_key=True)
    age_group: Mapped[str] = mapped_column(String(50), primary_key=True)
    shift: Mapped[Optional[str]] = mapped_column(String(50))
    has_organised_groups: Mapped[Optional[bool]] = mapped_column(Boolean)

    location: Mapped["SchoolLocation"] = relationship("SchoolLocation", back_populates="age_group_shifts")


# Import for relationship type hints
from app.models.country import Country
from app.models.pricing import Pricing
from app.models.exam_results import ExamResult
from app.models.field_source import FieldSource
from app.models.source_page import SourcePage

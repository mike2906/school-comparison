import enum
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class DiscrepancyType(str, enum.Enum):
    NUMERIC_DIFF = "numeric_diff"  # Numbers differ beyond threshold
    VALUE_MISMATCH = "value_mismatch"  # Strings/enums don't match
    MISSING_FIELD = "missing_field"  # Cheap model missed a field
    EXTRA_FIELD = "extra_field"  # Cheap model hallucinated a field


class SpotCheckResult(Base):
    """Stores spot-check comparisons between cheap and capable models."""

    __tablename__ = "spot_check_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("pipeline_runs.id"), nullable=False)
    school_id: Mapped[int] = mapped_column(Integer, ForeignKey("schools.id"), nullable=False)

    extractor_name: Mapped[str] = mapped_column(String(100), nullable=False)  # e.g., "PriceExtractor"
    field_name: Mapped[str] = mapped_column(String(100), nullable=False)  # e.g., "tuition_amount"

    cheap_value: Mapped[Optional[str]] = mapped_column(Text)  # JSON-serialized value from cheap model
    capable_value: Mapped[Optional[str]] = mapped_column(Text)  # JSON-serialized value from capable model

    discrepancy_type: Mapped[DiscrepancyType] = mapped_column(Enum(DiscrepancyType), nullable=False)
    discrepancy_magnitude: Mapped[float] = mapped_column(Float, nullable=False)  # 0.0-1.0

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    # Relationships
    school: Mapped["School"] = relationship("School")


# Import for relationship type hints
from app.models.school import School
